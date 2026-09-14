"""Where was the correct answer? Reachability and rank of the target entity.

This is the zero-model audit that decides whether the data layer still has
headroom. It reads only mechanical facts:

- each subtask's environment marks exactly one target entity and, usually, one
  target product (`*_type == "target"`); these ids are the ground truth,
- the agent's search results, replayed from the saved trajectory,
- the ids the agent actually bound in a write call,
- the subtask's binary reward.

It then splits every subtask into the cells that matter:

    target never returned        -> the search never surfaced the answer
    target returned at rank R    -> the answer was in hand, at position R
    agent bound the target       -> the answer was used
    subtask passed               -> the rubric was satisfied

The decisive cell is `returned but not bound`: the information was in front of
the model and it chose otherwise. That population cannot be fixed from the data
layer, because nothing about the data was missing. The complementary cell,
`never returned`, is where a data layer that knows the user's constraints could
change the query or the ranking.

Target/distraction annotations and rubric text are read here for offline
attribution only; the agent never sees them, and the written report keeps to
counts and ranks rather than reproducing any annotation value.

Usage:
    python scripts/target_reachability.py data/simulations/stock_dev.json
    python scripts/target_reachability.py stock.json --json
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import agent.vitabench_bootstrap as bootstrap  # noqa: E402

bootstrap.enable_vitabench_utf8()

ENTITY_KEYS = ("stores", "shops", "hotels", "flights", "trains", "attractions")
PRODUCT_KEYS = ("products", "rooms")

WRITE_PREFIXES = ("create_",)
WRITE_EXACT = {"instore_book", "instore_reservation"}


def is_write_tool(name: str) -> bool:
    return name.startswith(WRITE_PREFIXES) or name in WRITE_EXACT


def target_ids(environment: dict) -> tuple[set[str], set[str], set[str]]:
    """(entity ids, product ids, host entity ids of target products).

    The third set matters because the markers are not uniform: a delivery
    subtask marks the *store* as target, while an ota subtask marks only a
    *room* inside a hotel. Deriving reachability of the host hotel from a
    separate marker would leave that set empty, and any "was the host listed"
    test would then be a control that can never be false.
    """
    entities: set[str] = set()
    products: set[str] = set()
    hosts: set[str] = set()
    for key in ENTITY_KEYS:
        container = environment.get(key)
        if isinstance(container, dict):
            items = container.items()
        elif isinstance(container, list):
            items = ((None, item) for item in container)
        else:
            continue
        for eid, entity in items:
            if not isinstance(entity, dict):
                continue
            self_ids = {
                str(entity[field])
                for field in ("store_id", "shop_id", "hotel_id", "train_id", "flight_id", "id")
                if entity.get(field)
            }
            if eid:
                self_ids.add(str(eid))
            if entity.get("shop_type") == "target" or entity.get("store_type") == "target":
                entities |= self_ids
            for pkey in PRODUCT_KEYS:
                for product in entity.get(pkey) or []:
                    if isinstance(product, dict) and product.get("product_type") == "target":
                        for id_field in ("product_id", "room_id", "id"):
                            if product.get(id_field):
                                products.add(str(product[id_field]))
                        hosts |= self_ids
    return entities, products, hosts


def result_records(content: Any) -> list[str]:
    """Split a tool result into one record per returned candidate."""
    if not isinstance(content, str):
        return []
    return [line for line in content.split("\n") if line.strip()]


def rank_of(needle: str, records: list[str]) -> int | None:
    """1-based position of the first record containing `needle`."""
    for index, record in enumerate(records, start=1):
        if needle in record:
            return index
    return None


def bound_ids(messages: list[dict]) -> set[str]:
    """Every id-like argument value the agent passed to a write tool."""
    found: set[str] = set()
    for message in messages:
        for call in message.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            if not is_write_tool(str(call.get("name") or "")):
                continue
            arguments = call.get("arguments") or {}
            if not isinstance(arguments, dict):
                continue

            def walk(value: Any) -> None:
                if isinstance(value, str):
                    found.add(value)
                elif isinstance(value, list):
                    for item in value:
                        walk(item)
                elif isinstance(value, dict):
                    for item in value.values():
                        walk(item)

            walk(arguments)
    return found


def target_keys(environment: dict) -> set[str]:
    """Which environment containers hold the target, and of what kind."""
    keys: set[str] = set()
    for key in ENTITY_KEYS:
        container = environment.get(key)
        if isinstance(container, dict):
            items = container.values()
        elif isinstance(container, list):
            items = container
        else:
            continue
        for entity in items:
            if not isinstance(entity, dict):
                continue
            if entity.get("shop_type") == "target" or entity.get("store_type") == "target":
                keys.add(key)
            for pkey in PRODUCT_KEYS:
                for product in entity.get(pkey) or []:
                    if isinstance(product, dict) and product.get("product_type") == "target":
                        keys.add(pkey)
    return keys


def tool_names(messages: list[dict]) -> list[str]:
    names = []
    for message in messages:
        for call in message.get("tool_calls") or []:
            if isinstance(call, dict) and call.get("name"):
                names.append(str(call["name"]))
    return names


def analyse(
    checkpoint: dict, tasks_by_id: dict[str, Any], rank_cutoff: int
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for sim in checkpoint.get("simulations", []):
        task = tasks_by_id.get(str(sim.get("task_id")))
        if task is None:
            continue
        subtasks = list(task.subtasks)
        rewards = ((sim.get("reward_info") or {}).get("info") or {}).get(
            "subtask_rewards"
        ) or {}
        trajectories = (sim.get("states") or {}).get("integrity_subtask_trajectories") or []
        for traj in trajectories:
            index = traj.get("subtask_idx")
            if index is None or index >= len(subtasks):
                continue
            reward = rewards.get(f"subtask_{index}_reward")
            if reward is None:
                continue
            environment = subtasks[index].environment or {}
            entities, products, hosts = target_ids(environment)
            messages = traj.get("messages") or []

            best_rank: int | None = None
            best_on: str | None = None
            returned = False
            returned_entity = False
            returned_product = False
            returned_host = False
            for message in messages:
                if message.get("role") != "tool":
                    continue
                records = result_records(message.get("content"))
                if not records:
                    continue
                for label, ids in (("entity", entities), ("product", products)):
                    for target in ids:
                        rank = rank_of(target, records)
                        if rank is None:
                            continue
                        returned = True
                        if label == "entity":
                            returned_entity = True
                        else:
                            returned_product = True
                        if best_rank is None or rank < best_rank:
                            best_rank, best_on = rank, label
                for host in hosts:
                    if rank_of(host, records) is not None:
                        returned_host = True

            bound = bound_ids(messages)
            bound_entity = bool(entities & bound)
            bound_product = bool(products & bound)

            rows.append(
                {
                    "task_id": sim.get("task_id"),
                    "trial": sim.get("trial"),
                    "subtask_idx": index,
                    "subtask_id": traj.get("subtask_id"),
                    "domain": subtasks[index].domain,
                    "reward": float(reward),
                    "has_target": bool(entities or products),
                    "returned": returned,
                    "returned_entity": returned_entity,
                    "returned_product": returned_product,
                    "returned_host": returned_host,
                    "best_rank": best_rank,
                    "best_on": best_on,
                    "bound_entity": bound_entity,
                    "bound_product": bound_product,
                    "messages": len(messages),
                    "target_keys": sorted(target_keys(environment)),
                    "tools": tool_names(messages),
                }
            )
    return summarise(rows, rank_cutoff)


def summarise(rows: list[dict[str, Any]], rank_cutoff: int) -> dict[str, Any]:
    graded = [row for row in rows if row["has_target"]]
    failing = [row for row in graded if row["reward"] == 0.0]

    def rate(group: list[dict[str, Any]]) -> float:
        if not group:
            return 0.0
        return round(sum(row["reward"] for row in group) / len(group), 4)

    def bucket(row: dict[str, Any]) -> str:
        if not row["returned"]:
            return "target_never_returned"
        if row["best_rank"] is not None and row["best_rank"] <= rank_cutoff:
            return "target_returned_near"
        return "target_returned_buried"

    buckets: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for row in graded:
        buckets[bucket(row)].append(row)

    # The decisive split over failing subtasks only.
    fail_split: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for row in failing:
        bound = row["bound_entity"] or row["bound_product"]
        if not row["returned"]:
            key = "never_returned"
        elif bound:
            key = "returned_and_bound_something_target_like"
        elif row["best_rank"] is not None and row["best_rank"] <= rank_cutoff:
            key = "returned_near_not_bound"
        else:
            key = "returned_buried_not_bound"
        fail_split[key].append(row)

    ranks = sorted(row["best_rank"] for row in graded if row["best_rank"])

    def percentile(values: list[int], fraction: float) -> int | None:
        if not values:
            return None
        position = min(len(values) - 1, int(len(values) * fraction))
        return values[position]

    out: dict[str, Any] = {
        "graded_subtasks": len(graded),
        "overall_pass_rate": rate(graded),
        "rank_cutoff": rank_cutoff,
        "reachability_by_bucket": {
            key: {
                "n": len(group),
                "pass_rate": rate(group),
                "share": round(len(group) / len(graded), 4) if graded else 0.0,
            }
            for key, group in sorted(buckets.items())
        },
        "target_rank_median": percentile(ranks, 0.5),
        "target_rank_p25": percentile(ranks, 0.25),
        "target_rank_p75": percentile(ranks, 0.75),
        "target_rank_max": ranks[-1] if ranks else None,
        "failing_subtasks": len(failing),
        "failing_split": {
            key: {
                "n": len(group),
                "share_of_failures": round(len(group) / len(failing), 4) if failing else 0.0,
            }
            for key, group in sorted(fail_split.items())
        },
        "by_domain": {
            domain: {
                "n": len(group),
                "pass_rate": rate(group),
                "never_returned": sum(1 for r in group if not r["returned"]),
            }
            for domain, group in sorted(
                _group_by(graded, "domain").items()
            )
        },
        "never_returned_profile": _never_returned_profile(graded),
        "chain_by_domain": _chain_profile(graded),
        "conditioned_on_product_printed": _conditioned(graded),
        "detail": rows,
    }
    return out


def _conditioned(graded: list[dict[str, Any]]) -> dict[str, Any]:
    """Pass rate conditioned on whether the target product was ever printed.

    This is the sharpest mechanical split available: it asks whether a subtask
    can pass at all when the answer never appeared in front of the model.
    """
    printed = [row for row in graded if row["returned_product"]]
    absent = [row for row in graded if not row["returned_product"]]

    def rate(group: list[dict[str, Any]]) -> float:
        if not group:
            return 0.0
        return round(sum(row["reward"] for row in group) / len(group), 4)

    return {
        "product_printed_n": len(printed),
        "product_printed_pass_rate": rate(printed),
        "product_absent_n": len(absent),
        "product_absent_pass_rate": rate(absent),
        "product_absent_passes": sum(1 for row in absent if row["reward"] == 1.0),
        "total_passes": sum(1 for row in graded if row["reward"] == 1.0),
    }


def _chain_profile(graded: list[dict[str, Any]]) -> dict[str, Any]:
    """For subtasks whose target sits on a product, where does the chain break?

    The target is often a *product* (a room, a ticket type) inside an *entity*
    (a hotel, a train). Reachability is therefore a chain:

        entity never listed      -> the search missed the container entirely
        entity listed, no product-> the agent never drilled into that container
        product printed          -> the answer was in hand
    """
    relevant = [row for row in graded if row["target_keys"] and _product_only(row)]
    out: dict[str, Any] = {}
    for domain, group in _group_by(relevant, "domain").items():
        out[domain] = {
            "n": len(group),
            "pass_rate": round(
                sum(row["reward"] for row in group) / len(group), 4
            ),
            "host_never_listed": sum(1 for row in group if not row["returned_host"]),
            "host_listed_product_absent": sum(
                1 for row in group if row["returned_host"] and not row["returned_product"]
            ),
            "product_printed": sum(1 for row in group if row["returned_product"]),
        }
    return out


def _product_only(row: dict[str, Any]) -> bool:
    return "products" in row["target_keys"] or "rooms" in row["target_keys"]


def _never_returned_profile(graded: list[dict[str, Any]]) -> dict[str, Any]:
    """What the target was, and what the agent searched for instead."""
    missing = [row for row in graded if not row["returned"]]
    by_domain: dict[str, Any] = {}
    for domain, group in _group_by(missing, "domain").items():
        key_counts: collections.Counter[str] = collections.Counter(
            key for row in group for key in row["target_keys"]
        )
        tool_counts: collections.Counter[str] = collections.Counter(
            name for row in group for name in set(row["tools"])
        )
        by_domain[domain] = {
            "n": len(group),
            "target_container": dict(key_counts.most_common()),
            "tools_used": dict(tool_counts.most_common(6)),
        }
    return {"total": len(missing), "by_domain": by_domain}


def _group_by(rows: list[dict[str, Any]], key: str) -> dict[Any, list[dict[str, Any]]]:
    out: dict[Any, list[dict[str, Any]]] = collections.defaultdict(list)
    for row in rows:
        out[row[key]].append(row)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoint")
    parser.add_argument("--rank-cutoff", type=int, default=10)
    parser.add_argument("--language", default="chinese")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    with open(args.checkpoint, encoding="utf-8") as handle:
        checkpoint = json.load(handle)

    from agent.vitabench_runner import get_tasks

    tasks_by_id = {task.id: task for task in get_tasks(args.language)}
    report = analyse(checkpoint, tasks_by_id, args.rank_cutoff)

    if args.json:
        report.pop("detail", None)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    detail = report.pop("detail")
    print(f"checkpoint: {args.checkpoint}")
    for key, value in report.items():
        print(f"  {key}:")
        if isinstance(value, dict):
            for name, item in value.items():
                print(f"      {name}: {item}")
        else:
            print(f"      {value}")
    print()
    print(f"rows: {len(detail)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

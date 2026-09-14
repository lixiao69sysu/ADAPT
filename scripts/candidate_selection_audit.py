"""Zero-model audit: was a failing subtask's target reachable, and if so, how?

This is the second-stage device after ``target_reachability.py``. It takes the
cells that device produces and splits them by *mechanism*, using only ids and
tool-call arguments -- no model call, no rubric text:

Cell A  "host listed, product absent"
    The parent entity was printed but the target product never was. The
    question is whether the agent failed to expand any parent, expanded the
    wrong parent, or expanded the right parent and did not find the target.

Cell B  "product printed but subtask failed"
    The answer was in front of the model. The question is whether it bound the
    target id, bound a different observed id, or bound an id that was never
    printed at all (E-035: a write using an id the model supplied from memory).

Target/distraction annotations and rubric text are read for offline attribution
only; the agent never sees them, and this report keeps to counts and ranks.

Usage:
    python scripts/candidate_selection_audit.py data/simulations/stock_dev.json
    python scripts/candidate_selection_audit.py stock.json --json
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import agent.vitabench_bootstrap as bootstrap  # noqa: E402

bootstrap.enable_vitabench_utf8()

from scripts.target_reachability import (  # noqa: E402
    bound_ids,
    is_write_tool,
    rank_of,
    result_records,
    target_ids,
)


# An entity/product identifier as the tools print it: a leading letter run, then
# an underscore-joined suffix (S17791000995167386_P00015, H12_R3). Dates,
# addresses and user ids deliberately do not match, so a write argument that is
# a timestamp is never mistaken for an id the model supplied from memory.
_ID_LIKE = re.compile(r"^[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+$")


def observed_ids(messages: list[dict]) -> set[str]:
    """Every id-like token that was actually printed by a tool result."""
    observed: set[str] = set()
    for message in messages:
        if message.get("role") != "tool":
            continue
        for record in result_records(message.get("content")):
            for field in ("store_id=", "product_id=", "shop_id=", "hotel_id=",
                          "room_id=", "train_id=", "flight_id="):
                start = 0
                while True:
                    index = record.find(field, start)
                    if index < 0:
                        break
                    start = index + len(field)
                    tail = record[start:]
                    stop = len(tail)
                    for separator in (",", ")", " ", "\n"):
                        position = tail.find(separator)
                        if position >= 0:
                            stop = min(stop, position)
                    token = tail[:stop].strip()
                    if token:
                        observed.add(token)
    return observed


def call_arguments(call: dict) -> list[str]:
    """Flatten one tool call's argument values into a list of strings."""
    out: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str):
            out.append(value)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)

    walk(call.get("arguments") or {})
    return out


_ENTITY_KEYS = ("stores", "shops", "hotels", "flights", "trains", "attractions")


def all_entity_ids(environment: dict) -> set[str]:
    """Every parent-entity id in the subtask environment.

    ``target_ids`` returns only the marked target entity, which is enough to ask
    "was the target expanded" but not "was some other parent expanded instead".
    The latter needs the full entity set, and it must exclude products, so this
    walks only the entity containers.
    """
    ids: set[str] = set()
    for key in _ENTITY_KEYS:
        container = environment.get(key)
        if isinstance(container, dict):
            items: Any = container.items()
        elif isinstance(container, list):
            items = ((None, item) for item in container)
        else:
            continue
        for eid, entity in items:
            if not isinstance(entity, dict):
                continue
            if eid:
                ids.add(str(eid))
            for field in ("store_id", "shop_id", "hotel_id", "train_id",
                          "flight_id", "attraction_id", "id"):
                if entity.get(field):
                    ids.add(str(entity[field]))
    return ids


def analyse(checkpoint: dict, tasks_by_id: dict[str, Any]) -> dict[str, Any]:
    cell_a: list[dict[str, Any]] = []
    cell_b: list[dict[str, Any]] = []

    for sim in checkpoint.get("simulations", []):
        task = tasks_by_id.get(str(sim.get("task_id")))
        if task is None:
            continue
        subtasks = list(task.subtasks)
        rewards = ((sim.get("reward_info") or {}).get("info") or {}).get(
            "subtask_rewards"
        ) or {}
        trajectories = (sim.get("states") or {}).get(
            "integrity_subtask_trajectories"
        ) or []
        for traj in trajectories:
            index = traj.get("subtask_idx")
            if index is None or index >= len(subtasks):
                continue
            reward = rewards.get(f"subtask_{index}_reward")
            if reward is None:
                continue
            environment = subtasks[index].environment or {}
            entities, products, hosts = target_ids(environment)
            if not products:
                continue
            messages = traj.get("messages") or []

            product_rank: int | None = None
            host_rank: int | None = None
            for message in messages:
                if message.get("role") != "tool":
                    continue
                records = result_records(message.get("content"))
                for target in products:
                    rank = rank_of(target, records)
                    if rank is not None and (product_rank is None or rank < product_rank):
                        product_rank = rank
                for host in hosts:
                    rank = rank_of(host, records)
                    if rank is not None and (host_rank is None or rank < host_rank):
                        host_rank = rank

            expand_calls: list[set[str]] = []
            observed = observed_ids(messages)
            all_entities = all_entity_ids(environment)
            for message in messages:
                for call in message.get("tool_calls") or []:
                    if not isinstance(call, dict):
                        continue
                    name = str(call.get("name") or "")
                    values = set(call_arguments(call))
                    if is_write_tool(name):
                        continue
                    # A drill-down is any non-write call that references an id
                    # the tools actually printed. Detecting it by the referenced
                    # id (rather than by a "get_*" name prefix) keeps the measure
                    # robust to tool naming, which matters because the whole
                    # claim rests on how often the target parent was expanded.
                    if {value for value in values if _ID_LIKE.match(value)} & observed:
                        expand_calls.append(values)

            base = {
                "task_id": sim.get("task_id"),
                "trial": sim.get("trial"),
                "subtask_idx": index,
                "domain": subtasks[index].domain,
                "reward": float(reward),
            }

            if product_rank is not None:
                if float(reward) == 0.0:
                    bound = bound_ids(messages)
                    cell_b.append(
                        {
                            **base,
                            "target_rank": product_rank,
                            "bound_target": bool(products & bound),
                            "bound_observed_other": bool(
                                (bound - products) & observed
                            ),
                            "bound_unobserved": sorted(
                                value
                                for value in bound - products
                                if value not in observed and _ID_LIKE.match(value)
                            ),
                        }
                    )
                continue

            if host_rank is None:
                continue

            expanded_target = any(
                bool({v for v in values if _ID_LIKE.match(v)} & hosts)
                for values in expand_calls
            )
            # A different *parent* was drilled into instead. Restricted to entity
            # ids that exist in the subtask environment, so a product id or an
            # invented id can never be counted as "expanded the wrong parent".
            expanded_other = any(
                bool(
                    {v for v in values if _ID_LIKE.match(v)}
                    & (all_entities - hosts)
                    & observed
                )
                for values in expand_calls
            )
            cell_a.append(
                {
                    **base,
                    "host_rank": host_rank,
                    "expand_calls": len(expand_calls),
                    "expanded_target_host": expanded_target,
                    "expanded_other_host": expanded_other,
                }
            )

    def rate(group: list[dict[str, Any]]) -> float:
        if not group:
            return 0.0
        return round(sum(row["reward"] for row in group) / len(group), 4)

    def histogram(values: list[int]) -> dict[str, int]:
        buckets = collections.Counter()
        for value in values:
            if value <= 1:
                buckets["rank_1"] += 1
            elif value <= 3:
                buckets["rank_2_3"] += 1
            elif value <= 10:
                buckets["rank_4_10"] += 1
            else:
                buckets["rank_11_plus"] += 1
        return dict(sorted(buckets.items()))

    a_mechanism = collections.Counter()
    for row in cell_a:
        if row["expand_calls"] == 0:
            a_mechanism["never_expanded_any_parent"] += 1
        elif row["expanded_target_host"]:
            a_mechanism["expanded_target_parent_no_product"] += 1
        elif row["expanded_other_host"]:
            a_mechanism["expanded_a_different_parent"] += 1
        else:
            a_mechanism["expanded_unmatched_parent"] += 1

    b_mechanism = collections.Counter()
    for row in cell_b:
        if row["bound_target"]:
            b_mechanism["bound_target_but_failed_rubric"] += 1
        elif row["bound_observed_other"]:
            b_mechanism["bound_a_different_observed_id"] += 1
        elif row["bound_unobserved"]:
            b_mechanism["bound_an_unobserved_id"] += 1
        else:
            b_mechanism["no_id_bound"] += 1

    def rank_bucket(value: int) -> str:
        if value <= 1:
            return "rank_1"
        if value <= 3:
            return "rank_2_3"
        if value <= 10:
            return "rank_4_10"
        return "rank_11_plus"

    def a_label(row: dict[str, Any]) -> str:
        if row["expand_calls"] == 0:
            return "never_expanded_any_parent"
        if row["expanded_target_host"]:
            return "expanded_target_parent_no_product"
        if row["expanded_other_host"]:
            return "expanded_a_different_parent"
        return "expanded_unmatched_parent"

    def b_label(row: dict[str, Any]) -> str:
        if row["bound_target"]:
            return "bound_target_but_failed_rubric"
        if row["bound_observed_other"]:
            return "bound_a_different_observed_id"
        if row["bound_unobserved"]:
            return "bound_an_unobserved_id"
        return "no_id_bound"

    a_crosstab: dict[str, collections.Counter] = collections.defaultdict(
        collections.Counter
    )
    for row in cell_a:
        a_crosstab[rank_bucket(row["host_rank"])][a_label(row)] += 1

    b_crosstab: dict[str, collections.Counter] = collections.defaultdict(
        collections.Counter
    )
    for row in cell_b:
        b_crosstab[rank_bucket(row["target_rank"])][b_label(row)] += 1

    # The sharply actionable population: the correct parent was in the top three
    # of a search result the model had already seen, and the model still drilled
    # into a different one. Nothing about the data layer was missing there.
    a_sharp = sum(
        1
        for row in cell_a
        if rank_bucket(row["host_rank"]) in ("rank_1", "rank_2_3")
        and a_label(row) == "expanded_a_different_parent"
    )
    b_sharp = sum(
        1
        for row in cell_b
        if rank_bucket(row["target_rank"]) in ("rank_1", "rank_2_3")
        and b_label(row) == "bound_a_different_observed_id"
    )

    return {
        "note": (
            "Only subtasks whose target is a product are counted, so the cell "
            "sizes here are smaller than the failing populations in "
            "target_reachability.py. In cell A the target parent is almost "
            "never expanded (0 in this sample) because expanding it is what "
            "makes the target product print, which moves the row into cell B; "
            "that share is therefore close to tautological and must not be "
            "quoted as an independent finding."
        ),
        "cell_a_host_listed_product_absent": {
            "n": len(cell_a),
            "pass_rate": rate(cell_a),
            "domains": dict(collections.Counter(r["domain"] for r in cell_a)),
            "target_host_rank": histogram(
                [r["host_rank"] for r in cell_a if r["host_rank"]]
            ),
            "mechanism": dict(a_mechanism.most_common()),
            "rank_x_mechanism": {
                key: dict(value) for key, value in sorted(a_crosstab.items())
            },
            "top3_host_but_different_parent": a_sharp,
        },
        "cell_b_product_printed_but_failed": {
            "n": len(cell_b),
            "domains": dict(collections.Counter(r["domain"] for r in cell_b)),
            "target_rank": histogram([r["target_rank"] for r in cell_b]),
            "mechanism": dict(b_mechanism.most_common()),
            "rank_x_mechanism": {
                key: dict(value) for key, value in sorted(b_crosstab.items())
            },
            "top3_product_but_different_id": b_sharp,
        },
        "detail": {"cell_a": cell_a, "cell_b": cell_b},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoint")
    parser.add_argument("--language", default="chinese")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    with open(args.checkpoint, encoding="utf-8") as handle:
        checkpoint = json.load(handle)

    from agent.vitabench_runner import get_tasks

    tasks_by_id = {task.id: task for task in get_tasks(args.language)}
    report = analyse(checkpoint, tasks_by_id)

    if args.json:
        report.pop("detail", None)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    report.pop("detail")
    print(f"checkpoint: {args.checkpoint}")
    for section, payload in report.items():
        if isinstance(payload, str):
            print(f"\n{section}: {payload}")
            continue
        print(f"\n{section}:")
        for key, value in payload.items():
            print(f"    {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

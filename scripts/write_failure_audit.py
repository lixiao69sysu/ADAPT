"""Zero-model diagnosis: what do the "wrote but still scored 0" units lose on?

Motivation (E-053): an offline audit found that commit-type units which never
wrote pass at 0.057 against 0.427 for peers that write, which suggested landing
was the bottleneck. A pre-registered intervention falsified that -- forcing the
write left the score unchanged. So before building any write-time validator,
establish what the failing writes actually fail on.

This script classifies, per unit, whether the *written* entity contradicts an
explicitly observable element of the user's own instruction. It reads rewards
only to bucket units for inspection, never as a runtime signal.

Usage:
    python scripts/write_failure_audit.py <sim.json> [out.md]
"""

from __future__ import annotations

import collections
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from agent.decision import (  # noqa: E402
    ConstraintOperator,
    ConstraintTarget,
    TaskSpec,
)


def hard_entity_constraints(instruction: str) -> list[str]:
    """Explicit, hard, *observable* entity requirements from the instruction.

    These are the only constraints a bounded pre-write check may enforce: they
    come from the user's own words (I1), they are hard (I4), and they are
    checkable against what the environment reports back.
    """
    spec = TaskSpec.compile(instruction)
    out = []
    for c in spec.must:
        if (
            c.target == ConstraintTarget.CANDIDATE
            and c.operator == ConstraintOperator.CONTAINS
            and c.hard
        ):
            out.append(c.value)
    return out


def entity_constraint_satisfied(instruction: str, results_text: str) -> bool | None:
    """Did the written entity satisfy every explicit entity requirement?

    Returns None when the instruction carries no checkable entity requirement,
    so those units are excluded from the rate instead of counted as failures.
    """
    required = hard_entity_constraints(instruction)
    if not required:
        return None
    names = " ".join(_NAME_RE.findall(results_text))
    if not names:
        return None
    return all(value in names for value in required)

WRITE_EXACT = {"instore_book", "instore_reservation"}

# `Order(order_id:..., status:unpaid, ...)` shapes across the three domains.
_STATUS_RE = re.compile(r"status[:=]\s*(\w+)")
_ORDER_ID_RE = re.compile(r"order_id[:=]\s*(\w+)")
# Attributes carried on the created entity, e.g. `quantity=1`, `date=2026-12-27`.
_DATE_RE = re.compile(r"date=(\d{4}-\d{2}-\d{2})")
_QTY_RE = re.compile(r"quantity=(\d+)")
_PRICE_RE = re.compile(r"(?:total_price|price)[:=]\s*([\d.]+)")
_NAME_RE = re.compile(
    r"(?:product_name|hotel_name|train_number|flight_number|flight_no"
    r"|attraction_name|store_name|name)=([^,)\]]+)"
)

# Chinese content words worth matching against the instruction; deliberately
# coarse, this is a triage heuristic and not a validator.
_STOP = set("帮我个一份一张点去买下送到来的了还是都可以吧呢啊嘛哦哈")


def is_write(name: str) -> bool:
    return name.startswith("create_") or name in WRITE_EXACT


def salient_tokens(text: str) -> set[str]:
    """Rough content-word extraction: 2+ char CJK runs minus stop characters."""
    runs = re.findall(r"[\u4e00-\u9fff]{2,}", text or "")
    out: set[str] = set()
    for run in runs:
        for size in (2, 3):
            for i in range(len(run) - size + 1):
                piece = run[i : i + size]
                if not (set(piece) & _STOP):
                    out.add(piece)
    return out


def unit_records(path: pathlib.Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    for sim in data.get("simulations") or []:
        info = (sim.get("reward_info") or {}).get("info") or {}
        rewards = info.get("subtask_rewards") or {}
        for order, tr in enumerate(
            (sim.get("states") or {}).get("integrity_subtask_trajectories") or []
        ):
            key = f"subtask_{tr.get('subtask_idx')}_reward"
            if key not in rewards:
                continue
            msgs = tr.get("messages") or []
            instr = next(
                (
                    m["content"].strip()
                    for m in msgs
                    if m.get("role") == "user"
                    and isinstance(m.get("content"), str)
                    and m["content"].strip()
                ),
                "",
            )
            calls = [
                c
                for m in msgs
                for c in (m.get("tool_calls") or [])
                if isinstance(c, dict) and c.get("name")
            ]
            results = [str(m.get("content") or "") for m in msgs if m.get("role") == "tool"]
            yield {
                "sim": str(sim.get("task_id")),
                "trial": sim.get("trial"),
                "order": order,
                "subtask_id": tr.get("subtask_id"),
                "instruction": instr,
                "reward": float(rewards[key]),
                "calls": calls,
                "results": results,
                "term": tr.get("termination_reason"),
                "messages": len(msgs),
            }


def tag_unit(u: dict) -> list[str]:
    joined_results = " ".join(u["results"])
    statuses = set(_STATUS_RE.findall(joined_results))
    write_calls = [c for c in u["calls"] if is_write(c["name"])]
    names = " ".join(_NAME_RE.findall(joined_results))
    overlap = salient_tokens(u["instruction"]) & salient_tokens(names)
    tags = []
    if write_calls and "paid" in statuses:
        tags.append("write_paid")
    elif write_calls:
        tags.append("write_unpaid")
    if write_calls and not overlap:
        tags.append("entity_not_in_instruction")
    if not write_calls:
        tags.append("no_write")
    return tags or ["other"]


def main() -> None:
    path = pathlib.Path(sys.argv[1])
    out_path = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else None

    units = list(unit_records(path))
    commit = [u for u in units if TaskSpec.compile(u["instruction"]).action == "commit"]
    wrote = [u for u in commit if any(is_write(c["name"]) for c in u["calls"])]
    failing = [u for u in wrote if u["reward"] == 0.0]
    passing = [u for u in wrote if u["reward"] > 0.0]

    # A tag only has diagnostic value if it is more common among failing writes
    # than among passing ones. Reporting failure rates alone would repeat the
    # E-053 mistake of treating a symptom as a bottleneck.
    fail_tags: collections.Counter = collections.Counter()
    pass_tags: collections.Counter = collections.Counter()
    for u in failing:
        for t in tag_unit(u):
            fail_tags[t] += 1
    for u in passing:
        for t in tag_unit(u):
            pass_tags[t] += 1

    detail: list[str] = []
    for u in failing:
        write_calls = [c for c in u["calls"] if is_write(c["name"])]
        statuses = sorted(set(_STATUS_RE.findall(" ".join(u["results"]))))
        names = sorted(salient_tokens(" ".join(_NAME_RE.findall(" ".join(u["results"])))))
        detail.append(
            f"## {u['subtask_id']}  reward={u['reward']} term={u['term']} msgs={u['messages']}\n"
            f"- instruction: {u['instruction'][:100]}\n"
            f"- writes: {[c['name'] for c in write_calls]}\n"
            f"- statuses: {statuses}\n"
            f"- order/product names: {names[:12]}\n"
            f"- tags: {tag_unit(u)}\n"
        )

    print(f"file                : {path.name}")
    print(f"units               : {len(units)}")
    print(f"commit-type         : {len(commit)}")
    print(f"  of those wrote    : {len(wrote)}")
    print(f"    passing         : {len(passing)}")
    print(f"    failing         : {len(failing)}")
    print()
    print("tag            fail_rate   pass_rate   lift    verdict")
    for tag in sorted(set(fail_tags) | set(pass_tags)):
        fr = fail_tags[tag] / max(1, len(failing))
        pr = pass_tags[tag] / max(1, len(passing))
        lift = (fr / pr) if pr else float("inf")
        verdict = "discriminative" if lift >= 1.5 else ("weak" if lift >= 1.2 else "NOT discriminative")
        print(f"  {tag:26} {fr:8.3f} {pr:10.3f} {lift:7.2f}   {verdict}")
    print()
    term = collections.Counter(u["term"] for u in failing)
    print(f"termination of failing writes: {dict(term)}")
    term_p = collections.Counter(u["term"] for u in passing)
    print(f"termination of passing writes: {dict(term_p)}")

    # Sharp check: did the written entity contradict the user's own words?
    print()
    print("-- explicit hard entity requirement vs the written entity --")
    sharp = {"failing": collections.Counter(), "passing": collections.Counter()}
    for label, group in (("failing", failing), ("passing", passing)):
        for u in group:
            verdict = entity_constraint_satisfied(u["instruction"], " ".join(u["results"]))
            if verdict is None:
                sharp[label]["no_checkable_requirement"] += 1
            elif verdict:
                sharp[label]["satisfied"] += 1
            else:
                sharp[label]["VIOLATED"] += 1
    for label in ("failing", "passing"):
        c = sharp[label]
        base = c["satisfied"] + c["VIOLATED"]
        rate = c["VIOLATED"] / base if base else 0.0
        print(
            f"  {label:8} checkable={base:4}  satisfied={c['satisfied']:4} "
            f"violated={c['VIOLATED']:4}  violation_rate={rate:.3f}  "
            f"(no requirement: {c['no_checkable_requirement']})"
        )

    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            f"# Write-failure audit — `{path.name}`\n\n"
            f"- commit-type units: {len(commit)}\n"
            f"- wrote: {len(wrote)} (passing {len(passing)} / failing {len(failing)})\n"
            f"- failing tags: {dict(fail_tags)}\n"
            f"- passing tags: {dict(pass_tags)}\n"
            f"- interpretation: a tag is only diagnostic when its failing rate is\n"
            f"  clearly above its passing rate; see the table in the run output.\n\n"
            + "\n".join(detail)
            + "\n",
            encoding="utf-8",
        )
        print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()

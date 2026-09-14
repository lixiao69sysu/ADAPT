"""Zero-model measurement: does the memory's preference-evolution machinery fire?

The benchmark's capabilities are personalization, long-term relationship memory
and adaptation to preferences that change over months. The repository already
*has* the machinery for that -- a scoped drift detector, supersession in the fact
store, and a lifecycle that forgets. What has never been measured is whether any
of it actually fires on real trajectories, or whether it is paper capability in
the same way the thrash guard was (E-072: it fired zero times in its own smoke).

For each user this replays the memory pipeline in the orchestrator's order with
``llm=None`` (so only the heuristic path runs) and reports:

* facts created, and their final status distribution
* how often the drift detector fired and how often the lifecycle evicted
* **unresolved single-slot conflicts**: slot keys that still hold more than one
  *active* value at the end. Those are the direct evidence that a preference
  changed and the memory kept both, which is an evolution failure by the
  repository's own rule that only genuinely single-valued same-scope dimensions
  may supersede.
* the observed_at span actually present

Reads only the task's own interaction records; no model call, no rubric.

Usage:
    python scripts/memory_evolution_audit.py
"""

from __future__ import annotations

import argparse
import collections
import os
import re
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent.vitabench_bootstrap as bootstrap  # noqa: E402

bootstrap.enable_vitabench_utf8()

from agent.memory.adapt_memory import ADAPTMemory  # noqa: E402
from agent.memory.facts import SCALAR_DIMENSIONS  # noqa: E402

_EVENTS = re.compile(r"\+(\d+) signals")
_DRIFT = re.compile(r"(\d+) drift detected")
_FORGOT = re.compile(r"(\d+) forgotten")

# Dimensions that are genuinely single-valued in the same scope. Holding two
# active values for one of these is a failure to resolve a change. Imported
# from the data layer so the audit measures the boundary the mechanism actually
# enforces instead of a second copy of the list.
SINGLE_VALUED = SCALAR_DIMENSIONS


def analyse(tasks_by_id: dict[str, Any], users: list[str]) -> dict[str, Any]:
    totals = collections.Counter()
    per_user: dict[str, dict[str, Any]] = {}
    examples: list[str] = []

    for user in users:
        task = tasks_by_id.get(user)
        if task is None:
            continue
        memory = ADAPTMemory()
        signals = drift = forgotten = 0
        for subtask in task.subtasks:
            interactions = getattr(subtask, "interactions", None) or []
            if not interactions:
                continue
            detail = memory.update(list(interactions), llm=None) or ""
            signals += int(m.group(1)) if (m := _EVENTS.search(detail)) else 0
            drift += int(m.group(1)) if (m := _DRIFT.search(detail)) else 0
            forgotten += int(m.group(1)) if (m := _FORGOT.search(detail)) else 0

        facts = list(memory.facts)
        status = collections.Counter(fact.status for fact in facts)
        by_slot: dict[tuple, set] = collections.defaultdict(set)
        for fact in facts:
            if fact.status == "active" and fact.dimension in SINGLE_VALUED:
                by_slot[fact.slot_key].add(fact.value)
        conflicted = {key: values for key, values in by_slot.items() if len(values) > 1}

        stamps = sorted(fact.observed_at for fact in facts if fact.observed_at)
        per_user[user] = {
            "signals": signals,
            "facts": len(facts),
            "active": status.get("active", 0),
            "superseded": status.get("superseded", 0),
            "drift_events": drift,
            "forgotten": forgotten,
            "unresolved_conflicts": len(conflicted),
            "observed_at_span": f"{stamps[0]} .. {stamps[-1]}" if stamps else "",
        }
        totals["signals"] += signals
        totals["facts"] += len(facts)
        totals["active"] += status.get("active", 0)
        totals["superseded"] += status.get("superseded", 0)
        totals["drift"] += drift
        totals["forgotten"] += forgotten
        totals["conflicts"] += len(conflicted)
        for key, values in list(conflicted.items())[:2]:
            if len(examples) < 10:
                examples.append(f"{user} {key} -> {sorted(values)}")

    return {
        "users": len(per_user),
        "totals": {
            "signals": totals["signals"],
            "facts": totals["facts"],
            "active": totals["active"],
            "superseded": totals["superseded"],
            "drift_events": totals["drift"],
            "forgotten": totals["forgotten"],
            "unresolved_single_slot_conflicts": totals["conflicts"],
        },
        "per_user": per_user,
        "conflict_examples": examples,
        "note": (
            "Heuristic path only (llm=None): the LLM extraction and summary are "
            "skipped, so drift/forgetting here are what the rule-based pipeline "
            "produces. 'unresolved_single_slot_conflicts' counts slot keys that "
            "still hold more than one active value for a single-valued dimension."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", default="chinese")
    parser.add_argument("--users", nargs="*", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    from agent.vitabench_runner import get_tasks

    tasks_by_id = {task.id: task for task in get_tasks(args.language)}
    users = args.users or [
        "E057330", "E941775", "J365414", "M793481",
        "O309411", "P722245", "Q089190", "U000828",
    ]
    report = analyse(tasks_by_id, users)
    if args.json:
        import json

        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print(f"users: {report['users']}")
    print(f"totals: {report['totals']}")
    print()
    header = f"{'user':<10}{'signals':>8}{'facts':>7}{'active':>7}{'super':>6}{'drift':>6}{'forgot':>7}{'conflict':>9}"
    print(header)
    print("-" * len(header))
    for user, row in report["per_user"].items():
        print(
            f"{user:<10}{row['signals']:>8}{row['facts']:>7}{row['active']:>7}"
            f"{row['superseded']:>6}{row['drift_events']:>6}{row['forgotten']:>7}"
            f"{row['unresolved_conflicts']:>9}"
        )
    print()
    print("conflict examples:")
    for line in report["conflict_examples"]:
        print(f"  {line}")
    print()
    print(report["note"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

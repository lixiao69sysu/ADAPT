"""Zero-model audit: how often does the current instruction contradict memory?

Before building a conflict-clarification question, measure the population it
would serve. The rule this looks for is narrow, because the repository's priority
order already lets the instruction win silently:

* memory resolves a single positive value for a typed preference slot
  (room_type / transport / taste / caffeine / size / budget), and
* the current instruction states a *different* value for the same slot.

A conflict question is only worth asking if that population is non-trivial.
Reads only instructions, replayed structured facts, and the compiler's own
constraint list; never ``user_intention``.

Usage:
    python scripts/instruction_conflict_audit.py data/simulations/stock_avg4_8u.json
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent.vitabench_bootstrap as bootstrap  # noqa: E402

bootstrap.enable_vitabench_utf8()

from agent.decision import TaskSpec  # noqa: E402
from agent.memory.adapt_memory import ADAPTMemory  # noqa: E402
from agent.memory.slots import _CANONICAL_MARKERS  # noqa: E402
from agent.memory.slots import fact_relevant_to_task  # noqa: E402
from agent.memory.slots import semantic_slot_value  # noqa: E402


def instruction_slot_values(spec: TaskSpec) -> dict[str, str]:
    """Typed slot values the current instruction states explicitly.

    The compiler records stated attributes as ``must`` constraints
    (``kind="attribute"``); they are mapped onto typed slots with the same
    canonical markers the memory side uses, so the two are comparable.
    """
    stated: dict[str, str] = {}
    for constraint in spec.must:
        value = str(getattr(constraint, "value", "") or "")
        if not value:
            continue
        for slot, markers in _CANONICAL_MARKERS.items():
            for canonical, keys in markers:
                if any(key in value for key in keys):
                    stated.setdefault(slot, canonical)
    for slot, value in (spec.resolved_slots or {}).items():
        if value:
            stated.setdefault(slot, str(value))
    return stated


def memory_slot_values(spec: TaskSpec, facts: list[Any]) -> dict[str, str]:
    """Single positive memory value per typed slot, *before* instruction override."""
    values: dict[str, set[str]] = collections.defaultdict(set)
    for fact in facts:
        if fact.polarity != "positive" or not fact.decision_eligible:
            continue
        if not fact_relevant_to_task(fact, spec):
            continue
        for slot in _CANONICAL_MARKERS:
            value = semantic_slot_value(fact, slot)
            if value:
                values[slot].add(value)
    return {
        slot: next(iter(slot_values))
        for slot, slot_values in values.items()
        if len(slot_values) == 1
    }


def analyse(checkpoint: dict, tasks_by_id: dict[str, Any]) -> dict[str, Any]:
    totals = collections.Counter()
    conflicts: collections.Counter = collections.Counter()
    examples: list[str] = []
    seen: set[tuple[str, int]] = set()

    by_user: dict[str, dict[int, dict]] = collections.defaultdict(dict)
    for sim in checkpoint.get("simulations", []):
        user = str(sim.get("task_id"))
        for traj in (sim.get("states") or {}).get("integrity_subtask_trajectories") or []:
            idx = traj.get("subtask_idx")
            if idx is not None:
                by_user[user][int(idx)] = traj

    for user, trajs in sorted(by_user.items()):
        task = tasks_by_id.get(user)
        if task is None:
            continue
        subtasks = list(task.subtasks)
        memory = ADAPTMemory()
        for index in sorted(trajs):
            if index >= len(subtasks):
                continue
            subtask = subtasks[index]
            interactions = getattr(subtask, "interactions", None) or []
            if interactions:
                try:
                    memory.update(list(interactions), llm=None)
                except Exception:  # noqa: BLE001
                    pass
            if (user, index) in seen:
                continue
            seen.add((user, index))

            instruction = subtask.instruction or ""
            spec = TaskSpec.compile(instruction)
            facts = list(memory.facts)
            stated = instruction_slot_values(spec)
            remembered = memory_slot_values(spec, facts)

            totals["subtask_definitions"] += 1
            if remembered:
                totals["memory_resolves_a_slot"] += 1
            if stated:
                totals["instruction_states_a_slot"] += 1
            for slot, value in stated.items():
                other = remembered.get(slot)
                if other and other != value:
                    totals["conflicts"] += 1
                    conflicts[slot] += 1
                    if len(examples) < 12:
                        examples.append(
                            f"[{slot}] memory={other!r} instruction={value!r} "
                            f"{instruction[:38]!r}"
                        )
                elif other == value:
                    totals["agreements"] += 1

    return {
        "subtask_definitions": len(seen),
        "memory_resolves_a_slot": totals["memory_resolves_a_slot"],
        "instruction_states_a_slot": totals["instruction_states_a_slot"],
        "agreements": totals["agreements"],
        "conflicts": totals["conflicts"],
        "conflicts_by_slot": dict(conflicts.most_common()),
        "examples": examples,
        "note": (
            "Unique subtask definitions, not the 400 trial runs. A conflict here "
            "means memory holds one positive value for a typed slot and the "
            "instruction states a different one; the repository's priority order "
            "already lets the instruction win, so a question is only useful if "
            "the change is meant to persist."
        ),
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
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    for key, value in report.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

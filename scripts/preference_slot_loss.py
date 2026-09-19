"""How much does the card's ordering throw away, and does the loss bind?

The live read path is `read() -> compile_task() -> build_decision_card()`, whose
`prefer` list is ordered by a hand-written 13-entry ordinal table
(`decision.py:655 dimension_priority`) and then truncated by `render()` to
`max_facts - hard`. That ordering decides which preferences the model ever sees,
and it has no stated source.

This audit measures the loss **structurally** rather than by tuning: for every
preference entry the renderer drops, does the fact behind it bind to a decision
slot the current instruction still needs (`TaskSpec.unknown_slots`)? A dropped
entry that binds to an unresolved slot is a lost answer -- the memory held a
value for a slot this decision has to settle, and the ranking discarded it. A
dropped entry that binds to nothing is simply not about this decision, which is a
different thing that the current design flattens into the same number.

Nothing here is tuned and no evaluator field is read: the slots come from the
repository's own compiler, the binding test is `slots.semantic_slot_value`, and
the survivors are whatever `DecisionCard.render()` actually emits. Zero model
calls -- the memory is replayed with `llm=None`, the same path as
`scripts/memory_evolution_audit.py`.

Boundary: this is a **reach** measurement, not an effect. It says how large the
lost-answer population is; it says nothing about whether surfacing those entries
would raise the score.

Usage:
    python scripts/preference_slot_loss.py data/simulations/adapt8_1t.json
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
from agent.memory.slots import (  # noqa: E402
    _RESOLVABLE_PREFERENCE_SLOTS,
    fact_relevant_to_task,
    semantic_slot_value,
)


def _dedup(values: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for value in values:
        seen.setdefault(value, None)
    return list(seen)


def _rendered_prefer(rendered: str) -> list[str]:
    """The PREFER values `DecisionCard.render()` actually emitted."""
    for line in rendered.splitlines():
        if line.startswith("PREFER: "):
            return [item.strip() for item in line[len("PREFER: ") :].split(" | ")]
    return []


def analyse(checkpoint: dict, tasks_by_id: dict[str, Any]) -> dict[str, Any]:
    per_slot = collections.Counter()
    per_slot_unique = collections.Counter()
    summary = collections.Counter()
    examples: list[str] = []
    seen_users: set[str] = set()

    for sim in checkpoint.get("simulations", []):
        user_id = str(sim.get("task_id"))
        task = tasks_by_id.get(user_id)
        if task is None or user_id in seen_users:
            continue
        seen_users.add(user_id)
        memory = ADAPTMemory(language="chinese", user_id=user_id)

        for index, subtask in enumerate(task.subtasks):
            instruction = subtask.instruction or ""
            memory.update(list(getattr(subtask, "interactions", None) or []))
            memory.begin_subtask(instruction)

            spec = TaskSpec.compile(instruction)
            card = memory.compile_task(instruction)
            question = memory.propose_question(instruction)
            if question:
                card.ask.insert(0, question)

            preferences = _dedup(card.prefer)
            survivors = set(_rendered_prefer(card.render()))
            dropped = [value for value in preferences if value not in survivors]

            summary["subtasks"] += 1
            summary["prefer_entries"] += len(preferences)
            summary["rendered"] += len(survivors)
            summary["dropped"] += len(dropped)
            summary["subtasks_with_any_drop"] += bool(dropped)
            summary["subtasks_everything_dropped"] += bool(
                preferences and not survivors
            )

            unknown = list(spec.unknown_slots or [])
            if not unknown:
                continue

            active = [
                fact
                for fact in memory.facts
                if getattr(fact, "status", "") == "active"
            ]
            # Mirror `resolve_preference_slots` exactly: only positive,
            # decision-eligible facts that pass the structural relevance gate may
            # answer a slot. Without this guard `semantic_slot_value`'s open-world
            # fallback lets any fact whose dimension happens to equal the slot
            # name count, which is how a burger set came to "answer" the taste
            # slot of a fruit request.
            eligible = [
                fact
                for fact in active
                if fact.polarity == "positive"
                and fact.decision_eligible
                and fact_relevant_to_task(fact, spec)
            ]
            by_value: dict[str, list[Any]] = collections.defaultdict(list)
            for fact in eligible:
                by_value[fact.value].append(fact)
            distinct_per_slot: dict[str, set[str]] = collections.defaultdict(set)
            for fact in eligible:
                for slot in unknown:
                    resolved_value = semantic_slot_value(fact, slot)
                    if resolved_value:
                        distinct_per_slot[slot].add(resolved_value)

            for value in dropped:
                facts = by_value.get(value)
                if not facts:
                    continue
                for slot in unknown:
                    if not any(semantic_slot_value(fact, slot) for fact in facts):
                        continue
                    per_slot[slot] += 1
                    summary["dropped_binding_unknown_slot"] += 1
                    if len(distinct_per_slot[slot]) == 1:
                        per_slot_unique[slot] += 1
                        summary["lost_the_only_value"] += 1
                    if len(examples) < 10:
                        examples.append(
                            f"{user_id} sub{index} [{spec.domain}] "
                            f"slot={slot} value={value!r}\n"
                            f"      instr: {instruction[:56]}"
                        )
                    break

    total = max(1, summary["dropped"])
    return {
        "subtasks": summary["subtasks"],
        "prefer_entries": summary["prefer_entries"],
        "rendered": summary["rendered"],
        "dropped": summary["dropped"],
        "dropped_share": round(summary["dropped"] / max(1, summary["prefer_entries"]), 4),
        "subtasks_with_any_drop": summary["subtasks_with_any_drop"],
        "subtasks_everything_dropped": summary["subtasks_everything_dropped"],
        "dropped_binding_unknown_slot": summary["dropped_binding_unknown_slot"],
        "lost_the_only_value": summary["lost_the_only_value"],
        "share_of_drops_that_were_a_needed_answer": round(
            summary["dropped_binding_unknown_slot"] / total, 4
        ),
        "lost_by_slot": dict(per_slot.most_common()),
        "lost_the_only_value_by_slot": dict(per_slot_unique.most_common()),
        "resolvable_slot_vocabulary": sorted(_RESOLVABLE_PREFERENCE_SLOTS),
        "examples": examples,
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

    print(f"checkpoint {args.checkpoint}")
    for key in (
        "subtasks",
        "prefer_entries",
        "rendered",
        "dropped",
        "dropped_share",
        "subtasks_with_any_drop",
        "subtasks_everything_dropped",
        "dropped_binding_unknown_slot",
        "lost_the_only_value",
        "share_of_drops_that_were_a_needed_answer",
    ):
        print(f"  {key:<44}{report[key]}")
    print(f"  {'lost_by_slot':<44}{report['lost_by_slot']}")
    print(f"  {'lost_the_only_value_by_slot':<44}{report['lost_the_only_value_by_slot']}")
    print()
    for example in report["examples"]:
        print(f"  {example}")
    print()
    print(
        "Reach, not effect. 'dropped_binding_unknown_slot' counts preference "
        "entries the renderer discarded that would have answered a slot this "
        "instruction still has open; 'lost_the_only_value' is the subset where "
        "the memory held exactly one value for that slot."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

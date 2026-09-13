"""Probe: where do compiled constraints actually live?

Zero-model. Prints the composition of the decision card across every subtask
definition in the personalization task set, so we can see whether the graded
conditions are compiled into ``must``, ``avoid``, ``prefer`` or left ``unknown``.
Reads only subtask instructions and environment definitions; never
``user_intention``.
"""

from __future__ import annotations

import collections
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import agent.vitabench_bootstrap as bootstrap  # noqa: E402

bootstrap.enable_vitabench_utf8()

from agent.decision import TaskSpec, build_decision_card  # noqa: E402
from agent.vitabench_runner import get_tasks  # noqa: E402


def main() -> int:
    tasks = {task.id: task for task in get_tasks("chinese")}
    counter: collections.Counter = collections.Counter()
    samples: dict[str, list[tuple[str, str, str]]] = collections.defaultdict(list)
    total = 0

    for task in tasks.values():
        for subtask in task.subtasks:
            instruction = subtask.instruction or ""
            total += 1
            spec = TaskSpec.compile(instruction)
            card = build_decision_card(spec, [])
            if card.must:
                counter["has_card_must"] += 1
            if card.avoid:
                counter["has_card_avoid"] += 1
            if card.prefer:
                counter["has_card_prefer"] += 1
            if spec.unknown_slots:
                counter["has_unknown_slots"] += 1
            if spec.must:
                counter["has_spec_must"] += 1
            if card.must or card.avoid or card.prefer:
                counter["any_constraint"] += 1
            for item in card.must:
                if len(samples["must"]) < 15:
                    samples["must"].append((subtask.domain, instruction[:36], item))
            for item in spec.unknown_slots:
                if len(samples["unknown"]) < 15:
                    samples["unknown"].append((subtask.domain, instruction[:36], item))
            for item in card.avoid:
                if len(samples["avoid"]) < 10:
                    samples["avoid"].append((subtask.domain, instruction[:36], item))

    print(f"subtask definitions: {total}")
    for key in (
        "has_card_must",
        "has_spec_must",
        "has_card_avoid",
        "has_card_prefer",
        "has_unknown_slots",
        "any_constraint",
    ):
        share = counter[key] / total if total else 0.0
        print(f"  {key:20} {counter[key]:4}  ({share:.1%})")

    for name in ("must", "avoid", "unknown"):
        print()
        print(f"sample {name}:")
        for domain, instruction, item in samples[name]:
            print(f"  [{domain}] {instruction!r} -> {item!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

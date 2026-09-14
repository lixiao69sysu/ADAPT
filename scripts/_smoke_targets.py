"""List dev-cohort subtasks where the gap-driven policy would ask a question.

Zero-model. Used to pick the cheapest meaningful smoke: one user, one subtask,
where the proactive loop should have something to do. Reads only instructions and
the structured facts replayed in the orchestrator's order; never
``user_intention``.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent.vitabench_bootstrap as bootstrap  # noqa: E402

bootstrap.enable_vitabench_utf8()

from agent.decision import TaskSpec  # noqa: E402
from agent.memory.adapt_memory import ADAPTMemory  # noqa: E402
from agent.memory.proactive import QuestionContext  # noqa: E402

DEV = [
    "E057330",
    "E941775",
    "J365414",
    "M793481",
    "O309411",
    "P722245",
    "Q089190",
    "U000828",
]


def main() -> int:
    checkpoint = pathlib.Path("data/simulations/stock_avg4_8u.json")
    with checkpoint.open(encoding="utf-8") as handle:
        trials = json.load(handle)

    from agent.vitabench_runner import get_tasks

    tasks_by_id = {task.id: task for task in get_tasks("chinese")}

    # Which users actually appear in the cached dev checkpoint.
    present = {str(sim.get("task_id")) for sim in trials.get("simulations", [])}
    print(f"users in the cached checkpoint: {sorted(present)}")

    for user in sorted(present):
        task = tasks_by_id.get(user)
        if task is None:
            continue
        memory = ADAPTMemory()
        for index, subtask in enumerate(task.subtasks):
            interactions = getattr(subtask, "interactions", None) or []
            if interactions:
                try:
                    memory.update(list(interactions), llm=None)
                except Exception:  # noqa: BLE001
                    pass
            instruction = subtask.instruction or ""
            spec = TaskSpec.compile(instruction)
            if not spec.unknown_slots:
                continue
            known = {
                slot: value
                for slot, value in (spec.resolved_slots or {}).items()
                if value
            }
            context = QuestionContext(
                instruction=instruction,
                domain=spec.domain,
                facet=spec.facet,
                action=spec.action,
                unknown_slots=tuple(spec.unknown_slots or ()),
                resolved_slots=known,
                known_slots={},
            )
            engine = memory.proactive
            proposal = engine.propose(context=context)
            if proposal is None:
                continue
            print(
                f"  {user} sub{index:<2} [{spec.domain}/{spec.action}] "
                f"slot={proposal.slot:<12} {instruction[:38]!r}"
            )
            print(f"        -> {proposal.question}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

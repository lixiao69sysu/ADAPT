"""Zero-model audit: how often does the gap-driven policy ask, and about what?

Replays the saved stock trajectories: for each subtask, compile the instruction,
replay the ADAPT fact store in the orchestrator's order, and ask the current
``ProactiveEngine`` what it would propose. No model call, no rubric, no
``user_intention``.

This is the coverage check for the policy rewrite. The old policy asked from
topic keywords; the new one asks only about a declared, user-only, still-unknown
slot. The risk to watch is silently losing coverage on recommendation-type
subtasks, so the report splits by domain and action and prints the slot mix.

Usage:
    python scripts/proactive_coverage_audit.py data/simulations/stock_avg4_8u.json
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

from agent.decision import TaskSpec  # noqa: E402
from agent.memory.adapt_memory import ADAPTMemory  # noqa: E402
from agent.memory.proactive import ProactiveEngine, QuestionContext  # noqa: E402
from agent.memory.slots import resolve_preference_slots  # noqa: E402


def analyse(checkpoint: dict, tasks_by_id: dict[str, Any]) -> dict[str, Any]:
    totals = collections.Counter()
    slots = collections.Counter()
    per_domain: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    per_action: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    examples: list[str] = []

    by_sim: dict[tuple[str, Any], dict[int, dict]] = collections.defaultdict(dict)
    for sim in checkpoint.get("simulations", []):
        key = (str(sim.get("task_id")), sim.get("trial"))
        for traj in (sim.get("states") or {}).get("integrity_subtask_trajectories") or []:
            idx = traj.get("subtask_idx")
            if idx is not None:
                by_sim[key][int(idx)] = traj

    seen_subtasks: set[tuple[str, int]] = set()
    for (task_id, _trial), trajs in sorted(by_sim.items(), key=lambda kv: str(kv[0])):
        task = tasks_by_id.get(task_id)
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
                except Exception:  # noqa: BLE001 - offline replay must not abort
                    pass

            unique = (task_id, index)
            if unique in seen_subtasks:
                continue
            seen_subtasks.add(unique)

            instruction = subtask.instruction or ""
            spec = TaskSpec.compile(instruction)
            known = resolve_preference_slots(spec, memory.facts)
            context = QuestionContext(
                instruction=instruction,
                domain=spec.domain,
                facet=spec.facet,
                action=spec.action,
                unknown_slots=tuple(spec.unknown_slots or ()),
                resolved_slots={
                    slot: str(value)
                    for slot, value in (spec.resolved_slots or {}).items()
                    if value
                },
                known_slots=dict(known or {}),
            )
            engine = ProactiveEngine()
            question = engine.propose_question(context=context)

            totals["subtask_definitions"] += 1
            per_domain[spec.domain]["n"] += 1
            per_action[spec.action]["n"] += 1
            if spec.unknown_slots:
                totals["has_declared_gap"] += 1
            if question:
                totals["asks"] += 1
                per_domain[spec.domain]["asks"] += 1
                per_action[spec.action]["asks"] += 1
                for slot in engine.open_gaps(context):
                    slots[slot] += 1
                if len(examples) < 6:
                    examples.append(
                        f"[{spec.domain}/{spec.action}] {instruction[:30]!r} -> {question[:44]!r}"
                    )

    return {
        "subtask_definitions": totals["subtask_definitions"],
        "has_declared_gap": totals["has_declared_gap"],
        "asks": totals["asks"],
        "ask_rate": round(totals["asks"] / totals["subtask_definitions"], 4)
        if totals["subtask_definitions"]
        else 0.0,
        "gap_slot_mix": dict(slots.most_common()),
        "by_domain": {k: dict(v) for k, v in sorted(per_domain.items())},
        "by_action": {k: dict(v) for k, v in sorted(per_action.items())},
        "examples": examples,
        "note": (
            "Unique subtask definitions, not the 400 trial runs: trials replay "
            "the same script, so counting them would overstate coverage. Reads "
            "only the instruction and replayed structured facts."
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

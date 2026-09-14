"""Zero-model feasibility check for the confirmation-signal intervention.

E-073 identified the largest mechanically clean failure population: 60 runs where
the instruction asked for a transaction, the target product was printed, the agent
never attempted a write, and the run ended with the agent asking the user a
question.

The pre-registered idea is to show each observed candidate's relation to the
current instruction's constraints ("satisfied" in particular), in the hope that
seeing what is already established reduces unnecessary asking.

Under the frozen comparison configuration (stock + RewriteMemory) there is no
structured memory, so the only available constraint source is
``TaskSpec.compile(instruction)``. This script measures how much of that
population the resulting view could even reach: if a run yields no constraint,
the view has nothing to say and the intervention cannot touch it.

Reads only instructions, tool calls and message text; never rubric or
``user_intention``.

Usage:
    python scripts/confirmation_feasibility.py data/simulations/stock_dev.json
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

from agent.decision import TaskSpec, build_decision_card  # noqa: E402
from agent.runtime.correspondence import constraints_from_card  # noqa: E402
from scripts.target_reachability import rank_of, result_records, target_ids  # noqa: E402
from scripts.no_write_audit import is_write_tool  # noqa: E402


def _no_write_ask_population(checkpoint: dict, tasks_by_id: dict[str, Any]) -> list[dict]:
    """The E-073 population, plus the instruction of each run."""
    rows: list[dict] = []
    for sim in checkpoint.get("simulations", []):
        task = tasks_by_id.get(str(sim.get("task_id")))
        if task is None:
            continue
        subtasks = list(task.subtasks)
        rewards = ((sim.get("reward_info") or {}).get("info") or {}).get(
            "subtask_rewards"
        ) or {}
        for traj in (sim.get("states") or {}).get(
            "integrity_subtask_trajectories"
        ) or []:
            index = traj.get("subtask_idx")
            if index is None or index >= len(subtasks):
                continue
            reward = rewards.get(f"subtask_{index}_reward")
            if reward is None or float(reward) != 0.0:
                continue
            _entities, products, _hosts = target_ids(subtasks[index].environment or {})
            if not products:
                continue
            messages = traj.get("messages") or []
            printed = False
            for message in messages:
                if message.get("role") != "tool":
                    continue
                records = result_records(message.get("content"))
                if any(rank_of(target, records) is not None for target in products):
                    printed = True
                    break
            if not printed:
                continue
            wrote = any(
                is_write_tool(str(call.get("name") or ""))
                for message in messages
                for call in (message.get("tool_calls") or [])
                if isinstance(call, dict)
            )
            if wrote:
                continue
            rows.append(
                {
                    "user": str(sim.get("task_id")),
                    "subtask_idx": index,
                    "domain": subtasks[index].domain,
                    "instruction": subtasks[index].instruction or "",
                }
            )
    return rows


def analyse(checkpoint: dict, tasks_by_id: dict[str, Any]) -> dict[str, Any]:
    population = _no_write_ask_population(checkpoint, tasks_by_id)
    counts: collections.Counter = collections.Counter()
    sources: collections.Counter = collections.Counter()
    examples: list[str] = []
    for row in population:
        spec = TaskSpec.compile(row["instruction"])
        card = build_decision_card(spec, [])
        constraints = constraints_from_card(card)
        hard = [c for c in constraints if c.hard]
        counts["runs"] += 1
        counts["action_" + spec.action] += 1
        if constraints:
            counts["has_any_constraint"] += 1
        if hard:
            counts["has_hard_constraint"] += 1
        for constraint in constraints:
            sources[constraint.source] += 1
        if len(examples) < 8:
            examples.append(
                f"[{row['domain']}/{spec.action}] {row['instruction'][:34]!r} -> "
                f"{[(c.value, c.polarity.value, c.hard) for c in constraints][:4]}"
            )

    return {
        "population": counts["runs"],
        "has_any_constraint_from_the_instruction": counts["has_any_constraint"],
        "has_a_hard_constraint": counts["has_hard_constraint"],
        "reachable_share": round(counts["has_any_constraint"] / counts["runs"], 4)
        if counts["runs"]
        else 0.0,
        "constraint_sources": dict(sources.most_common()),
        "action_mix": {
            key.removeprefix("action_"): value
            for key, value in counts.items()
            if key.startswith("action_")
        },
        "examples": examples,
        "note": (
            "A run with no constraint from the instruction gives the view nothing "
            "to display, so it cannot be affected by the intervention. This is an "
            "upper bound on reach under stock + RewriteMemory, not evidence of "
            "effect."
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

"""Reach of a write-time constraint guardrail, measured zero-model.

Question this answers, before any run is paid for: **among failing runs, how
often did a WRITE bind a candidate that the current instruction's own compiled
constraints mark as VIOLATED?** A guardrail that refuses (or redirects) such a
write can only ever act on those runs, so that count is its reach -- the same
discipline as `scripts/task_state_reach.py` (E-091) and the E-074 kill line.

Mechanics, all from the repository's own compilers -- nothing is re-derived and
no evaluator field is read at mechanism level:

    instruction -> TaskSpec.compile -> build_decision_card(spec, [])
                -> constraints_from_card      (the instruction's conditions)
    tool results -> CandidateLedger.observe  (what the model actually saw)
    write arguments -> ledger.constraint_candidates
                    -> correspondence_for_candidate (satisfied/violated/unknown)

`build_decision_card` appends relevant **negative** facts to ``card.avoid`` with
``hard=fact.dimension in {"avoid","safety"}`` (decision.py:720), so the
constraint set depends on the user's accumulated history, not only on the
instruction. The audit therefore replays each user's interactions into
``ADAPTMemory`` (zero model calls, ``llm=None``, same as
``scripts/memory_evolution_audit.py``) and compiles against the live fact store
-- reproducing what the agent's own ``_current_constraints`` sees. ``--no-memory``
drops the replay and compiles from the instruction alone, which is the lower
bound.

Two boundaries worth stating:

* Soft constraints (remembered positive preferences) are counted separately from
  hard ones. Only hard conditions -- the graded requirements and prohibitions --
  are what a guardrail may act on; acting on a soft preference would be the
  governance pattern E-042 measured as net negative.
* Parent-relationship and numeric conditions report UNKNOWN without printed
  evidence, and are never counted as violations.

Usage:
    python scripts/write_constraint_reach.py data/simulations/adapt8_1t.json
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

from agent.candidate_ledger import CandidateLedger  # noqa: E402
from agent.decision import TaskSpec, build_decision_card  # noqa: E402
from agent.memory.adapt_memory import ADAPTMemory  # noqa: E402
from agent.runtime.correspondence import (  # noqa: E402
    ConstraintStatus,
    build_vocabulary,
    constraints_from_card,
    correspondence_for_candidate,
)
from scripts.target_reachability import is_write_tool  # noqa: E402

# One rescued run moves the official Avg@4 by this much: the unit is
# (person, subtask), a rescue moves 1/4 of that unit's trial mean, and each unit
# carries 1/100 of the cohort. Same constant as E-084 / E-089 / E-091.
UNIT_VALUE = 0.0025


def _violations(candidate: Any, constraints: list[Any], vocabulary: dict[str, str]):
    hard, soft = [], []
    for correspondence in correspondence_for_candidate(
        candidate, constraints, vocabulary
    ):
        if correspondence.status is not ConstraintStatus.VIOLATED:
            continue
        (hard if correspondence.constraint.hard else soft).append(correspondence)
    return hard, soft


def _describe(correspondences) -> list[str]:
    return [
        f"{c.constraint.value}({c.constraint.polarity.value}"
        f"{'/hard' if c.constraint.hard else '/soft'} -> {c.observed_value or '-'})"
        for c in correspondences
    ]


def analyse(
    checkpoint: dict, tasks_by_id: dict[str, Any], *, replay_memory: bool = True
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sim in checkpoint.get("simulations", []):
        task = tasks_by_id.get(str(sim.get("task_id")))
        if task is None:
            continue
        subtasks = list(task.subtasks)
        rewards = ((sim.get("reward_info") or {}).get("info") or {}).get(
            "subtask_rewards"
        ) or {}
        trajectories = {
            traj.get("subtask_idx"): traj
            for traj in (sim.get("states") or {}).get(
                "integrity_subtask_trajectories"
            )
            or []
        }
        memory = None
        if replay_memory:
            memory = ADAPTMemory(language="chinese", user_id=str(sim.get("task_id")))

        for index, subtask in enumerate(subtasks):
            instruction = subtask.instruction or ""
            if memory is not None:
                # L1 order: the subtask's own history is folded in (2a) before
                # the subtask runs, then the instruction is set.
                memory.update(list(getattr(subtask, "interactions", None) or []))
                memory.begin_subtask(instruction)
                card = memory.compile_task(instruction)
            else:
                card = build_decision_card(TaskSpec.compile(instruction), [])
            constraints = constraints_from_card(card)
            hard_constraints = [c for c in constraints if c.hard]

            reward = rewards.get(f"subtask_{index}_reward")
            traj = trajectories.get(index)
            if reward is None or traj is None:
                continue
            messages = traj.get("messages") or []

            ledger = CandidateLedger()
            wrote = False
            resolved_bound = 0
            hard_hits: list[Any] = []
            soft_hits: list[Any] = []
            for message in messages:
                role = message.get("role")
                if role == "tool":
                    ledger.observe(
                        str(message.get("name") or ""), message.get("content")
                    )
                    continue
                if role != "assistant":
                    continue
                for call in message.get("tool_calls") or []:
                    if not isinstance(call, dict):
                        continue
                    if not is_write_tool(str(call.get("name") or "")):
                        continue
                    wrote = True
                    arguments = call.get("arguments")
                    if not isinstance(arguments, dict):
                        continue
                    bound = ledger.constraint_candidates(arguments)
                    if not bound:
                        continue
                    vocabulary = build_vocabulary(ledger.candidates.values())
                    for candidate in bound:
                        resolved_bound += 1
                        hard, soft = _violations(candidate, constraints, vocabulary)
                        hard_hits.extend(hard)
                        soft_hits.extend(soft)

            rows.append(
                {
                    "task_id": str(sim.get("task_id")),
                    "subtask_idx": index,
                    "domain": subtasks[index].domain,
                    "reward": float(reward),
                    "wrote": wrote,
                    "resolved_bound": resolved_bound,
                    "n_constraints": len(constraints),
                    "n_hard": len(hard_constraints),
                    "hard_violations": _describe(hard_hits),
                    "soft_violations": _describe(soft_hits),
                    "instruction": instruction[:60],
                }
            )
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    failing = [row for row in rows if row["reward"] != 1.0]
    wrote = [row for row in failing if row["wrote"]]
    resolved = [row for row in wrote if row["resolved_bound"]]
    hard = [row for row in resolved if row["hard_violations"]]
    soft_only = [
        row for row in resolved if row["soft_violations"] and not row["hard_violations"]
    ]
    with_conditions = [row for row in failing if row["n_hard"]]
    return {
        "graded_runs": len(rows),
        "failing_runs": len(failing),
        "failing_with_hard_conditions": len(with_conditions),
        "failing_with_zero_hard_conditions": len(failing) - len(with_conditions),
        "failing_that_wrote": len(wrote),
        "failing_write_bound_resolved": len(resolved),
        "failing_bound_a_hard_violation": len(hard),
        "failing_bound_only_a_soft_violation": len(soft_only),
        "reach_ceiling_on_avg4": round(len(hard) * UNIT_VALUE, 4),
        "domains_of_hits": dict(collections.Counter(row["domain"] for row in hard)),
        "examples": [
            {
                "unit": f"{row['task_id']} sub{row['subtask_idx']}",
                "domain": row["domain"],
                "instruction": row["instruction"],
                "violations": row["hard_violations"][:4],
            }
            for row in hard[:8]
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoint")
    parser.add_argument("--language", default="chinese")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--no-memory",
        action="store_true",
        help="compile from the instruction alone (lower bound, no replay)",
    )
    args = parser.parse_args()

    with open(args.checkpoint, encoding="utf-8") as handle:
        checkpoint = json.load(handle)
    from agent.vitabench_runner import get_tasks

    tasks_by_id = {task.id: task for task in get_tasks(args.language)}
    rows = analyse(checkpoint, tasks_by_id, replay_memory=not args.no_memory)
    report = summarize(rows)
    report["memory_replayed"] = not args.no_memory
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print(f"checkpoint {args.checkpoint}")
    for key in (
        "graded_runs",
        "failing_runs",
        "failing_with_hard_conditions",
        "failing_with_zero_hard_conditions",
        "failing_that_wrote",
        "failing_write_bound_resolved",
        "failing_bound_a_hard_violation",
        "failing_bound_only_a_soft_violation",
        "reach_ceiling_on_avg4",
    ):
        print(f"  {key:<40}{report[key]}")
    print(f"  {'domains_of_hits':<40}{report['domains_of_hits']}")
    print()
    for example in report["examples"]:
        print(f"  {example['unit']} [{example['domain']}] {example['instruction']}")
        for violation in example["violations"]:
            print(f"      {violation}")
    print()
    print(
        "Reach is not effect. A guardrail may only act on hard conditions; the "
        "soft-violation column is reported for completeness and is the "
        "governance pattern E-042 measured net negative."
    )
    if not report.get("memory_replayed", True):
        print(
            "Ran with --no-memory: the instruction alone was compiled, so this "
            "is the lower bound and under-counts the conditions a real memory adds."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

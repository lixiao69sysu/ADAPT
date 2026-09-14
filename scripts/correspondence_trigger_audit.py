"""Zero-model trigger audit: would the constraint view actually fire on real runs?

The pre-registered plan requires proving the candidate evidence view *triggers*
before spending model budget on an A/B. This script answers that mechanically,
replaying the saved stock trajectories instead of running the agent:

for every subtask-trial it
  1. compiles the subtask's **instruction** into a Decision Card
     (the instruction only -- never ``user_intention``, which is hidden),
  2. parses candidates out of the tool results the agent actually received,
  3. builds the three-valued correspondence and records whether the rendered
     view is non-empty and whether any constraint resolved to something other
     than UNKNOWN.

Output is a coverage report: how often the view would have had something to say,
and of what kind. It is an upper bound on usefulness, not evidence of gain.

Usage:
    python scripts/correspondence_trigger_audit.py data/simulations/stock_dev.json
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

from agent.candidate_ledger import CandidateLedger  # noqa: E402
from agent.decision import TaskSpec, build_decision_card  # noqa: E402
from agent.runtime.correspondence import (  # noqa: E402
    build_vocabulary,
    constraints_from_card,
    correspondence_for_candidate,
    render_correspondence_block,
)


def _tool_results(messages: list[dict]) -> list[tuple[str, str]]:
    """(tool_name, content) for each tool result in the trajectory."""
    names: dict[int, str] = {}
    for index, message in enumerate(messages):
        for call in message.get("tool_calls") or []:
            if isinstance(call, dict) and call.get("id"):
                names[call["id"]] = str(call.get("name") or "")
    out: list[tuple[str, str]] = []
    for message in messages:
        if message.get("role") != "tool":
            continue
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        name = str(message.get("name") or names.get(message.get("tool_call_id"), ""))
        out.append((name, content))
    return out


def analyse(
    checkpoint: dict, tasks_by_id: dict[str, Any], with_memory: bool = False
) -> dict[str, Any]:
    """Coverage report. With ``with_memory`` the ADAPT fact store is replayed.

    ``with_memory`` mirrors the orchestrator's ordering (process a subtask's
    interactions into memory, then run it) using ``llm=None``, so it is still
    zero-model. It answers whether memory-derived constraints -- the allergies
    and dislikes that actually motivate E-064 -- raise coverage above what the
    instruction alone can carry.
    """
    from agent.memory.adapt_memory import ADAPTMemory

    totals = collections.Counter()
    status_totals = collections.Counter()
    hard_status = collections.Counter()
    soft_status = collections.Counter()
    per_domain: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    examples: list[str] = []
    source_totals = collections.Counter()
    hard_source_totals = collections.Counter()

    by_sim: dict[tuple[str, Any], dict[int, dict]] = collections.defaultdict(dict)
    for sim in checkpoint.get("simulations", []):
        key = (str(sim.get("task_id")), sim.get("trial"))
        for traj in (sim.get("states") or {}).get("integrity_subtask_trajectories") or []:
            idx = traj.get("subtask_idx")
            if idx is not None:
                by_sim[key][int(idx)] = traj

    for (task_id, _trial), trajs in sorted(by_sim.items(), key=lambda kv: str(kv[0])):
        task = tasks_by_id.get(task_id)
        if task is None:
            continue
        subtasks = list(task.subtasks)
        memory = ADAPTMemory() if with_memory else None

        for index in sorted(trajs):
            if index >= len(subtasks):
                continue
            traj = trajs[index]
            domain = subtasks[index].domain
            instruction = subtasks[index].instruction or ""
            totals["subtask_trials"] += 1
            per_domain[domain]["runs"] += 1

            if memory is not None:
                interactions = getattr(subtasks[index], "interactions", None) or []
                if interactions:
                    try:
                        memory.update(list(interactions), llm=None)
                    except Exception:  # noqa: BLE001 - offline replay must not abort
                        pass

            facts = list(memory.facts) if memory is not None else []
            card = build_decision_card(TaskSpec.compile(instruction), facts)
            constraints = constraints_from_card(card)
            if not constraints:
                totals["no_constraint"] += 1
                per_domain[domain]["no_constraint"] += 1
                continue
            totals["has_constraint"] += 1
            per_domain[domain]["has_constraint"] += 1
            for constraint in constraints:
                source_totals[constraint.source] += 1
                if constraint.hard:
                    hard_source_totals[constraint.source] += 1
            if any(c.hard for c in constraints):
                totals["has_hard_constraint"] += 1
                per_domain[domain]["has_hard_constraint"] += 1

            ledger = CandidateLedger()
            for name, content in _tool_results(traj.get("messages") or []):
                try:
                    ledger.observe(name, content)
                except Exception:  # noqa: BLE001 - offline replay must not abort
                    continue
            candidates = list(ledger.candidates.values())
            if not candidates:
                totals["no_candidates"] += 1
                per_domain[domain]["no_candidates"] += 1
                continue

            # The candidate set induces the attribute vocabulary, which is what
            # lets a requirement without an explicit key be refuted at all.
            vocabulary = build_vocabulary(candidates)

            block = render_correspondence_block(
                candidates, constraints, vocabulary=vocabulary
            )
            if block:
                totals["view_non_empty"] += 1
                per_domain[domain]["view_non_empty"] += 1
                if len(examples) < 5:
                    examples.append(f"[{domain}] {block.splitlines()[0][:150]}")

            for candidate in candidates:
                for item in correspondence_for_candidate(
                    candidate, constraints, vocabulary
                ):
                    status_totals[item.status.value] += 1
                    if item.constraint.hard:
                        hard_status[item.status.value] += 1
                    else:
                        soft_status[item.status.value] += 1

    resolved = status_totals.get("satisfied", 0) + status_totals.get("violated", 0)
    hard_pairs = sum(hard_status.values())
    return {
        "with_memory": with_memory,
        "subtask_trials": totals["subtask_trials"],
        "runs_with_a_constraint": totals["has_constraint"],
        "runs_with_a_hard_constraint": totals["has_hard_constraint"],
        "no_constraint": totals["no_constraint"],
        "has_constraint_but_no_parsed_candidate": totals["no_candidates"],
        "view_non_empty": totals["view_non_empty"],
        "view_non_empty_rate_of_all_runs": round(
            totals["view_non_empty"] / totals["subtask_trials"], 4
        )
        if totals["subtask_trials"]
        else 0.0,
        "constraint_sources": dict(source_totals),
        "hard_constraint_sources": dict(hard_source_totals),
        "all_pairs_by_status": dict(status_totals),
        "hard_pairs_by_status": dict(hard_status),
        "soft_pairs_by_status": dict(soft_status),
        "hard_pairs_total": hard_pairs,
        "resolved_share_of_all_pairs": round(resolved / sum(status_totals.values()), 4)
        if status_totals
        else 0.0,
        "by_domain": {
            domain: dict(counter) for domain, counter in sorted(per_domain.items())
        },
        "examples": examples,
        "note": (
            "This is trigger coverage, not effect. It reads only the subtask "
            "instruction and the tool results the agent already received; "
            "user_intention is never read. A non-empty view means the mechanism "
            "would have fired, not that it would have helped."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoint")
    parser.add_argument("--language", default="chinese")
    parser.add_argument(
        "--with-memory",
        action="store_true",
        help="also replay the ADAPT fact store across subtasks (still zero-model)",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    with open(args.checkpoint, encoding="utf-8") as handle:
        checkpoint = json.load(handle)

    from agent.vitabench_runner import get_tasks

    tasks_by_id = {task.id: task for task in get_tasks(args.language)}
    report = analyse(checkpoint, tasks_by_id, with_memory=args.with_memory)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    for key, value in report.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""How many of the questions the agent actually asked could not have changed anything?

The question policy asks when a slot is open. That is a *gap* test, not a *value*
test: it never asks whether the answer would change which candidate gets chosen.
A question is provably worthless, before any answer arrives, in three cases:

    no askable gap        nothing is open that the policy itself considers
                          askable (tool-findable slots excluded), so the question
                          was aimed at no slot at all;
    <= 1 candidate        with one candidate in hand, no answer can change the
                          choice;
    identical candidates  every observed candidate printed the same attributes, so
                          no user-only slot can separate them.

Everything else is *possibly* useful -- this is a lower bound on the wasted
questions, and it deliberately does not claim the rest were well spent.

Zero model. The fact store is replayed per user; candidates come from the same
CandidateLedger the live agent uses; "askable" is the repository's own
USER_ONLY_SLOTS minus TOOL_FINDABLE_SLOTS minus CONTEXT_RESOLVABLE_SLOTS.

Usage:
    python scripts/question_voi_reach.py data/simulations/adapt8_1t.json
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

from agent.adapt_agent import asked_the_question, task_state_slots  # noqa: E402
from agent.candidate_ledger import CandidateLedger  # noqa: E402
from agent.memory.adapt_memory import ADAPTMemory  # noqa: E402
from scripts.task_state_reach import askable_gaps, is_write_call  # noqa: E402

UNIT_PER_RUN = 0.01  # one rescued run of a 1-trial, 100-unit arm


def _signatures(ledger: CandidateLedger) -> set[tuple[str, ...]]:
    """One sorted attribute signature per observed candidate."""
    out: set[tuple[str, ...]] = set()
    for candidate in getattr(ledger, "candidates", {}).values():
        attributes = tuple(
            sorted(
                f"{key}={value}"
                for key, value in (getattr(candidate, "attributes", None) or {}).items()
                if str(value or "").strip()
            )
        )
        out.add(attributes)
    return out


def analyse(checkpoint: dict, tasks_by_id: dict[str, Any]) -> dict[str, Any]:
    counts: collections.Counter = collections.Counter()
    examples: list[str] = []
    seen_users: set[str] = set()

    for sim in checkpoint.get("simulations", []):
        user_id = str(sim.get("task_id"))
        task = tasks_by_id.get(user_id)
        if task is None or user_id in seen_users:
            continue
        seen_users.add(user_id)
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
        memory = ADAPTMemory(language="chinese", user_id=user_id)

        for index, subtask in enumerate(task.subtasks):
            instruction = subtask.instruction or ""
            memory.update(list(getattr(subtask, "interactions", None) or []))
            memory.begin_subtask(instruction)
            resolved = dict(memory.resolve_task_slots(instruction))

            traj = trajectories.get(index)
            reward = rewards.get(f"subtask_{index}_reward")
            if traj is None or reward is None or float(reward) == 1.0:
                continue

            slots = task_state_slots(instruction, resolved)
            required = [slot for slot, _ in slots]
            open_slots = [slot for slot, settled in slots if not settled]
            gaps = askable_gaps(open_slots)
            # Strict observer: the question the policy itself would propose. The
            # loose `_looks_like_a_question` counts a closing "还有什么需要帮忙的
            # 吗？" as a question, which is why it is not used here.
            proposal = memory.propose_question(instruction)

            ledger = CandidateLedger()
            write_attempted = False
            last_question = ""
            candidates_at_question = 0
            signatures_at_question: set[tuple[str, ...]] = set()
            gaps_at_question: list[str] = []
            asked = False

            for message in traj.get("messages") or []:
                if not isinstance(message, dict):
                    continue
                role = message.get("role")
                if role == "tool":
                    ledger.observe(
                        str(message.get("name") or ""), message.get("content")
                    )
                    continue
                if role != "assistant":
                    continue
                content = message.get("content")
                for call in message.get("tool_calls") or []:
                    if isinstance(call, dict) and is_write_call(
                        str(call.get("name") or "")
                    ):
                        write_attempted = True
                if isinstance(content, str) and content.strip():
                    # Recompute the askable gaps against the memory state at the
                    # moment of speaking (same replay, so the value is identical).
                    if proposal and asked_the_question(proposal, content):
                        asked = True
                        last_question = content.strip()
                        candidates_at_question = len(
                            getattr(ledger, "candidates", {})
                        )
                        signatures_at_question = _signatures(ledger)
                        gaps_at_question = list(gaps)

            if not asked:
                counts["failing_runs_that_never_asked"] += 1
                continue
            counts["failing_runs_that_asked"] += 1
            counts["_write_attempted_any"] += int(write_attempted)
            counts["_required_slots_nonempty"] += int(bool(required))

            if not gaps_at_question:
                counts["provably_worthless__no_askable_gap"] += 1
                if len(examples) < 6:
                    examples.append(
                        f"{user_id} sub{index} NO-GAP | {instruction[:38]}\n"
                        f"      asked: {last_question[:70]}"
                    )
                continue
            if candidates_at_question <= 1:
                counts["provably_worthless__at_most_one_candidate"] += 1
                if len(examples) < 8:
                    examples.append(
                        f"{user_id} sub{index} 1-CAND | {instruction[:38]}\n"
                        f"      asked: {last_question[:70]}"
                    )
                continue
            if len(signatures_at_question) <= 1:
                counts["provably_worthless__identical_candidates"] += 1
                continue
            counts["candidates_differ__possibly_useful"] += 1

    worthless = (
        counts["provably_worthless__no_askable_gap"]
        + counts["provably_worthless__at_most_one_candidate"]
        + counts["provably_worthless__identical_candidates"]
    )
    asked = counts["failing_runs_that_asked"]
    return {
        "failing_runs_that_asked": asked,
        "failing_runs_that_never_asked": counts["failing_runs_that_never_asked"],
        "provably_worthless__no_askable_gap": counts[
            "provably_worthless__no_askable_gap"
        ],
        "provably_worthless__at_most_one_candidate": counts[
            "provably_worthless__at_most_one_candidate"
        ],
        "provably_worthless__identical_candidates": counts[
            "provably_worthless__identical_candidates"
        ],
        "candidates_differ__possibly_useful": counts[
            "candidates_differ__possibly_useful"
        ],
        "provably_worthless_total": worthless,
        "worthless_share_of_asked": round(worthless / asked, 4) if asked else 0.0,
        "ceiling_if_all_worthless_questions_removed": round(
            worthless * UNIT_PER_RUN, 4
        ),
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
        "failing_runs_that_asked",
        "failing_runs_that_never_asked",
        "provably_worthless__no_askable_gap",
        "provably_worthless__at_most_one_candidate",
        "provably_worthless__identical_candidates",
        "candidates_differ__possibly_useful",
        "provably_worthless_total",
        "worthless_share_of_asked",
        "ceiling_if_all_worthless_questions_removed",
    ):
        print(f"  {key:<46}{report[key]}")
    print()
    for line in report["examples"]:
        print(f"  {line}")
    print()
    print(
        "Lower bound on wasted questions. A question is called worthless only "
        "when no answer could have changed the choice; 'possibly useful' is not "
        "a claim that it was useful."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

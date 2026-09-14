"""Task-state reach: how many failing baseline runs would have seen the block?

E-091 adds a bounded, non-directive per-turn statement of the subtask's own
state. Before spending a run on it, this script measures its **reach** on the
cached stock baseline: for each of the 400 graded runs, would a block have been
delivered at all, and would it have shown either of the two states the two
target mechanisms need?

    (a) every required slot settled AND no write attempted yet
        -> the ``planning_defect`` population (E-089: 66 runs, 23.3%)
    (b) no slot open AND a question was asked
        -> the ``over_asking`` population (E-089: 42 runs, 14.8%)

Method, deliberately narrow: the compiler (``TaskSpec.compile``, through
``task_state_slots``) and the saved trajectories, nothing else. No model call,
no evaluator, no rubric text, no target/distraction annotation. Reward is read
only to split the 400 graded runs into passing and failing, which is what the
question asks for.

The replay is the same code the live agent runs: one shared
``render_task_state_block``, one shared ``observed_candidate_counts``, and the
same commit-tool test, so what this script predicts is what the agent would
append. State is replayed in trajectory order -- candidates accumulate from the
tool results already printed, a commit call flips the write flag for the turns
after it, and a question-shaped assistant turn flips the question flag.

What this cannot measure: whether seeing the block changes any decision. Reach
is a necessary condition for the mechanism to matter, not evidence that it
helps. Nothing here is a score claim.

Usage:
    python scripts/task_state_reach.py data/simulations/stock_dev.json
    python scripts/task_state_reach.py data/simulations/stock_dev.json --json
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import agent.vitabench_bootstrap as bootstrap  # noqa: E402

bootstrap.enable_vitabench_utf8()

from agent.adapt_agent import (  # noqa: E402
    _looks_like_a_question,
    observed_candidate_counts,
    render_task_state_block,
    task_state_slots,
)
from agent.candidate_ledger import CandidateLedger  # noqa: E402
from agent.decision import is_commit_tool  # noqa: E402
from agent.memory.proactive import (  # noqa: E402
    CONTEXT_RESOLVABLE_SLOTS,
    TOOL_FINDABLE_SLOTS,
    USER_ONLY_SLOTS,
)

# The read-only stock baseline was produced with ``--memory-type rewrite``, so
# the memory backend contributes no structured slot resolution. The replay
# therefore passes none, which makes every "settled" claim compiler-only. With
# ADAPT memory in the path more slots can settle from structured facts, so the
# reach measured here is a lower bound on the block's settled population.
MEMORY_RESOLVED: dict[str, str] = {}


def is_write_call(name: str) -> bool:
    """The same write test the offline audits use (create_/instore_*)."""
    return name.startswith("create_") or name in {
        "instore_book",
        "instore_reservation",
    }


def askable_gaps(open_slots: list[str]) -> list[str]:
    """The subset of open slots the repository's own question policy would ask.

    This is a diagnostic, **not** what the block shows: the checkpoint's block
    reports the compiler's ``unknown_slots``. A slot the compiler marked unknown
    can still be one no question should ever be spent on -- ``product`` and
    ``shop_or_service`` are found by searching, and ``address`` is already in the
    user profile (``agent.memory.proactive``). Comparing the two definitions says
    whether the reach is limited by the mechanism or by the compiler's own
    vocabulary.
    """
    return [
        slot
        for slot in open_slots
        if slot in USER_ONLY_SLOTS
        and slot not in TOOL_FINDABLE_SLOTS
        and slot not in CONTEXT_RESOLVABLE_SLOTS
    ]


def replay_run(
    messages: list[dict], instruction: str
) -> dict[str, Any]:
    """Replay one subtask's turns and record what the block would have shown."""
    slots = task_state_slots(instruction, MEMORY_RESOLVED)
    required = [slot for slot, _settled in slots]
    open_slots = [slot for slot, settled in slots if not settled]

    ledger = CandidateLedger()
    write_attempted = False
    commit_calls = 0
    write_calls = 0
    question_asked = False
    question_turns = 0
    last_assistant = ""
    turns: list[dict[str, Any]] = []

    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role == "assistant":
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                last_assistant = content
                if _looks_like_a_question(content):
                    question_asked = True
                    question_turns += 1
            for call in message.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                name = str(call.get("name") or "")
                if is_write_call(name):
                    write_calls += 1
                if is_commit_tool(name):
                    commit_calls += 1
                    write_attempted = True
        elif role == "tool":
            content = message.get("content")
            ledger.observe(
                str(message.get("name") or ""),
                content if isinstance(content, str) else "",
            )
            block = render_task_state_block(
                slots,
                candidate_counts=observed_candidate_counts(ledger),
                write_attempted=write_attempted,
                question_asked=question_asked,
            )
            if not block:
                continue
            turns.append(
                {
                    "candidate_kinds": observed_candidate_counts(ledger),
                    "candidates_total": sum(
                        observed_candidate_counts(ledger).values()
                    ),
                    "block_chars": len(block),
                    "open_slots": list(open_slots),
                    "required_slots": list(required),
                    "question_asked": question_asked,
                    "write_attempted": write_attempted,
                }
            )

    return {
        "required_slots": required,
        "open_slots": open_slots,
        "turns": turns,
        "write_calls": write_calls,
        "commit_calls": commit_calls,
        "question_turns": question_turns,
        "last_turn_is_a_question": bool(
            last_assistant and _looks_like_a_question(last_assistant)
        ),
    }


def _run_rows(checkpoint: dict, tasks_by_id: dict[str, Any]):
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
            if reward is None:
                continue
            yield {
                "task_id": sim.get("task_id"),
                "trial": sim.get("trial"),
                "subtask_idx": index,
                "reward": float(reward),
                "instruction": subtasks[index].instruction or "",
                "messages": traj.get("messages") or [],
            }


def analyse(checkpoint: dict, tasks_by_id: dict[str, Any]) -> dict[str, Any]:
    graded = 0
    failing = 0
    delivered = 0
    block_turns = 0

    no_write_call = 0
    no_open_slot = 0
    no_required_slots = 0
    no_askable_gap = 0
    no_askable_gap_and_no_write = 0
    last_turn_asked = 0
    over_asking_shape = 0
    open_slot_frequency: dict[str, int] = {}

    a_runs = 0
    a_strict_runs = 0
    a_no_requirement_runs = 0
    b_runs = 0
    b_last_turn_runs = 0
    b_no_requirement_runs = 0
    either_runs = 0

    for row in _run_rows(checkpoint, tasks_by_id):
        graded += 1
        if row["reward"] == 1.0:
            continue
        failing += 1
        state = replay_run(row["messages"], row["instruction"])
        turns = state["turns"]
        if turns:
            delivered += 1
            block_turns += len(turns)
        if state["write_calls"] == 0:
            no_write_call += 1
        if not state["open_slots"]:
            no_open_slot += 1
        if not state["required_slots"]:
            no_required_slots += 1
        for slot in state["open_slots"]:
            open_slot_frequency[slot] = open_slot_frequency.get(slot, 0) + 1
        if not askable_gaps(state["open_slots"]):
            no_askable_gap += 1
            if state["write_calls"] == 0:
                no_askable_gap_and_no_write += 1
        if state["last_turn_is_a_question"]:
            last_turn_asked += 1
        if state["last_turn_is_a_question"] and not state["open_slots"]:
            over_asking_shape += 1

        # (a) the block showed every required slot settled and no write yet.
        a_turn = any(
            not turn["open_slots"]
            and not turn["write_attempted"]
            and turn["required_slots"]
            for turn in turns
        )
        a_any_turn = any(
            not turn["open_slots"] and not turn["write_attempted"] for turn in turns
        )
        # (b) the block showed no slot open and a question already asked.
        b_turn = any(
            not turn["open_slots"]
            and turn["question_asked"]
            and turn["required_slots"]
            for turn in turns
        )
        b_any_turn = any(
            not turn["open_slots"] and turn["question_asked"] for turn in turns
        )
        if a_turn:
            a_runs += 1
            if state["write_calls"] == 0:
                a_strict_runs += 1
        if a_any_turn:
            a_no_requirement_runs += 1
        if b_turn:
            b_runs += 1
            if state["last_turn_is_a_question"]:
                b_last_turn_runs += 1
        if b_any_turn:
            b_no_requirement_runs += 1
        if a_turn or b_turn:
            either_runs += 1

    return {
        "graded_runs": graded,
        "failing_runs": failing,
        "passing_runs": graded - failing,
        "block_delivery": {
            "failing_runs_with_at_least_one_block": delivered,
            "total_blocks_appended": block_turns,
        },
        "trajectory_only_populations": {
            "failing_runs_with_no_write_call": no_write_call,
            "failing_runs_with_no_open_slot": no_open_slot,
            "failing_runs_whose_instruction_requires_no_slot": no_required_slots,
            "failing_runs_whose_last_assistant_turn_is_a_question": last_turn_asked,
            "failing_runs_matching_the_over_asking_shape": over_asking_shape,
        },
        "diagnostic_askable_gap_definition": {
            "failing_runs_with_no_askable_gap": no_askable_gap,
            "of_which_no_write_call": no_askable_gap_and_no_write,
            "note": (
                "Not what the block shows. This counts open slots after applying "
                "the question policy's own tool-findable / context-resolvable "
                "filter, i.e. the slots the repository would actually spend a "
                "question on. It separates 'the mechanism reached too few runs' "
                "from 'the compiler's slot vocabulary left the slot open'."
            ),
        },
        "open_slot_frequency": dict(
            sorted(open_slot_frequency.items(), key=lambda item: -item[1])
        ),
        "reach": {
            "a_all_slots_settled_and_no_write_yet": a_runs,
            "a_of_which_the_run_never_issued_a_write_call": a_strict_runs,
            "a_without_the_requires_at_least_one_slot_guard": a_no_requirement_runs,
            "b_no_slot_open_and_a_question_already_asked": b_runs,
            "b_of_which_the_last_assistant_turn_is_a_question": b_last_turn_runs,
            "b_without_the_requires_at_least_one_slot_guard": b_no_requirement_runs,
            "either_a_or_b": either_runs,
        },
        "e089_reference": {
            "planning_defect": 66,
            "over_asking": 42,
            "note": "E-089 primary labels on the same checkpoint; not recomputed here.",
        },
        "note": (
            "Reach, not effect. The block is delivered on tool results only, so a "
            "failing run with no tool result gets no block at all. Memory supplies "
            "no resolved slots in this replay (the baseline used rewrite memory), so "
            "the settled population measured here is a lower bound. 'A question was "
            "asked' is read from question-shaped assistant turns "
            "(_looks_like_a_question), the same observer the repository already "
            "uses, because the stock baseline has no proactive engine and therefore "
            "no committed-question record."
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

    reach = report["reach"]
    delivery = report["block_delivery"]
    populations = report["trajectory_only_populations"]
    diagnostic = report["diagnostic_askable_gap_definition"]
    failing = report["failing_runs"]
    print(f"checkpoint: {args.checkpoint}")
    print(
        f"graded runs: {report['graded_runs']}   failing: {failing}   "
        f"passing: {report['passing_runs']}"
    )
    print()
    print("block delivery (failing runs)")
    print(
        f"  runs where >=1 block would be appended: "
        f"{delivery['failing_runs_with_at_least_one_block']} / {failing}"
    )
    print(f"  total blocks appended:                 {delivery['total_blocks_appended']}")
    print()
    print("populations read from trajectories only (no rubric, no target ids)")
    print(f"  no write call at all:                  {populations['failing_runs_with_no_write_call']}")
    print(f"  no open slot:                          {populations['failing_runs_with_no_open_slot']}")
    print(f"  instruction requires no slot:          {populations['failing_runs_whose_instruction_requires_no_slot']}")
    print(f"  last assistant turn is a question:     {populations['failing_runs_whose_last_assistant_turn_is_a_question']}")
    print(f"  last turn a question and no open slot: {populations['failing_runs_matching_the_over_asking_shape']}")
    print()
    print("state-based reach (the block literally showed this)")
    print(
        f"  (a) every required slot settled, no write attempted yet: "
        f"{reach['a_all_slots_settled_and_no_write_yet']}"
    )
    print(
        f"      ... and the run never issued a write call at all (planning_defect "
        f"shape): {reach['a_of_which_the_run_never_issued_a_write_call']}"
    )
    print(
        f"  (b) no slot open and a question already asked:           "
        f"{reach['b_no_slot_open_and_a_question_already_asked']}"
    )
    print(
        f"      ... and the run's last turn is a question (over_asking "
        f"shape): {reach['b_of_which_the_last_assistant_turn_is_a_question']}"
    )
    print(f"  either (a) or (b):                                      {reach['either_a_or_b']}")
    print()
    print("diagnostic: what limits the reach")
    print(f"  open-slot frequency over failing runs: {report['open_slot_frequency']}")
    print(
        f"  failing runs with no *askable* gap (question-policy definition, NOT "
        f"what the block shows): {diagnostic['failing_runs_with_no_askable_gap']}"
    )
    print(
        f"      ... of which no write call at all: "
        f"{diagnostic['of_which_no_write_call']}"
    )
    print()
    print(
        "E-089 reference on this checkpoint: planning_defect 66, over_asking 42 "
        "(primary labels, not recomputed here)."
    )
    print()
    print(report["note"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

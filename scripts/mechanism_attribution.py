"""Mechanism-level attribution of the baseline's failing trajectories.

The baseline checkpoint is `--agent stock --memory-type rewrite`, so ADAPT's own
data layer is NOT in its path: every failure here is the stock skeleton's. The
point of attributing them to a *mechanism* is therefore not to blame our code but
to say which capability our agent has to do better, and where.

Each failing run is assigned one primary mechanism using mechanical signals only
(tool names, argument ids, result text, termination reason), and the report prints
the distribution plus short real excerpts so the attribution can be eyeballed
rather than trusted.

Mechanisms, mapped to the benchmark's stated capabilities:

    container_observed_not_opened   personalization: the right container was in
                                    hand and the model did not use it
    wrong_candidate_bound           personalization: wrote/ordered a different
                                    observed candidate
    retrieval_miss                  personalization: nothing target-related was
                                    ever printed, so no choice was possible
    asked_and_abandoned             proactiveness: no write, last turn asks
    tool_argument_rejected          tool robustness: a write came back rejected
    budget_exhausted                long-horizon control: hit max_steps

Reads target annotations for offline attribution only; the agent never sees them
and the report keeps to counts, ids shapes and message excerpts.

Usage:
    python scripts/mechanism_attribution.py data/simulations/stock_dev.json
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent.vitabench_bootstrap as bootstrap  # noqa: E402

bootstrap.enable_vitabench_utf8()

from agent.decision import TaskSpec  # noqa: E402
from scripts.candidate_selection_audit import observed_ids  # noqa: E402
from scripts.target_reachability import (  # noqa: E402
    bound_ids,
    is_write_tool,
    rank_of,
    result_records,
    target_ids,
)

_ERROR_MARKERS = (
    "not found",
    "invalid",
    "cannot",
    "failed",
    "error",
    "不存在",
    "无效",
    "失败",
    "无法",
)
_ID_LIKE = re.compile(r"^[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+$")

MECHANISM_TO_CAPABILITY = {
    "tool_hallucination": "tool calling / grounding",
    "dead_loop_no_recovery": "long-horizon control",
    "state_loss": "personalization / long-term memory",
    "transaction_left_unfinished": "execution / commitment",
    "missed_question": "proactiveness (under-asking)",
    "over_asking": "proactiveness (over-asking)",
    "planning_defect": "planning / task decomposition",
    "unattributed": "-",
}

# Every label above names a GENERAL logic gap, never a vocabulary gap. The
# forbidden attribution is "this task's keyword did not match"; the required one
# is "the agent lacks general logic for this kind of ambiguous reference". A run
# is never labelled by what words it contained, only by what the agent did with
# information it had: re-used an id nobody printed, repeated a call until the
# budget ran out, held the answer and bound another candidate, decided while a
# slot was still open, asked while nothing was open, or stopped without acting.


def _last_assistant_text(messages: list[dict]) -> str:
    for message in reversed(messages):
        if message.get("role") == "assistant":
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
    return ""


def analyse(checkpoint: dict, tasks_by_id: dict[str, Any]) -> dict[str, Any]:
    counts: collections.Counter = collections.Counter()
    examples: dict[str, list[str]] = collections.defaultdict(list)
    total = 0
    failing = 0

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
            total += 1
            if float(reward) == 1.0:
                continue
            failing += 1

            messages = traj.get("messages") or []
            environment = subtasks[index].environment or {}
            entities, products, hosts = target_ids(environment)
            instruction = subtasks[index].instruction or ""

            writes: list[str] = []
            signatures: list[str] = []
            paid = False
            write_rejected = False
            for message in messages:
                if message.get("role") == "assistant":
                    for call in message.get("tool_calls") or []:
                        if not isinstance(call, dict):
                            continue
                        name = str(call.get("name") or "")
                        if "pay" in name.lower():
                            paid = True
                        try:
                            payload = json.dumps(
                                call.get("arguments") or {},
                                sort_keys=True,
                                ensure_ascii=False,
                            )
                        except (TypeError, ValueError):
                            payload = str(call.get("arguments"))
                        signatures.append(f"{name}:{payload}")
                        if is_write_tool(name):
                            writes.append(name)
                elif message.get("role") == "tool" and writes:
                    head = (message.get("content") or "").strip().casefold()[:160]
                    if any(marker in head for marker in _ERROR_MARKERS):
                        write_rejected = True

            observed = observed_ids(messages)

            target_printed = False
            host_rank: int | None = None
            if products or entities:
                for message in messages:
                    if message.get("role") != "tool":
                        continue
                    records = result_records(message.get("content"))
                    for target in products:
                        if rank_of(target, records) is not None:
                            target_printed = True
                    for target in entities:
                        if rank_of(target, records) is not None:
                            target_printed = True
                    for host in hosts or ():
                        rank = rank_of(host, records)
                        if rank is not None and (host_rank is None or rank < host_rank):
                            host_rank = rank

            bound = bound_ids(messages)
            bound_target = bool((products | entities) & bound)
            bound_other = any(
                _ID_LIKE.match(value) and value not in (products | entities)
                for value in bound
            )
            last = _last_assistant_text(messages)
            asked = any(m in last for m in ("？", "?", "吗", "呢"))

            # An id the agent passed to a write that no tool result ever
            # printed. It came from nowhere observable -- the E-035 class.
            hallucinated = [
                value
                for value in bound
                if _ID_LIKE.match(value) and value not in observed
            ]
            counts_sig = collections.Counter(signatures)
            repeated = max(counts_sig.values()) if counts_sig else 0
            spec = TaskSpec.compile(instruction)
            open_slots = list(spec.unknown_slots or [])

            # The six categories are general-logic gaps, never "this task's
            # keyword did not match". Each test is about what the agent did with
            # information it had, not about the vocabulary of one case.
            if hallucinated:
                mechanism = "tool_hallucination"
            elif repeated >= 3:
                mechanism = "dead_loop_no_recovery"
            elif target_printed and writes and (bound_other or not bound_target):
                mechanism = "state_loss"
            elif writes and (bound_target or not (products | entities)) and not paid:
                # The right candidate was bound and an order exists, but the run
                # never completed the transaction: the agent handed the final
                # step back to the user ("要现在支付吗？", "那你自己支付就行") and
                # the user left. A commit instruction already carried the
                # authorisation, so this is a general logic gap about finishing,
                # not about vocabulary.
                mechanism = "transaction_left_unfinished"
            elif open_slots and not asked:
                mechanism = "missed_question"
            elif asked and not open_slots:
                mechanism = "over_asking"
            elif not writes:
                mechanism = "planning_defect"
            else:
                mechanism = "unattributed"

            counts[mechanism] += 1
            if len(examples[mechanism]) < 3:
                examples[mechanism].append(
                    f"{subtasks[index].domain} | target_printed={target_printed} "
                    f"host_rank={host_rank} writes={sorted(set(writes))} "
                    f"bound_target={bound_target}\n"
                    f"      instr: {instruction[:52]}\n"
                    f"      last : {last[:88]}"
                )

    return {
        "graded_runs": total,
        "failing_runs": failing,
        "pass_rate": round((total - failing) / total, 4) if total else 0.0,
        "attribution": [
            {
                "mechanism": mechanism,
                "capability": MECHANISM_TO_CAPABILITY.get(mechanism, "-"),
                "runs": runs,
                "share_of_failures": round(runs / failing, 4) if failing else 0.0,
                "impact_ceiling_on_avg4": round(runs * 0.0025, 4),
            }
            for mechanism, runs in counts.most_common()
        ],
        "examples": {key: value for key, value in examples.items()},
        "note": (
            "Mechanical and primary-label only: each failing run is assigned the "
            "first mechanism whose test passes, in a fixed order, so the classes "
            "are mutually exclusive. 'impact_ceiling_on_avg4' assumes every run in "
            "the class is rescued, which no intervention achieves."
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

    print(f"graded runs {report['graded_runs']}  failing {report['failing_runs']}  "
          f"pass {report['pass_rate']:.1%}")
    print()
    header = f"{'mechanism':<34}{'capability':<28}{'runs':>6}{'share':>8}{'ceiling':>9}"
    print(header)
    print("-" * len(header))
    for row in report["attribution"]:
        print(
            f"{row['mechanism']:<34}{row['capability']:<28}{row['runs']:>6}"
            f"{row['share_of_failures']:>8.1%}{row['impact_ceiling_on_avg4']:>9.4f}"
        )
    print()
    for mechanism, lines in report["examples"].items():
        print(f"--- {mechanism} ---")
        for line in lines:
            print(f"    {line}")
    print()
    print(report["note"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

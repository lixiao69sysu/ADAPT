"""Headroom of an executable constraint Filter, measured zero-model.

The mechanism under test is the one SCOPE (ACL 2026, "Programming over Thinking")
and RunAgent describe: stop asking the model to satisfy constraints by attention
over a long printed candidate list, and instead compile the constraints into a
deterministic Filter that eliminates violating candidates before a choice is
bound.

This audit measures the mechanism's **headroom**, not its effect. For every
failing run whose write bound an observed NON-target candidate while the target
was also printed, it asks:

    is there a condition, named by something the agent can read, that the target
    SATISFIES and the wrongly-bound candidate VIOLATES?

Only then does a Filter remove the wrong candidate without also removing the
right one. Direction matters, so the test is explicitly two-sided:

    require  V named in the instruction, V printed on the target, not on the
             wrong candidate  -> a require-Filter keeps the target
    forbid   V named by a negative-polarity memory fact, printed on the wrong
             candidate, not on the target -> a forbid-Filter drops the wrong one

A value that only appears among the user's *positive* facts is a remembered
preference or a repeat purchase. Ordering from the same store again is not a
condition the instruction imposes, and filtering on it is the governance pattern
E-042 measured net negative, so those runs are counted separately and excluded.

Target annotations are read for **scoring this audit only**; the mechanism never
sees them. The anchors are the instruction text and the replayed fact store.

Usage:
    python scripts/oracle_filter_headroom.py data/simulations/adapt8_1t.json
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
from agent.memory.adapt_memory import ADAPTMemory  # noqa: E402
from agent.runtime.alignment import _normalize  # noqa: E402
from scripts.target_reachability import is_write_tool, target_ids  # noqa: E402

UNIT_VALUE = 0.0025


def _printed_values(candidate: Any) -> set[str]:
    """Normalized attribute values printed on one candidate row."""
    values: set[str] = set()
    for _key, value in (getattr(candidate, "attributes", None) or {}).items():
        normalized = _normalize(str(value or ""))
        if len(normalized) >= 2 and not normalized.isdigit():
            values.add(normalized)
    return values


def _pairs(candidate: Any) -> set[tuple[str, str]]:
    """(key, normalized value) pairs printed on one candidate row."""
    pairs: set[tuple[str, str]] = set()
    for key, value in (getattr(candidate, "attributes", None) or {}).items():
        normalized = _normalize(str(value or ""))
        if len(normalized) >= 2 and not normalized.isdigit():
            pairs.add((str(key), normalized))
    name = _normalize(str(getattr(candidate, "name", "") or ""))
    if len(name) >= 2:
        pairs.add(("name", name))
    return pairs


def _text_of(candidate: Any) -> str:
    """Everything normalized that this row printed, for containment tests."""
    parts = [
        str(getattr(candidate, "name", "") or ""),
        str(getattr(candidate, "raw", "") or ""),
    ]
    for _key, value in (getattr(candidate, "attributes", None) or {}).items():
        parts.append(str(value or ""))
    return _normalize(" ".join(parts))


def _conditions(
    ledger: CandidateLedger,
    instruction: str,
    negative_values: set[str],
    positive_values: set[str],
) -> list[tuple[str, str]]:
    """(kind, value) pairs named by an agent-readable source and printed somewhere."""
    instruction_norm = _normalize(instruction)
    found: set[tuple[str, str]] = set()
    for candidate in ledger.candidates.values():
        for value in _printed_values(candidate):
            if value in instruction_norm:
                found.add(("require", value))
            if value in negative_values:
                found.add(("forbid", value))
            if value in positive_values:
                found.add(("soft", value))
    return sorted(found)


def analyse(checkpoint: dict, tasks_by_id: dict[str, Any]) -> dict[str, Any]:
    buckets: collections.Counter = collections.Counter()
    examples: dict[str, list[str]] = collections.defaultdict(list)
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
        negative_values: set[str] = set()
        positive_values: set[str] = set()

        for index, subtask in enumerate(task.subtasks):
            instruction = subtask.instruction or ""
            memory.update(list(getattr(subtask, "interactions", None) or []))
            memory.begin_subtask(instruction)
            for fact in memory.facts:
                if getattr(fact, "status", "") != "active":
                    continue
                normalized = _normalize(fact.value)
                if len(normalized) < 2:
                    continue
                if fact.polarity == "negative":
                    negative_values.add(normalized)
                else:
                    positive_values.add(normalized)

            traj = trajectories.get(index)
            reward = rewards.get(f"subtask_{index}_reward")
            if traj is None or reward is None or float(reward) == 1.0:
                continue

            entities, products, _hosts = target_ids(
                getattr(subtask, "environment", None) or {}
            )
            targets = products | entities
            if not targets:
                continue

            ledger = CandidateLedger()
            bound_wrong: list[Any] = []
            bound_target: list[Any] = []
            wrote = False
            for message in traj.get("messages") or []:
                if message.get("role") == "tool":
                    ledger.observe(
                        str(message.get("name") or ""), message.get("content")
                    )
                    continue
                if message.get("role") != "assistant":
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
                    for candidate in ledger.constraint_candidates(arguments):
                        if candidate.candidate_id in targets:
                            bound_target.append(candidate)
                        else:
                            bound_wrong.append(candidate)

            if not wrote:
                continue
            if not bound_wrong:
                buckets[
                    "target_bound__out_of_scope"
                    if bound_target
                    else "no_id_like_binding__filter_cannot_act"
                ] += 1
                continue

            target_rows = [
                candidate
                for candidate in ledger.candidates.values()
                if candidate.candidate_id in targets
            ]
            if not target_rows:
                buckets["target_printed_but_row_unparsed"] += 1
                continue

            target_text = " ".join(_text_of(row) for row in target_rows)
            conditions = _conditions(
                ledger, instruction, negative_values, positive_values
            )

            verdict = "unobservable_difference"
            detail = ""
            for wrong in bound_wrong:
                wrong_text = _text_of(wrong)
                require_hits: list[str] = []
                forbid_hits: list[str] = []
                soft_hits: list[str] = []
                for kind, value in conditions:
                    in_target = value in target_text
                    in_wrong = value in wrong_text
                    if kind == "require" and in_target and not in_wrong:
                        require_hits.append(f"require:{value}")
                    elif kind == "forbid" and in_wrong and not in_target:
                        forbid_hits.append(f"forbid:{value}")
                    elif kind == "soft" and in_target and not in_wrong:
                        soft_hits.append(f"soft:{value}")
                    elif kind == "soft" and in_wrong and not in_target:
                        soft_hits.append(f"soft-against:{value}")
                if require_hits:
                    verdict = "instruction_require__filterable"
                    detail = f"{wrong.candidate_id}: " + ", ".join(require_hits[:3])
                    break
                if forbid_hits:
                    verdict = "memory_forbid__filterable"
                    detail = f"{wrong.candidate_id}: " + ", ".join(forbid_hits[:3])
                    continue
                if soft_hits and verdict == "unobservable_difference":
                    verdict = "preference_anchored__soft"
                    detail = f"{wrong.candidate_id}: " + ", ".join(soft_hits[:3])
                if not require_hits and not forbid_hits and not soft_hits:
                    target_pairs: set[tuple[str, str]] = set()
                    for row in target_rows:
                        target_pairs |= _pairs(row)
                    only_wrong = sorted(_pairs(wrong) - target_pairs)
                    detail = (
                        f"{wrong.candidate_id} differs only in "
                        + ", ".join(f"{k}={v}" for k, v in only_wrong[:4])
                    )

            buckets[verdict] += 1
            if len(examples[verdict]) < 6 and detail:
                examples[verdict].append(
                    f"{user_id} sub{index} | instr: {instruction[:44]}\n"
                    f"      {detail}"
                )

    hard_reachable = (
        buckets["instruction_require__filterable"]
        + buckets["memory_forbid__filterable"]
    )
    soft_reachable = buckets["preference_anchored__soft"]
    return {
        "instruction_require__filterable": buckets[
            "instruction_require__filterable"
        ],
        "memory_forbid__filterable": buckets["memory_forbid__filterable"],
        "preference_anchored__soft": soft_reachable,
        "unobservable_difference": buckets["unobservable_difference"],
        "no_id_like_binding__filter_cannot_act": buckets[
            "no_id_like_binding__filter_cannot_act"
        ],
        "target_bound__out_of_scope": buckets["target_bound__out_of_scope"],
        "target_printed_but_row_unparsed": buckets["target_printed_but_row_unparsed"],
        "reachable_hard_only": hard_reachable,
        "reachable_including_soft": hard_reachable + soft_reachable,
        "headroom_hard_only": round(hard_reachable * UNIT_VALUE, 4),
        "headroom_including_soft": round(
            (hard_reachable + soft_reachable) * UNIT_VALUE, 4
        ),
        "examples": {key: value for key, value in examples.items()},
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
    print("  failing runs whose write bound an observed NON-target:")
    for key in (
        "instruction_require__filterable",
        "memory_forbid__filterable",
        "preference_anchored__soft",
        "unobservable_difference",
        "no_id_like_binding__filter_cannot_act",
        "target_bound__out_of_scope",
        "target_printed_but_row_unparsed",
    ):
        print(f"    {key:<40}{report[key]}")
    print()
    print(
        f"  {'reachable (hard only)':<40}{report['reachable_hard_only']}"
        f"   headroom {report['headroom_hard_only']}"
    )
    print(
        f"  {'reachable (including preferences)':<40}"
        f"{report['reachable_including_soft']}"
        f"   headroom {report['headroom_including_soft']}"
    )
    print()
    for verdict, lines in report["examples"].items():
        print(f"--- {verdict} ---")
        for line in lines:
            print(f"    {line}")
    print()
    print(
        "Headroom, not effect: it assumes a perfect compiler and a perfect "
        "Filter, and that rescuing the run turns it into a full pass. "
        "'unobservable_difference' is the part NO constraint Filter can reach, "
        "because only the rubric knows which candidate was right."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

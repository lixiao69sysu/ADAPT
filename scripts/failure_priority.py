"""Failure attribution ranked by priority = frequency x impact x addressability.

Reads the stock dev-cohort checkpoint and decomposes every failing subtask run into
measurable failure *flags*, then ranks the flags by

    priority = frequency x impact x addressability

Two of the three factors are measured here; the third is a judgement and is kept
visibly separate:

frequency      counted from the 400 saved subtask-trial records.
impact         upper bound on the official Avg@4 if *every* record carrying the
               flag were rescued. One rescued subtask-trial moves its official
               unit's trial mean by 1/4 and the unit weighs 1/100, so one record
               is worth 0.0025. This is a ceiling, not an expectation: it assumes
               100% rescue, which no intervention achieves.
addressability  a 0-1 judgement with a cited reason. It is NOT measured. It is
               grounded in the interventions this project actually tried and the
               engineering-log entries that falsified or bounded them.

Flags are not mutually exclusive; a run can thrash and also never see its target.
Co-occurrence is reported rather than hidden by forcing one primary label.

Usage:
    python scripts/failure_priority.py data/simulations/stock_dev.json
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

from scripts.target_reachability import (  # noqa: E402
    bound_ids,
    is_write_tool,
    rank_of,
    result_records,
    target_ids,
)

ONE_RESCUED_TRIAL = 0.0025  # official Avg@4 weight of one subtask-trial record

_ERROR_MARKERS = (
    "not found",
    "invalid",
    "cannot",
    "can not",
    "failed",
    "error",
    "不存在",
    "无效",
    "失败",
    "无法",
)
_ORDER_RE = re.compile(r"Order\(order_id")
_PAY_TOOL_RE = re.compile(r"pay|payment", re.IGNORECASE)


def _signature(name: str, arguments: Any) -> str:
    try:
        payload = json.dumps(arguments or {}, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        payload = str(arguments)
    return f"{name}:{payload}"


def _looks_like_an_error(content: str) -> bool:
    head = (content or "").strip().casefold()[:160]
    if not head:
        return False
    return any(marker in head for marker in _ERROR_MARKERS)


# Addressability: a judgement, with the evidence that grounds it. Deliberately
# conservative -- every large class this project could already reach got tested
# and came back bounded or infeasible.
ADDRESSABILITY: dict[str, tuple[float, str]] = {
    "write_argument_rejected": (
        0.6,
        "Deterministic: the environment states why the write failed. tool_recovery.py "
        "was built for exactly this and is archived, not deleted (E-020, E-051). "
        "Never wired, so the ceiling is untested.",
    ),
    "order_created_for_the_target_but_unpaid": (
        0.5,
        "Correct candidate, payment deferred. Mechanical state transition, so the fix "
        "is auditable. Measured small: only 8 runs, so impact is +0.02 at most. "
        "E-019 records the framework previously misreading this class.",
    ),
    "order_created_for_another_candidate": (
        0.15,
        "Measured subclass of choosing the wrong candidate: 83 of the 91 'unpaid' runs "
        "built an order for a non-target id. Shares the low addressability of the "
        "selection class (E-066/E-067 zero refutations, E-074 4.5% reach).",
    ),
    "thrash_frozen_repeat": (
        0.2,
        "Bounded, not eliminated: E-072 measured a +0.0325 ceiling even if every "
        "thrashing trial is rescued, and the guard is a single hint. E-056 has the "
        "within-cluster contrast but not causality.",
    ),
    "target_never_printed": (
        0.15,
        "The answer never reached the model, so a data layer can only change what is "
        "searched for, not the model's query. E-062: 70/129 of the related cell sat "
        "at rank 11+.",
    ),
    "wrote_but_not_the_target": (
        0.15,
        "The candidate was in front of the model and it chose otherwise. E-066/E-067 "
        "measured the correspondence view producing zero refutations, and E-074 "
        "measured its reach at 4.5% in the frozen configuration.",
    ),
    "ended_asking_the_user": (
        0.1,
        "Every framework attempt to change asking behaviour was net negative "
        "(E-042 LOST 7 : GAINED 1, E-048, E-049, E-050, E-053). No intervention on "
        "this class currently exists.",
    ),
    "hit_max_steps": (
        0.1,
        "A budget symptom, not a cause. E-072: runaway runs scored 0.3077 against "
        "0.2931 for the rest, so the class has no measured headroom.",
    ),
}


def analyse(checkpoint: dict, tasks_by_id: dict[str, Any]) -> dict[str, Any]:
    total = 0
    failures = 0
    frequency: collections.Counter = collections.Counter()
    combinations: collections.Counter = collections.Counter()
    by_domain: dict[str, collections.Counter] = collections.defaultdict(
        collections.Counter
    )

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
            failures += 1
            domain = subtasks[index].domain
            messages = traj.get("messages") or []

            flags: set[str] = set()
            signatures: list[str] = []
            writes: list[str] = []
            write_result_error = False
            order_created = False
            paid = False
            for message in messages:
                if message.get("role") == "assistant":
                    for call in message.get("tool_calls") or []:
                        if not isinstance(call, dict):
                            continue
                        name = str(call.get("name") or "")
                        signatures.append(_signature(name, call.get("arguments")))
                        if is_write_tool(name):
                            writes.append(name)
                        if _PAY_TOOL_RE.search(name):
                            paid = True
                elif message.get("role") == "tool":
                    content = message.get("content") or ""
                    if writes and _looks_like_an_error(content):
                        write_result_error = True
                    if _ORDER_RE.search(content):
                        order_created = True

            # The environment's target must be resolved BEFORE the flags are
            # computed: the unpaid-order split depends on it, and reading it
            # afterwards silently reused the previous iteration's value.
            _entities, products, hosts = target_ids(subtasks[index].environment or {})

            counts = collections.Counter(signatures)
            if counts and max(counts.values()) >= 3:
                flags.add("thrash_frozen_repeat")
            if write_result_error:
                flags.add("write_argument_rejected")
            if order_created and not paid:
                # Split by whether the order used the right candidate. Naively
                # labelling this a "payment" class put it first in the ranking
                # with a high addressability score, and checking it collapsed the
                # class: 83 of 91 orders used a NON-target id, i.e. they are the
                # candidate-selection failure seen from another angle (E-084).
                if products and (products & bound_ids(messages)):
                    flags.add("order_created_for_the_target_but_unpaid")
                elif products:
                    flags.add("order_created_for_another_candidate")
                else:
                    flags.add("order_created_for_the_target_but_unpaid")
            if str(traj.get("termination_reason")) == "max_steps":
                flags.add("hit_max_steps")

            target_printed = False
            if products:
                for message in messages:
                    if message.get("role") != "tool":
                        continue
                    records = result_records(message.get("content"))
                    if any(rank_of(t, records) is not None for t in products):
                        target_printed = True
                        break
            if products and not target_printed:
                flags.add("target_never_printed")
            if products and target_printed and writes:
                flags.add("wrote_but_not_the_target")

            last_text = ""
            for message in reversed(messages):
                if message.get("role") == "assistant":
                    content = message.get("content")
                    if isinstance(content, str) and content.strip():
                        last_text = content.strip()
                        break
            if not writes and any(m in last_text for m in ("？", "?", "吗", "呢")):
                flags.add("ended_asking_the_user")

            if not flags:
                flags.add("unclassified")
            for flag in flags:
                frequency[flag] += 1
                by_domain[flag][domain] += 1
            combinations[" + ".join(sorted(flags))] += 1

    rows = []
    for flag, count in frequency.most_common():
        addressability, reason = ADDRESSABILITY.get(flag, (0.05, "No intervention exists for this class."))
        impact = round(count * ONE_RESCUED_TRIAL, 4)
        rows.append(
            {
                "failure_mode": flag,
                "frequency": count,
                "share_of_runs": round(count / total, 4) if total else 0.0,
                "impact_ceiling_on_avg4": impact,
                "addressability": addressability,
                "priority": round(count * impact * addressability, 6),
                "addressability_basis": reason,
                "domains": dict(by_domain[flag].most_common()),
            }
        )
    rows.sort(key=lambda row: row["priority"], reverse=True)
    return {
        "graded_subtask_runs": total,
        "failing_runs": failures,
        "pass_rate": round((total - failures) / total, 4) if total else 0.0,
        "one_rescued_trial_is_worth": ONE_RESCUED_TRIAL,
        "note": (
            "frequency and impact are measured; addressability is a JUDGEMENT with "
            "cited evidence, and is not measured. impact is a ceiling assuming 100% "
            "rescue. flags are not mutually exclusive."
        ),
        "ranked_failure_modes": rows,
        "flag_combinations_top": dict(combinations.most_common(8)),
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

    print(f"graded subtask runs : {report['graded_subtask_runs']}")
    print(f"failing runs        : {report['failing_runs']}   (pass {report['pass_rate']:.1%})")
    print(f"one rescued trial   : {report['one_rescued_trial_is_worth']}")
    print()
    header = f"{'failure mode':<28}{'freq':>6}{'share':>8}{'impact':>9}{'addr':>7}{'PRIORITY':>11}"
    print(header)
    print("-" * len(header))
    for row in report["ranked_failure_modes"]:
        print(
            f"{row['failure_mode']:<28}{row['frequency']:>6}"
            f"{row['share_of_runs']:>8.1%}{row['impact_ceiling_on_avg4']:>9.4f}"
            f"{row['addressability']:>7.2f}{row['priority']:>11.5f}"
        )
    print()
    print("basis for the addressability column:")
    for row in report["ranked_failure_modes"]:
        print(f"  {row['failure_mode']}: {row['addressability']:.2f} -- {row['addressability_basis']}")
    print()
    print(f"note: {report['note']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Zero-model autopsy of runaway and truncated turns in saved checkpoints.

Two questions answered from artifacts only:

1. Decoding: does the agent model ever hit `max_tokens`? Every assistant message
   carries the raw API response, so `finish_reason` is available per turn.
   `length` means the model was cut off mid-answer.

2. Runaway: what actually happens inside subtasks that terminate on `max_steps`?
   Are they thrashing on one repeated call, or making progress that the step
   budget simply ends?

Reads only observable trajectory fields (roles, tool names, arguments,
finish_reason). It never reads rewards, rubrics or target annotations, so it is
safe to run on any checkpoint.

Usage:
    python scripts/runaway_autopsy.py data/simulations/stock_avg4_8u.json
    python scripts/runaway_autopsy.py data/simulations/stock_avg4_8u.json --tail 30
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import pathlib
import sys
from typing import Any, Iterable

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

# The guard and this audit must name calls and results identically. Sharing one
# module is the structural defence against the E-053 failure mode, where the
# runtime guard and the offline count disagreed about message shape and the
# guard silently never fired.
from agent.tool_signature import digest_of as _digest  # noqa: E402
from agent.tool_signature import signature_of as _signature  # noqa: E402


def _tool_calls(message: dict[str, Any]) -> list[tuple[str, Any]]:
    calls = message.get("tool_calls") or []
    out: list[tuple[str, Any]] = []
    for call in calls:
        if not isinstance(call, dict):
            continue
        name = call.get("name")
        if name is None and isinstance(call.get("function"), dict):
            name = call["function"].get("name")
        arguments = call.get("arguments")
        if arguments is None and isinstance(call.get("function"), dict):
            arguments = call["function"].get("arguments")
        out.append((name or "?", arguments))
    return out


def _finish_reason(message: dict[str, Any]) -> str | None:
    raw = message.get("raw_data")
    if not isinstance(raw, dict):
        return None
    choices = raw.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    return first.get("finish_reason")


def _completion_tokens(message: dict[str, Any]) -> int:
    usage = message.get("usage")
    if isinstance(usage, dict):
        value = usage.get("completion_tokens")
        if isinstance(value, int):
            return value
    return 0


def decoding_report(simulations: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Distribution of API finish_reason and completion length per turn."""
    reasons: collections.Counter[str] = collections.Counter()
    per_task_length: dict[str, int] = collections.defaultdict(int)
    max_completion = 0
    long_turns = 0
    total_assistant = 0
    missing_raw = 0
    # A turn can only be *proven* truncated when its raw response is present and
    # says `length`. Turns whose completion length is near the cap but whose raw
    # response is absent are unproven, and are reported separately rather than
    # being folded into a clean bill of health.
    near_cap: list[dict[str, Any]] = []
    cap = 0

    for sim in simulations:
        task_id = sim.get("task_id", "?")
        for message in sim.get("messages", []):
            if message.get("role") != "assistant":
                continue
            total_assistant += 1
            reason = _finish_reason(message)
            if reason is None:
                missing_raw += 1
            reasons[reason or "unknown"] += 1
            tokens = _completion_tokens(message)
            max_completion = max(max_completion, tokens)
            if tokens >= 900:
                long_turns += 1
            if reason == "length":
                per_task_length[task_id] += 1
            if tokens >= 800:
                near_cap.append(
                    {
                        "task_id": task_id,
                        "trial": sim.get("trial"),
                        "completion_tokens": tokens,
                        "finish_reason": reason,
                        "raw_response_present": message.get("raw_data") is not None,
                        "has_tool_calls": bool(_tool_calls(message)),
                    }
                )

    near_cap.sort(key=lambda row: -row["completion_tokens"])
    if near_cap:
        cap = near_cap[0]["completion_tokens"]
    # Turns at the observed cap *with* a `length` stop are proven truncations;
    # turns at the cap with a present non-`length` reason are proven clean.
    proven_truncated = [row for row in near_cap if row["finish_reason"] == "length"]
    unproven_high = [
        row for row in near_cap if row["finish_reason"] is None and row["completion_tokens"] >= cap
    ]

    return {
        "assistant_turns": total_assistant,
        "finish_reason": dict(reasons.most_common()),
        "max_completion_tokens": max_completion,
        "turns_at_or_above_900_completion_tokens": long_turns,
        "proven_length_truncations": len(proven_truncated),
        "length_stops_by_task": dict(per_task_length),
        "turns_without_raw_response": missing_raw,
        "turns_at_cap_without_raw_response": len(unproven_high),
        "top_completion_lengths": near_cap[:12],
    }


def runaway_report(simulations: list[dict[str, Any]], tail: int) -> dict[str, Any]:
    """Describe every max_steps subtask: thrash signature vs forward progress."""
    runs = [s for s in simulations if s.get("termination_reason") == "max_steps"]
    normal = [s for s in simulations if s.get("termination_reason") != "max_steps"]

    def mean_len(group: list[dict[str, Any]]) -> float:
        if not group:
            return 0.0
        return sum(len(s.get("messages", [])) for s in group) / len(group)

    detail = []
    for sim in runs:
        messages = sim.get("messages", [])
        signatures = [
            _signature(name, args)
            for message in messages
            if message.get("role") == "assistant"
            for name, args in _tool_calls(message)
        ]
        counts = collections.Counter(signatures)
        worst_sig, worst_n = (counts.most_common(1)[0] if counts else ("-", 0))

        window = messages[-tail:]
        window_sigs = [
            _signature(name, args)
            for message in window
            if message.get("role") == "assistant"
            for name, args in _tool_calls(message)
        ]
        window_counts = collections.Counter(window_sigs)
        window_tools = [name for name, _ in [
            call
            for message in window
            if message.get("role") == "assistant"
            for call in _tool_calls(message)
        ]]
        tool_errors = sum(
            1
            for message in window
            if message.get("role") == "tool"
            and _looks_like_error(message.get("content"))
        )
        tool_results = sum(1 for message in window if message.get("role") == "tool")

        detail.append(
            {
                "task_id": sim.get("task_id"),
                "trial": sim.get("trial"),
                "messages": len(messages),
                "tool_calls": len(signatures),
                "distinct_calls": len(counts),
                "repeat_ratio": round(1 - len(counts) / len(signatures), 3) if signatures else 0.0,
                "worst_repeat": [worst_sig, worst_n],
                "tail_distinct_calls": len(window_counts),
                "tail_most_repeated": window_counts.most_common(1)[0] if window_counts else None,
                "tail_tool_sequence": window_tools,
                "tail_tool_results": tool_results,
                "tail_tool_errors": tool_errors,
                "tail_error_rate": round(tool_errors / tool_results, 3) if tool_results else None,
            }
        )

    return {
        "max_steps_runs": len(runs),
        "normal_runs": len(normal),
        "mean_messages_max_steps": round(mean_len(runs), 1),
        "mean_messages_normal": round(mean_len(normal), 1),
        "share_of_runs": round(len(runs) / len(simulations), 3) if simulations else 0.0,
        "detail": sorted(detail, key=lambda row: -row["messages"]),
    }


def _looks_like_error(content: Any) -> bool:
    if not isinstance(content, str):
        return False
    head = content[:200].lower()
    markers = ("error", "failed", "invalid", "not found", "cannot", "unable", "exception")
    return any(marker in head for marker in markers)


def repeat_result_identity(
    simulations: list[dict[str, Any]], signatures: list[str]
) -> dict[str, Any]:
    """Do repeated identical calls return identical results?

    This is the decisive question for any anti-thrash guard. A repeated call
    whose result never changes carries no new information, so refusing it cannot
    remove information the model needs. A repeated call whose result *does*
    change is polling a live value and must not be blocked.
    """
    wanted = set(signatures)
    found: dict[str, list[tuple[str, int]]] = {sig: [] for sig in wanted}

    for sim in simulations:
        if sim.get("termination_reason") != "max_steps":
            continue
        pending: list[str] = []
        for message in sim.get("messages", []):
            role = message.get("role")
            if role == "assistant":
                pending = [_signature(name, args) for name, args in _tool_calls(message)]
            elif role == "tool":
                content = message.get("content")
                blob = content if isinstance(content, str) else repr(content)
                digest = _digest(content)
                for sig in pending:
                    if sig in wanted:
                        found[sig].append((digest, len(blob)))
                pending = []

    report: dict[str, Any] = {}
    for sig, results in found.items():
        digests = [digest for digest, _ in results]
        distinct = list(dict.fromkeys(digests))
        report[sig] = {
            "occurrences": len(digests),
            "distinct_results": len(distinct),
            "result_frozen": len(distinct) == 1 and len(digests) > 1,
            "result_digests": digests,
            "result_lengths": sorted({length for _, length in results}),
        }
    return report


def show_frozen(
    simulations: list[dict[str, Any]], signatures: list[str], width: int
) -> dict[str, Any]:
    """Concrete trace unit: the arguments and the frozen result of a thrash call."""
    wanted = set(signatures)
    out: dict[str, Any] = {sig: None for sig in wanted}
    for sim in simulations:
        if sim.get("termination_reason") != "max_steps":
            continue
        pending: list[tuple[str, Any]] = []
        for message in sim.get("messages", []):
            role = message.get("role")
            if role == "assistant":
                pending = [
                    (_signature(name, args), args) for name, args in _tool_calls(message)
                ]
            elif role == "tool":
                for sig, args in pending:
                    if sig in wanted and out[sig] is None:
                        content = message.get("content")
                        blob = content if isinstance(content, str) else repr(content)
                        out[sig] = {
                            "task_id": sim.get("task_id"),
                            "trial": sim.get("trial"),
                            "arguments": args,
                            "result_repr": blob[:width],
                            "result_length": len(blob),
                        }
                pending = []
    return out


def reward_by_termination(simulations: list[dict[str, Any]]) -> dict[str, Any]:
    """What does a runaway actually cost? Reward grouped by termination reason.

    Offline trace analysis only: the agent never reads rewards.
    """
    groups: dict[str, list[float]] = collections.defaultdict(list)
    for sim in simulations:
        info = sim.get("reward_info") or {}
        reward = info.get("reward")
        if reward is None:
            continue
        groups[sim.get("termination_reason") or "?"].append(float(reward))

    out: dict[str, Any] = {}
    for reason, values in groups.items():
        out[reason] = {
            "units": len(values),
            "mean_reward": round(sum(values) / len(values), 4),
            "zeros": sum(1 for value in values if value == 0.0),
        }
    total = sum(len(values) for values in groups.values())
    out["_overall"] = {
        "units": total,
        "mean_reward": round(
            sum(sum(values) for values in groups.values()) / total, 4
        )
        if total
        else 0.0,
    }
    return out


def frozen_ceiling(simulations: list[dict[str, Any]]) -> dict[str, Any]:
    """Upper bound if every max_steps unit scored at the non-runaway mean."""
    runaway = [
        s for s in simulations if s.get("termination_reason") == "max_steps"
    ]
    normal = [
        s
        for s in simulations
        if s.get("termination_reason") != "max_steps" and (s.get("reward_info") or {}).get("reward") is not None
    ]
    if not normal or not runaway:
        return {"units": len(runaway), "upper_bound_delta": 0.0}
    normal_mean = sum(
        float((s.get("reward_info") or {})["reward"]) for s in normal
    ) / len(normal)
    all_sims = [s for s in simulations if (s.get("reward_info") or {}).get("reward") is not None]
    baseline = sum(float((s.get("reward_info") or {})["reward"]) for s in all_sims) / len(all_sims)
    recovered = sum(
        normal_mean if (s.get("reward_info") or {}).get("reward") == 0.0 else float((s.get("reward_info") or {})["reward"])
        for s in runaway
    )
    projected = (
        sum(float((s.get("reward_info") or {})["reward"]) for s in all_sims)
        - sum(float((s.get("reward_info") or {})["reward"]) for s in runaway)
        + recovered
    ) / len(all_sims)
    return {
        "runaway_units": len(runaway),
        "runaway_share": round(len(runaway) / len(all_sims), 4),
        "runaway_mean_reward": round(
            sum(float((s.get("reward_info") or {})["reward"]) for s in runaway) / len(runaway), 4
        ),
        "non_runaway_mean_reward": round(normal_mean, 4),
        "baseline_mean": round(baseline, 4),
        "upper_bound_mean": round(projected, 4),
        "upper_bound_delta": round(projected - baseline, 4),
    }


def subtask_thrash_report(
    simulations: list[dict[str, Any]], repeat_threshold: int
) -> dict[str, Any]:
    """Thrash at the L1 unit (one subtask), which is the loop's real unit.

    A subtask is `thrashing` when some single call signature is issued at least
    `repeat_threshold` times AND every occurrence yielded an identical result.
    Result identity is part of the definition: a repeated call whose result
    changes is polling a live value, not thrashing.

    Grade is read from `subtask_rewards` for offline attribution only.
    """
    rows: list[dict[str, Any]] = []
    for sim in simulations:
        rewards = ((sim.get("reward_info") or {}).get("info") or {}).get(
            "subtask_rewards"
        ) or {}
        trajectories = (sim.get("states") or {}).get("integrity_subtask_trajectories")
        if not isinstance(trajectories, list):
            continue
        for index, traj in enumerate(trajectories):
            messages = traj.get("messages") or []
            pending: list[str] = []
            results: dict[str, list[str]] = collections.defaultdict(list)
            for message in messages:
                role = message.get("role")
                if role == "assistant":
                    pending = [
                        _signature(name, args) for name, args in _tool_calls(message)
                    ]
                elif role == "tool":
                    digest = _digest(message.get("content"))
                    for sig in pending:
                        results[sig].append(digest)
                    pending = []
            worst_sig, worst_n, worst_frozen = "-", 0, False
            for sig, digests in results.items():
                if len(digests) > worst_n:
                    worst_sig, worst_n = sig, len(digests)
                    worst_frozen = len(set(digests)) == 1
            thrashing = worst_n >= repeat_threshold and worst_frozen
            key = f"subtask_{index}_reward"
            rows.append(
                {
                    "unit": f"{sim.get('task_id')}#t{sim.get('trial')}",
                    "subtask_id": traj.get("subtask_id"),
                    "messages": len(messages),
                    "worst_repeat": [worst_sig, worst_n],
                    "worst_frozen": worst_frozen,
                    "thrashing": thrashing,
                    "reward": rewards.get(key),
                }
            )

    graded = [row for row in rows if row["reward"] is not None]
    thrash = [row for row in graded if row["thrashing"]]
    clean = [row for row in graded if not row["thrashing"]]

    # False-positive exposure: how often would a guard keyed on "N identical
    # frozen repeats" fire on a subtask that in fact passed? 0 is the safe case.
    histogram: dict[str, dict[str, int]] = {}
    for row in graded:
        bucket = min(row["worst_repeat"][1] if row["worst_frozen"] else 0, 20)
        key = str(bucket)
        group = "passed" if float(row["reward"]) == 1.0 else "failed"
        histogram.setdefault(key, {"passed": 0, "failed": 0})
        histogram[key][group] += 1
    passing_with_frozen_repeat = sum(
        1 for row in graded if float(row["reward"]) == 1.0 and row["thrashing"]
    )

    def rate(group: list[dict[str, Any]]) -> float:
        if not group:
            return 0.0
        return round(sum(float(row["reward"]) for row in group) / len(group), 4)

    units = len({row["unit"] for row in graded})
    per_unit_subtasks = len(graded) / units if units else 0.0
    # A rescued thrash subtask is worth 1/num_subtasks of its user-task unit.
    headroom = 0.0
    if units and per_unit_subtasks:
        potential = sum(1.0 - float(row["reward"]) for row in thrash)
        headroom = round(potential / per_unit_subtasks / units, 4)

    return {
        "subtasks_graded": len(graded),
        "units": units,
        "subtasks_per_unit": round(per_unit_subtasks, 2),
        "repeat_threshold": repeat_threshold,
        "thrashing_subtasks": len(thrash),
        "thrashing_share": round(len(thrash) / len(graded), 4) if graded else 0.0,
        "thrashing_pass_rate": rate(thrash),
        "clean_pass_rate": rate(clean),
        "passing_subtasks_that_would_fire": passing_with_frozen_repeat,
        "frozen_repeat_histogram": dict(
            sorted(histogram.items(), key=lambda item: int(item[0]))
        ),
        "max_cohort_avg_gain_if_all_rescued": headroom,
        "detail": sorted(thrash, key=lambda row: -(row["worst_repeat"][1])),
    }


def cluster_report(report: dict[str, Any]) -> dict[str, Any]:
    """Collapse trial replicates onto (user, subtask) before counting.

    The four trials of one user replay the same script, so the four rows for one
    (user, subtask) pair are replicates of one unit, not four independent ones.
    Counting them separately is pseudoreplication: it multiplies N by the trial
    count and overstates significance accordingly. The pair is the honest unit.
    """
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = collections.defaultdict(list)
    for row in report["detail"] + []:
        grouped[(row["unit"].split("#")[0], row["subtask_id"])].append(row)

    # Detail only lists thrashing rows, so rebuild the full pass/fail split from
    # the histogram-independent totals carried in the report.
    return {
        "note": "see cluster_counts for the full split",
        "thrashing_clusters": len(grouped),
        "thrashing_cluster_ids": sorted(
            f"{user}/{sid}" for user, sid in grouped
        ),
    }


def cluster_counts(simulations: list[dict[str, Any]], threshold: int) -> dict[str, Any]:
    """Cluster-level pass/fail for the thrash signal, replicating per (user, subtask)."""
    clusters: dict[tuple[str, str], dict[str, Any]] = {}
    for sim in simulations:
        rewards = ((sim.get("reward_info") or {}).get("info") or {}).get(
            "subtask_rewards"
        ) or {}
        trajectories = (sim.get("states") or {}).get("integrity_subtask_trajectories")
        if not isinstance(trajectories, list):
            continue
        for index, traj in enumerate(trajectories):
            messages = traj.get("messages") or []
            pending: list[str] = []
            results: dict[str, list[str]] = collections.defaultdict(list)
            for message in messages:
                role = message.get("role")
                if role == "assistant":
                    pending = [
                        _signature(name, args) for name, args in _tool_calls(message)
                    ]
                elif role == "tool":
                    digest = _digest(message.get("content"))
                    for sig in pending:
                        results[sig].append(digest)
                    pending = []
            worst_n, worst_frozen = 0, False
            for digests in results.values():
                if len(digests) > worst_n:
                    worst_n = len(digests)
                    worst_frozen = len(set(digests)) == 1
            reward = rewards.get(f"subtask_{index}_reward")
            if reward is None:
                continue
            key = (str(sim.get("task_id")), str(traj.get("subtask_id")))
            entry = clusters.setdefault(
                key, {"rewards": [], "thrash_trials": 0, "trials": 0, "max_repeat": 0}
            )
            entry["rewards"].append(float(reward))
            entry["trials"] += 1
            entry["max_repeat"] = max(entry["max_repeat"], worst_n)
            if worst_n >= threshold and worst_frozen:
                entry["thrash_trials"] += 1

    rows = []
    for (user, sid), entry in clusters.items():
        rows.append(
            {
                "cluster": f"{user}/{sid}",
                "trials": entry["trials"],
                "thrash_trials": entry["thrash_trials"],
                "thrashing": entry["thrash_trials"] > 0,
                "mean_reward": sum(entry["rewards"]) / len(entry["rewards"]),
                "max_repeat": entry["max_repeat"],
            }
        )

    thrash = [r for r in rows if r["thrashing"]]
    clean = [r for r in rows if not r["thrashing"]]
    passed_thrash = [r for r in thrash if r["mean_reward"] > 0]
    return {
        "clusters": len(rows),
        "trials_per_cluster": round(sum(r["trials"] for r in rows) / len(rows), 2)
        if rows
        else 0.0,
        "thrashing_clusters": len(thrash),
        "passing_clusters_total": sum(1 for r in rows if r["mean_reward"] > 0),
        "thrashing_clusters_that_passed": len(passed_thrash),
        "clean_mean_reward": round(
            sum(r["mean_reward"] for r in clean) / len(clean), 4
        )
        if clean
        else 0.0,
        "thrashing_mean_reward": round(
            sum(r["mean_reward"] for r in thrash) / len(thrash), 4
        )
        if thrash
        else 0.0,
        "detail": sorted(thrash, key=lambda r: -r["max_repeat"]),
    }


def within_cluster_contrast(
    simulations: list[dict[str, Any]], threshold: int
) -> dict[str, Any]:
    """Same (user, subtask), same script: thrashing trials vs non-thrashing ones.

    This is the contrast that matters. Comparing thrashing subtasks against
    *other* subtasks confounds the signal with intrinsic difficulty: a hard
    subtask is exactly where a model gets lost. Comparing the trials of one
    cluster against each other removes that confound, because the subtask, the
    script and the user are held fixed.
    """
    clusters: dict[tuple[str, str], list[tuple[bool, float]]] = collections.defaultdict(list)
    for sim in simulations:
        rewards = ((sim.get("reward_info") or {}).get("info") or {}).get(
            "subtask_rewards"
        ) or {}
        trajectories = (sim.get("states") or {}).get("integrity_subtask_trajectories")
        if not isinstance(trajectories, list):
            continue
        for index, traj in enumerate(trajectories):
            reward = rewards.get(f"subtask_{index}_reward")
            if reward is None:
                continue
            messages = traj.get("messages") or []
            pending: list[str] = []
            results: dict[str, list[str]] = collections.defaultdict(list)
            for message in messages:
                role = message.get("role")
                if role == "assistant":
                    pending = [
                        _signature(name, args) for name, args in _tool_calls(message)
                    ]
                elif role == "tool":
                    digest = _digest(message.get("content"))
                    for sig in pending:
                        results[sig].append(digest)
                    pending = []
            frozen_max = 0
            for digests in results.values():
                if len(set(digests)) == 1:
                    frozen_max = max(frozen_max, len(digests))
            key = (str(sim.get("task_id")), str(traj.get("subtask_id")))
            clusters[key].append((frozen_max >= threshold, float(reward)))

    # Only clusters that donate both kinds of trial carry the contrast.
    mixed = {k: v for k, v in clusters.items() if len({f for f, _ in v}) == 2}
    thrash_trials = [r for k, v in mixed.items() for f, r in v if f]
    other_trials = [r for k, v in mixed.items() for f, r in v if not f]

    a = sum(1 for r in thrash_trials if r == 1.0)
    b = len(thrash_trials) - a
    c = sum(1 for r in other_trials if r == 1.0)
    d = len(other_trials) - c

    return {
        "mixed_clusters": len(mixed),
        "mixed_cluster_ids": sorted(f"{u}/{s}" for u, s in mixed),
        "thrash_trials": len(thrash_trials),
        "thrash_trials_passed": a,
        "non_thrash_trials": len(other_trials),
        "non_thrash_trials_passed": c,
        "thrash_pass_rate": round(a / len(thrash_trials), 4) if thrash_trials else None,
        "non_thrash_pass_rate": round(c / len(other_trials), 4)
        if other_trials
        else None,
        "fisher_exact_p_two_sided": _fisher_two_sided(a, b, c, d),
    }


def _fisher_two_sided(a: int, b: int, c: int, d: int) -> float:
    """Exact two-sided Fisher test for a 2x2 table, no scipy dependency."""
    row1, row2 = a + b, c + d
    col1 = a + c
    total = row1 + row2
    if total == 0:
        return 1.0

    def prob(x: int) -> float:
        return (
            math.comb(row1, x) * math.comb(row2, col1 - x) / math.comb(total, col1)
        )

    observed = prob(a)
    low = max(0, col1 - row2)
    high = min(row1, col1)
    total_p = 0.0
    for x in range(low, high + 1):
        p = prob(x)
        if p <= observed + 1e-12:
            total_p += p
    return round(min(total_p, 1.0), 5)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoint", nargs="+")
    parser.add_argument("--tail", type=int, default=24)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--repeat-detail",
        action="store_true",
        help="for each max_steps worst-repeat signature, show result identity across calls",
    )
    parser.add_argument(
        "--show-frozen",
        action="store_true",
        help="print the arguments and frozen result of each worst-repeat signature",
    )
    parser.add_argument("--width", type=int, default=300)
    parser.add_argument(
        "--repeat-threshold",
        type=int,
        default=10,
        help="a signature issued this many times with an identical result is thrash",
    )
    args = parser.parse_args()

    simulations: list[dict[str, Any]] = []
    for path in args.checkpoint:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        simulations.extend(payload.get("simulations", []))

    decoding = decoding_report(simulations)
    runaway = runaway_report(simulations, args.tail)
    rewards = reward_by_termination(simulations)
    ceiling = frozen_ceiling(simulations)
    subtasks = subtask_thrash_report(simulations, args.repeat_threshold)
    clusters = cluster_counts(simulations, args.repeat_threshold)
    contrast = within_cluster_contrast(simulations, args.repeat_threshold)

    identity = {}
    frozen = {}
    if args.repeat_detail or args.show_frozen:
        worst = [
            row["worst_repeat"][0]
            for row in runaway["detail"]
            if row["worst_repeat"][1] > 1
        ]
        if args.repeat_detail:
            identity = repeat_result_identity(simulations, worst)
        if args.show_frozen:
            frozen = show_frozen(simulations, worst, args.width)

    if args.json:
        print(
            json.dumps(
                {"decoding": decoding, "runaway": runaway, "repeat_identity": identity,
                 "frozen_calls": frozen, "reward_by_termination": rewards,
                 "frozen_ceiling": ceiling, "subtask_thrash": subtasks,
                 "clustered": clusters, "within_cluster": contrast},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    print(f"simulations: {len(simulations)}")
    print()
    print("== decoding ==")
    for key, value in decoding.items():
        print(f"  {key}: {value}")
    print()
    print("== runaway ==")
    for key, value in runaway.items():
        if key != "detail":
            print(f"  {key}: {value}")
    print()
    print("== reward by termination ==")
    for reason, info in rewards.items():
        print(f"  {reason}: {info}")
    print()
    print("== runaway cost ceiling ==")
    for key, value in ceiling.items():
        print(f"  {key}: {value}")
    print()
    print("== clustered by (user, subtask) ==")
    for key, value in clusters.items():
        if key != "detail":
            print(f"  {key}: {value}")
    for row in clusters["detail"]:
        print(
            f"    {row['cluster']:<26} trials={row['trials']} "
            f"thrash_trials={row['thrash_trials']} mean_reward={row['mean_reward']:.4f} "
            f"max_repeat={row['max_repeat']}"
        )
    print()
    print("== within-cluster contrast (same user, same subtask, same script) ==")
    for key, value in contrast.items():
        print(f"  {key}: {value}")
    print()
    print("== trial-replicated subtask view (pseudoreplicated, for reference) ==")
    for key, value in subtasks.items():
        if key != "detail":
            print(f"  {key}: {value}")
    for row in subtasks["detail"]:
        print(
            f"    {row['unit']:<18} {str(row['subtask_id']):<22} msgs={row['messages']:<5} "
            f"reward={row['reward']} repeat={row['worst_repeat'][0]} x{row['worst_repeat'][1]}"
        )
    print()
    header = f"{'task':<10} {'tr':<3} {'msgs':>5} {'calls':>6} {'uniq':>5} {'rep':>5} {'t_uniq':>6} {'t_err':>6} {'worst repeat'}"
    print(header)
    print("-" * len(header))
    for row in runaway["detail"]:
        print(
            f"{str(row['task_id']):<10} {str(row['trial']):<3} {row['messages']:>5} "
            f"{row['tool_calls']:>6} {row['distinct_calls']:>5} {row['repeat_ratio']:>5} "
            f"{row['tail_distinct_calls']:>6} {str(row['tail_error_rate']):>6} "
            f"{row['worst_repeat'][0]} x{row['worst_repeat'][1]}"
        )
    print()
    for row in runaway["detail"]:
        print(f"-- {row['task_id']} trial {row['trial']} tail tool sequence --")
        print("   " + " ".join(row["tail_tool_sequence"]))

    if identity:
        print()
        print("== repeat identity ==")
        for sig, info in identity.items():
            print(f"  {sig}")
            for key, value in info.items():
                if key != "result_digests":
                    print(f"      {key}: {value}")
            print(f"      result_digests: {info['result_digests']}")

    if frozen:
        print()
        print("== frozen calls ==")
        for sig, info in frozen.items():
            if not info:
                continue
            print(f"  {sig}  ({info['task_id']} trial {info['trial']})")
            print(f"      arguments: {json.dumps(info['arguments'], ensure_ascii=False)[:400]}")
            print(f"      result ({info['result_length']} chars): {info['result_repr']!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

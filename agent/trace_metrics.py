"""Aggregate observable run metrics without exposing blind-case trace details."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any


def summarize(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    simulations = payload.get("simulations", [])
    rewards: list[float] = []
    window_rewards: list[list[float]] = []
    tool_errors = 0
    prevented_writes = 0
    repeated_search_excess = 0
    incomplete_payments = 0
    subtask_rewards: list[float] = []
    task_trials: dict[str, list[tuple[int, float]]] = defaultdict(list)
    subtask_trials: dict[tuple[str, str], list[tuple[int, float]]] = defaultdict(list)

    for simulation_index, simulation in enumerate(simulations):
        task_id = str(simulation.get("task_id") or f"simulation:{simulation_index}")
        trial = int(simulation.get("trial") or 0)
        reward = (simulation.get("reward_info") or {}).get("reward")
        if isinstance(reward, (int, float)):
            rewards.append(float(reward))
            task_trials[task_id].append((trial, float(reward)))
        breakdown = (
            ((simulation.get("reward_info") or {}).get("info") or {})
            .get("subtask_rewards")
            or {}
        )
        subtask_rewards.extend(
            float(value)
            for value in breakdown.values()
            if isinstance(value, (int, float))
        )
        for subtask_id, value in breakdown.items():
            if isinstance(value, (int, float)):
                subtask_trials[(task_id, str(subtask_id))].append(
                    (trial, float(value))
                )
        windows = _window_rewards(simulation.get("reward_info") or {})
        if windows:
            window_rewards.append(windows)

        search_counts: Counter[str] = Counter()
        pending_payment = False
        for message in _flatten_messages(simulation.get("messages", [])):
            role = message.get("role")
            content = str(message.get("content") or "")
            if role == "assistant":
                for call in message.get("tool_calls") or []:
                    name = str(call.get("name") or "")
                    if _is_search(name):
                        arguments = call.get("arguments") or {}
                        signature = f"{name}:{json.dumps(arguments, sort_keys=True, ensure_ascii=False)}"
                        search_counts[signature] += 1
            elif role == "tool":
                if content.startswith("ADAPT preflight rejected"):
                    prevented_writes += 1
                elif message.get("error"):
                    tool_errors += 1
                if "status=unpaid" in content or "status:unpaid" in content:
                    pending_payment = True
                if "Payment successful" in content or "支付成功" in content:
                    pending_payment = False
        repeated_search_excess += sum(max(0, count - 2) for count in search_counts.values())
        incomplete_payments += int(pending_payment)

    early, late = _early_late(window_rewards)
    configured_k = int((payload.get("info") or {}).get("num_trials") or 1)
    observed_k = max((len(values) for values in task_trials.values()), default=0)
    evaluation_k = min(configured_k, observed_k) if observed_k else configured_k
    task_k_values = [
        _ordered_values(values)[:evaluation_k]
        for values in task_trials.values()
        if values
    ]
    subtask_k_values = [
        _ordered_values(values)[:evaluation_k]
        for values in subtask_trials.values()
        if values
    ]
    task_avg_at_1 = (
        _round(mean(mean(values) for values in task_k_values))
        if task_k_values
        else None
    )
    task_pass_at_1 = (
        _round(mean(_pass_at_k(values, 1) for values in task_k_values))
        if task_k_values
        else None
    )
    subtask_avg_at_1 = (
        _round(mean(mean(values) for values in subtask_k_values))
        if subtask_k_values
        else None
    )
    subtask_pass_at_1 = (
        _round(mean(_pass_at_k(values, 1) for values in subtask_k_values))
        if subtask_k_values
        else None
    )
    task_avg_at_k = (
        _round(mean(mean(values) for values in task_k_values))
        if task_k_values
        else None
    )
    task_pass_at_k = (
        _round(mean(_pass_at_k(values, evaluation_k) for values in task_k_values))
        if task_k_values
        else None
    )
    subtask_avg_at_k = (
        _round(mean(mean(values) for values in subtask_k_values))
        if subtask_k_values
        else None
    )
    subtask_pass_at_k = (
        _round(mean(_pass_at_k(values, evaluation_k) for values in subtask_k_values))
        if subtask_k_values
        else None
    )
    return {
        "path": str(path),
        "agent_kind": (payload.get("info") or {}).get("agent_kind"),
        "cohort": (payload.get("info") or {}).get("cohort"),
        "num_simulations": len(simulations),
        # ``avg_reward`` remains for backward compatibility. The explicit
        # names mirror VitaBench's task-level Avg@1 / Pass@1 definitions.
        "avg_reward": task_avg_at_k,
        "evaluation_k": evaluation_k,
        "task_avg_at_1": task_avg_at_1,
        "task_pass_at_1": task_pass_at_1,
        "subtask_avg_at_1": subtask_avg_at_1,
        "subtask_pass_at_1": subtask_pass_at_1,
        "task_avg_at_k": task_avg_at_k,
        "task_pass_at_k": task_pass_at_k,
        "subtask_avg_at_k": subtask_avg_at_k,
        "subtask_pass_at_k": subtask_pass_at_k,
        "subtask_count": len(subtask_rewards),
        "early_window_reward": early,
        "late_window_reward": late,
        "late_minus_early": (
            _round(late - early)
            if early is not None and late is not None
            else None
        ),
        "tool_errors": tool_errors,
        "prevented_writes": prevented_writes,
        "repeated_search_excess": repeated_search_excess,
        "incomplete_payments": incomplete_payments,
    }


def compare(baseline: dict[str, Any], adapt: dict[str, Any]) -> dict[str, Any]:
    result = {"baseline": baseline, "adapt": adapt}
    base_reward = baseline.get("avg_reward")
    adapt_reward = adapt.get("avg_reward")
    result["reward_delta"] = (
        _round(adapt_reward - base_reward)
        if isinstance(base_reward, (int, float)) and isinstance(adapt_reward, (int, float))
        else None
    )
    for metric in (
        "task_avg_at_1",
        "task_pass_at_1",
        "subtask_avg_at_1",
        "subtask_pass_at_1",
        "task_avg_at_k",
        "task_pass_at_k",
        "subtask_avg_at_k",
        "subtask_pass_at_k",
    ):
        original = baseline.get(metric)
        current = adapt.get(metric)
        result[f"{metric}_delta"] = (
            _round(current - original)
            if isinstance(original, (int, float))
            and isinstance(current, (int, float))
            else None
        )
    for metric in ("tool_errors", "repeated_search_excess", "incomplete_payments"):
        original = baseline.get(metric, 0)
        current = adapt.get(metric, 0)
        result[f"{metric}_reduction"] = (
            _round((original - current) / original) if original else None
        )
    return result


def _flatten_messages(messages: list[dict]) -> list[dict]:
    flattened: list[dict] = []
    for message in messages:
        if message.get("role") == "tool" and message.get("tool_messages"):
            flattened.extend(message["tool_messages"])
        else:
            flattened.append(message)
    return flattened


def _ordered_values(values: list[tuple[int, float]]) -> list[float]:
    return [value for _, value in sorted(values, key=lambda item: item[0])]


def _pass_at_k(values: list[float], k: int) -> float:
    n = len(values)
    if n < k or k <= 0:
        return 0.0
    successes = sum(value == 1.0 for value in values)
    if n - successes < k:
        return 1.0
    return 1.0 - math.comb(n - successes, k) / math.comb(n, k)


def _window_rewards(reward_info: dict) -> list[float]:
    result: list[float] = []
    for window in reward_info.get("window_evaluations") or []:
        for key in ("reward", "score"):
            value = window.get(key)
            if isinstance(value, (int, float)):
                result.append(float(value))
                break
    return result


def _early_late(windows: list[list[float]]) -> tuple[float | None, float | None]:
    early = [values[0] for values in windows if values]
    late = [values[-1] for values in windows if values]
    return (
        _round(mean(early)) if early else None,
        _round(mean(late)) if late else None,
    )


def _round(value: float) -> float:
    return round(value, 6)


def _is_search(name: str) -> bool:
    lowered = name.lower()
    return any(marker in lowered for marker in ("search", "recommend", "recommand"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("adapt", type=Path)
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args()
    adapt = summarize(args.adapt)
    output = compare(summarize(args.baseline), adapt) if args.baseline else adapt
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

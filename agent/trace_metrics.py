"""Aggregate observable run metrics without exposing blind-case trace details."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from statistics import mean
from typing import Any


def summarize(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    simulations = payload.get("simulations", [])
    exclusions = _load_exclusions(path)
    rewards: list[float] = []
    window_rewards: list[list[float]] = []
    tool_errors = 0
    prevented_writes = 0
    repeated_search_excess = 0
    incomplete_payments = 0
    evaluation_failed = 0
    skill_rewards: dict[str, list[float]] = {"personalize": [], "proactive": []}
    simulation_scores: dict[str, float] = {}

    for simulation in simulations:
        if simulation.get("evaluation_status") == "evaluation_failed" or _excluded(
            simulation, exclusions
        ):
            evaluation_failed += 1
            continue
        reward = (simulation.get("reward_info") or {}).get("reward")
        if isinstance(reward, (int, float)):
            rewards.append(float(reward))
            key = ":".join(
                str(simulation.get(field, ""))
                for field in ("task_id", "trial", "seed")
            )
            simulation_scores[key] = float(reward)
        reward_info = simulation.get("reward_info") or {}
        info = reward_info.get("info") or {}
        tested = info.get("subtask_skill_tested") or {}
        per_subtask = info.get("subtask_rewards") or {}
        for subtask_key, skills in tested.items():
            suffix = str(subtask_key).removeprefix("subtask_")
            value = per_subtask.get(f"subtask_{suffix}_reward")
            if not isinstance(value, (int, float)):
                continue
            for skill in (skills if isinstance(skills, list) else [skills]):
                lowered = str(skill).lower()
                if lowered == "update":
                    skill_rewards["personalize"].append(float(value))
                for group, values in skill_rewards.items():
                    if group in lowered:
                        values.append(float(value))
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
    pass4 = _pass_at_four(simulations, exclusions)
    return {
        "path": str(path),
        "agent_kind": (payload.get("info") or {}).get("agent_kind"),
        "cohort": (payload.get("info") or {}).get("cohort"),
        "num_simulations": len(simulations),
        "num_scoreable": len(rewards),
        "evaluation_failed": evaluation_failed,
        "avg_reward": _round(mean(rewards)) if rewards else None,
        "personalize_reward": (
            _round(mean(skill_rewards["personalize"]))
            if skill_rewards["personalize"] else None
        ),
        "proactive_reward": (
            _round(mean(skill_rewards["proactive"]))
            if skill_rewards["proactive"] else None
        ),
        "pass_at_4": pass4,
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
        "simulation_scores": simulation_scores,
    }


def _load_exclusions(path: Path) -> list[dict[str, Any]]:
    sidecar = path.with_suffix(".exclusions.json")
    if not sidecar.exists():
        return []
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    return list(payload.get("exclusions") or [])


def _excluded(simulation: dict[str, Any], exclusions: list[dict[str, Any]]) -> bool:
    for exclusion in exclusions:
        if str(exclusion.get("task_id")) != str(simulation.get("task_id")):
            continue
        matches = True
        for key in ("trial", "seed"):
            if exclusion.get(key) is not None and exclusion.get(key) != simulation.get(key):
                matches = False
                break
        if matches:
            return True
    return False


def compare(baseline: dict[str, Any], adapt: dict[str, Any]) -> dict[str, Any]:
    result = {"baseline": baseline, "adapt": adapt}
    base_reward = baseline.get("avg_reward")
    adapt_reward = adapt.get("avg_reward")
    result["reward_delta"] = (
        _round(adapt_reward - base_reward)
        if isinstance(base_reward, (int, float)) and isinstance(adapt_reward, (int, float))
        else None
    )
    for metric in ("tool_errors", "repeated_search_excess", "incomplete_payments"):
        original = baseline.get(metric, 0)
        current = adapt.get(metric, 0)
        result[f"{metric}_reduction"] = (
            _round((original - current) / original) if original else None
        )
    baseline_scores = baseline.get("simulation_scores") or {}
    adapt_scores = adapt.get("simulation_scores") or {}
    shared = sorted(set(baseline_scores) & set(adapt_scores))
    result["paired"] = {
        "wins": sum(adapt_scores[key] > baseline_scores[key] for key in shared),
        "ties": sum(adapt_scores[key] == baseline_scores[key] for key in shared),
        "losses": sum(adapt_scores[key] < baseline_scores[key] for key in shared),
        "net_wins": sum(adapt_scores[key] > baseline_scores[key] for key in shared)
        - sum(adapt_scores[key] < baseline_scores[key] for key in shared),
        "shared": len(shared),
    }
    return result


def _pass_at_four(
    simulations: list[dict], exclusions: list[dict[str, Any]] | None = None
) -> float | None:
    by_task: dict[str, list[float]] = {}
    for simulation in simulations:
        if simulation.get("evaluation_status") == "evaluation_failed" or _excluded(
            simulation, exclusions or []
        ):
            continue
        reward = (simulation.get("reward_info") or {}).get("reward")
        if isinstance(reward, (int, float)):
            by_task.setdefault(str(simulation.get("task_id")), []).append(float(reward))
    groups = [values[:4] for values in by_task.values() if len(values) >= 4]
    if not groups:
        return None
    return _round(mean(1.0 if all(value >= 1.0 for value in values) else 0.0 for values in groups))


def _flatten_messages(messages: list[dict]) -> list[dict]:
    flattened: list[dict] = []
    for message in messages:
        if message.get("role") == "tool" and message.get("tool_messages"):
            flattened.extend(message["tool_messages"])
        else:
            flattened.append(message)
    return flattened


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

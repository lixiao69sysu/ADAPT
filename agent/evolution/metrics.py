"""Build comparable scorecards from baseline and candidate VitaBench results."""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any

from agent.evolution.cases import _split_subtasks, mine_log_hints
from agent.evolution.models import Scorecard


_LOG_REWARD = re.compile(
    r"Subtask\s+sub_(.+)_(\d+)\s+evaluation:\s+reward=([0-9.]+)"
)


def read_subtask_rewards_from_log(path: Path) -> dict[str, float]:
    rewards: dict[str, float] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = _LOG_REWARD.search(line)
        if match:
            task_id, one_based_index, reward = match.groups()
            rewards[f"{task_id}:subtask_{int(one_based_index) - 1}"] = float(reward)
    return rewards


def _reward_map(results: dict[str, Any]) -> dict[str, float]:
    values: dict[str, float] = {}
    for simulation in results.get("simulations") or []:
        task_id = str(simulation.get("task_id") or simulation.get("id") or "unknown")
        info = ((simulation.get("reward_info") or {}).get("info") or {})
        for key, reward in (info.get("subtask_rewards") or {}).items():
            if not key.startswith("subtask_") or not key.endswith("_reward"):
                continue
            index = key.removeprefix("subtask_").removesuffix("_reward")
            values[f"{task_id}:subtask_{index}"] = float(reward)
    return values


def _trajectory_metrics(results: dict[str, Any]) -> tuple[float, float, float | None]:
    tool_messages = 0
    tool_errors = 0
    looped_subtasks = 0
    total_subtasks = 0
    token_total = 0.0
    token_observations = 0
    for simulation in results.get("simulations") or []:
        chunks = _split_subtasks(simulation.get("messages") or [])
        total_subtasks += len(chunks)
        for chunk in chunks:
            signatures: list[str] = []
            for message in chunk:
                if message.get("role") == "tool":
                    tool_messages += 1
                    tool_errors += int(bool(message.get("error")))
                if message.get("role") == "assistant":
                    usage = message.get("usage") or {}
                    value = usage.get("total_tokens")
                    if isinstance(value, (int, float)):
                        token_total += float(value)
                        token_observations += 1
                    for call in message.get("tool_calls") or []:
                        arguments = json.dumps(
                            call.get("arguments") or {},
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        signatures.append(f"{call.get('name', '')}:{arguments}")
            if max(Counter(signatures).values(), default=0) >= 3:
                looped_subtasks += 1
    invalid_rate = tool_errors / tool_messages if tool_messages else 0.0
    loop_rate = looped_subtasks / total_subtasks if total_subtasks else 0.0
    avg_tokens = token_total / total_subtasks if token_observations and total_subtasks else None
    return invalid_rate, loop_rate, avg_tokens


def _score(
    results: dict[str, Any],
    *,
    population: list[str],
    bad_cases: list[str],
    regressions: list[str],
) -> Scorecard:
    rewards = _reward_map(results)

    def rate(ids: list[str]) -> float:
        if not ids:
            return 1.0
        return sum(rewards.get(case_id, 0.0) > 0 for case_id in ids) / len(ids)

    invalid_rate, loop_rate, avg_tokens = _trajectory_metrics(results)
    average = (
        sum(rewards.get(case_id, 0.0) for case_id in population) / len(population)
        if population
        else 0.0
    )
    return Scorecard(
        sample_count=len(population),
        avg_reward=average,
        bad_case_success_rate=rate(bad_cases),
        regression_success_rate=rate(regressions),
        invalid_tool_rate=invalid_rate,
        loop_rate=loop_rate,
        avg_tokens=avg_tokens,
    )


def build_scorecards(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    *,
    baseline_log: Path | None = None,
    candidate_log: Path | None = None,
) -> tuple[Scorecard, Scorecard]:
    """Score both runs on the exact baseline cohort to prevent missing-case bias."""
    baseline_rewards = _reward_map(baseline)
    population = sorted(baseline_rewards)
    bad_cases = [case_id for case_id in population if baseline_rewards[case_id] <= 0]
    regressions = [case_id for case_id in population if baseline_rewards[case_id] > 0]
    baseline_card = _score(
        baseline,
        population=population,
        bad_cases=bad_cases,
        regressions=regressions,
    )
    candidate_card = _score(
        candidate,
        population=population,
        bad_cases=bad_cases,
        regressions=regressions,
    )
    if bool(baseline_log) != bool(candidate_log):
        raise ValueError("baseline and candidate logs must be provided together")
    if baseline_log and candidate_log:
        task_ids = {case_id.split(":subtask_", 1)[0] for case_id in population}
        if len(task_ids) != 1:
            raise ValueError("log-aware gating currently requires a single task")
        task_id = next(iter(task_ids))

        def rate(path: Path) -> float:
            hints = mine_log_hints(path, task_id)
            looped = sum("repeated_tool_call" in codes for codes in hints.values())
            return looped / len(population) if population else 0.0

        baseline_card = replace(baseline_card, loop_rate=rate(baseline_log))
        candidate_card = replace(candidate_card, loop_rate=rate(candidate_log))
    return baseline_card, candidate_card

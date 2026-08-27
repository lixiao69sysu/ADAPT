from __future__ import annotations

import json

from agent.trace_metrics import compare, summarize


def test_trace_metrics_are_aggregate_and_observable(tmp_path):
    path = tmp_path / "run.json"
    path.write_text(json.dumps({
        "info": {"agent_kind": "adapt", "cohort": "dev"},
        "simulations": [{
            "reward_info": {
                "reward": 0.5,
                "info": {
                    "subtask_rewards": {
                        "subtask_0_reward": 1.0,
                        "subtask_1_reward": 0.0,
                    }
                },
                "window_evaluations": [{"reward": 0.4}, {"reward": 0.6}],
            },
            "messages": [
                {"role": "assistant", "tool_calls": [
                    {"name": "hotel_search_recommand", "arguments": {"city": "上海"}}
                ]},
                {"role": "tool", "name": "create_hotel_order", "error": True,
                 "content": "invalid room id"},
            ],
        }],
    }), encoding="utf-8")
    metrics = summarize(path)
    assert metrics["avg_reward"] == 0.5
    assert metrics["task_avg_at_1"] == 0.5
    assert metrics["task_pass_at_1"] == 0.0
    assert metrics["subtask_avg_at_1"] == 0.5
    assert metrics["subtask_pass_at_1"] == 0.5
    assert metrics["subtask_count"] == 2
    assert metrics["late_minus_early"] == 0.2
    assert metrics["tool_errors"] == 1
    assert "rubric" not in metrics


def test_metric_comparison_reports_reward_and_error_reduction():
    result = compare(
        {"avg_reward": 0.2, "task_avg_at_1": 0.2, "task_pass_at_1": 0.1,
         "subtask_avg_at_1": 0.3, "subtask_pass_at_1": 0.3,
         "tool_errors": 10, "repeated_search_excess": 4,
         "incomplete_payments": 2},
        {"avg_reward": 0.25, "task_avg_at_1": 0.25, "task_pass_at_1": 0.2,
         "subtask_avg_at_1": 0.4, "subtask_pass_at_1": 0.4,
         "tool_errors": 5, "repeated_search_excess": 1,
         "incomplete_payments": 0},
    )
    assert result["reward_delta"] == 0.05
    assert result["task_avg_at_1_delta"] == 0.05
    assert result["task_pass_at_1_delta"] == 0.1
    assert result["subtask_avg_at_1_delta"] == 0.1
    assert result["subtask_pass_at_1_delta"] == 0.1
    assert result["tool_errors_reduction"] == 0.5


def test_trace_metrics_compute_true_avg_and_pass_at_four(tmp_path):
    path = tmp_path / "run4.json"
    simulations = []
    for task_id, rewards in {"A": [0.1, 1.0, 0.3, 0.5], "B": [0.2, 0.4, 0.6, 0.8]}.items():
        for trial, reward in enumerate(rewards):
            simulations.append({
                "task_id": task_id,
                "trial": trial,
                "reward_info": {
                    "reward": reward,
                    "info": {"subtask_rewards": {"s0": reward}},
                },
                "messages": [],
            })
    path.write_text(
        json.dumps({"info": {"num_trials": 4}, "simulations": simulations}),
        encoding="utf-8",
    )
    metrics = summarize(path)
    assert metrics["evaluation_k"] == 4
    assert metrics["task_avg_at_1"] == 0.4875
    assert metrics["task_pass_at_1"] == 0.125
    assert metrics["task_avg_at_k"] == 0.4875
    assert metrics["task_pass_at_k"] == 0.5
    assert metrics["subtask_pass_at_k"] == 0.5

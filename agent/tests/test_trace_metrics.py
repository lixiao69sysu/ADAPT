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
    assert metrics["late_minus_early"] == 0.2
    assert metrics["tool_errors"] == 1
    assert "rubric" not in metrics


def test_metric_comparison_reports_reward_and_error_reduction():
    result = compare(
        {"avg_reward": 0.2, "tool_errors": 10, "repeated_search_excess": 4,
         "incomplete_payments": 2},
        {"avg_reward": 0.25, "tool_errors": 5, "repeated_search_excess": 1,
         "incomplete_payments": 0},
    )
    assert result["reward_delta"] == 0.05
    assert result["tool_errors_reduction"] == 0.5

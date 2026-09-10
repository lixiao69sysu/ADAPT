"""Ablation definitions and promotion gates for ADAPT V2."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from agent.trace_metrics import compare, summarize
from agent.v2.config import V2FeatureFlags

ABLATION_MATRIX: dict[str, dict[str, Any]] = {
    "stock_rewrite": {
        "agent_kind": "stock",
        "memory_type": "rewrite",
        "feature_flags": V2FeatureFlags(),
    },
    "stock_hybrid": {
        "agent_kind": "adapt_v2",
        "memory_type": "rewrite",
        "feature_flags": V2FeatureFlags(hybrid_memory=True),
    },
    "v2_planner_rewrite": {
        "agent_kind": "adapt_v2",
        "memory_type": "rewrite",
        "feature_flags": V2FeatureFlags(
            decision_workspace=True,
            planner=True,
        ),
    },
    "v2_full": {
        "agent_kind": "adapt_v2",
        "memory_type": "rewrite",
        "feature_flags": V2FeatureFlags(
            hybrid_memory=True,
            decision_workspace=True,
            planner=True,
            voi_questions=True,
            transaction_enforcement=True,
        ),
    },
    "stock_groundtruth": {
        "agent_kind": "stock",
        "memory_type": "groundtruth",
        "feature_flags": V2FeatureFlags(),
    },
}


def promotion_report(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    *,
    blind_baseline: dict[str, Any] | None = None,
    blind_candidate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    paired = compare(baseline, candidate).get("paired", {})
    checks = {
        "evaluation_integrity": candidate.get("evaluation_failed", 0) == 0,
        "dev_avg_plus_0_03": _delta(baseline, candidate, "avg_reward") >= 0.03,
        "personalize_floor": _floor(
            baseline, candidate, "personalize_reward", tolerance=0.01
        ),
        "proactive_plus_0_05": _delta(
            baseline, candidate, "proactive_reward"
        )
        >= 0.05,
        "positive_net_wins": paired.get("net_wins", 0) > 0,
        "no_new_tool_errors": candidate.get("tool_errors", 0)
        <= baseline.get("tool_errors", 0),
    }
    if blind_baseline is not None and blind_candidate is not None:
        checks["blind_non_regression"] = (
            _delta(blind_baseline, blind_candidate, "avg_reward") >= 0.0
            and blind_candidate.get("evaluation_failed", 0) == 0
        )
    else:
        checks["blind_non_regression"] = False
    return {
        "promote": all(checks.values()),
        "checks": checks,
        "dev_comparison": compare(baseline, candidate),
        "blind_comparison": (
            compare(blind_baseline, blind_candidate)
            if blind_baseline is not None and blind_candidate is not None
            else None
        ),
    }


def _delta(baseline: dict[str, Any], candidate: dict[str, Any], key: str) -> float:
    before, after = baseline.get(key), candidate.get(key)
    if not isinstance(before, (int, float)) or not isinstance(after, (int, float)):
        return float("-inf")
    return float(after) - float(before)


def _floor(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    key: str,
    *,
    tolerance: float,
) -> bool:
    before, after = baseline.get(key), candidate.get(key)
    return (
        isinstance(before, (int, float))
        and isinstance(after, (int, float))
        and float(after) >= float(before) - tolerance
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--blind-baseline", type=Path)
    parser.add_argument("--blind-candidate", type=Path)
    args = parser.parse_args()
    report = promotion_report(
        summarize(args.baseline),
        summarize(args.candidate),
        blind_baseline=summarize(args.blind_baseline) if args.blind_baseline else None,
        blind_candidate=summarize(args.blind_candidate) if args.blind_candidate else None,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

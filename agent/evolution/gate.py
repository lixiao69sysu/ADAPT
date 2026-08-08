"""Promotion rules for candidate Agent strategies."""

from __future__ import annotations

from agent.evolution.models import GateDecision, Scorecard


class PromotionGate:
    """Reward-first gate; token usage is recorded but is not a hard constraint."""

    def __init__(
        self,
        *,
        max_regression_drop: float = 0.0,
        max_invalid_tool_increase: float = 0.0,
        max_loop_increase: float = 0.0,
    ) -> None:
        self.max_regression_drop = max_regression_drop
        self.max_invalid_tool_increase = max_invalid_tool_increase
        self.max_loop_increase = max_loop_increase

    def evaluate(self, baseline: Scorecard, candidate: Scorecard) -> GateDecision:
        reasons: list[str] = []
        if baseline.sample_count <= 0 or candidate.sample_count <= 0:
            reasons.append("scorecards require at least one evaluated sample")
        if candidate.bad_case_success_rate <= baseline.bad_case_success_rate:
            reasons.append("bad-case success rate did not improve")
        if candidate.avg_reward < baseline.avg_reward:
            reasons.append("average reward regressed")
        if (
            candidate.regression_success_rate
            < baseline.regression_success_rate - self.max_regression_drop
        ):
            reasons.append("regression success rate exceeded allowed drop")
        if (
            candidate.invalid_tool_rate
            > baseline.invalid_tool_rate + self.max_invalid_tool_increase
        ):
            reasons.append("invalid-tool rate increased")
        if candidate.loop_rate > baseline.loop_rate + self.max_loop_increase:
            reasons.append("loop rate increased")
        return GateDecision(promoted=not reasons, reasons=reasons or ["all gates passed"])


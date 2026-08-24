"""Deterministic gate for questions emitted outside the framework planner."""

from __future__ import annotations

from dataclasses import dataclass

from agent.runtime.state import RuntimePhase, TaskRuntime


@dataclass(frozen=True)
class QuestionDecision:
    allowed: bool
    dimension: str = ""
    reason: str = ""
    counts_against_budget: bool = True


class QuestionGate:
    """Allow only one new decision-critical dimension at a time."""

    @staticmethod
    def is_question(text: str) -> bool:
        return "?" in (text or "") or "？" in (text or "")

    def evaluate(self, text: str, runtime: TaskRuntime) -> QuestionDecision:
        if not self.is_question(text):
            return QuestionDecision(True, counts_against_budget=False)
        if runtime.phase == RuntimePhase.READY_TO_PAY:
            if runtime.authorization.pay_declined:
                return QuestionDecision(False, reason="payment was declined")
            return QuestionDecision(
                True, "payment_confirmation", counts_against_budget=False
            )
        if runtime.authorization.candidate_choice_authorized:
            return QuestionDecision(
                False,
                reason=(
                    "the direct execution request already authorizes candidate "
                    "selection; expand required details or commit without reconfirming"
                ),
            )
        if runtime.authorization.choice_delegated:
            return QuestionDecision(
                False,
                reason="the user delegated the choice; select a compliant candidate",
            )
        if (
            runtime.forbid_redundant_candidate_question
            and runtime.authorization.create_authorized
            and runtime.execution_ready
        ):
            return QuestionDecision(
                False,
                reason=(
                    "a learned same-user runtime policy requires execution after "
                    "an executable candidate"
                ),
            )
        if runtime.phase == RuntimePhase.SELECT and not runtime.selection_made:
            if "candidate_choice" in runtime.asked_dimensions:
                return QuestionDecision(
                    False, reason="candidate choice was already asked"
                )
            if len(runtime.asked_dimensions) >= 2:
                return QuestionDecision(False, reason="question budget exhausted")
            return QuestionDecision(True, "candidate_choice")
        return QuestionDecision(
            False, reason=f"questions are not allowed in phase {runtime.phase.value}"
        )

    @staticmethod
    def commit(decision: QuestionDecision, runtime: TaskRuntime) -> None:
        if decision.allowed and decision.counts_against_budget and decision.dimension:
            runtime.commit_question(decision.dimension)
            if decision.dimension == "candidate_choice":
                runtime.phase = RuntimePhase.SELECT

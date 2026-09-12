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
        """Decide whether a model-authored question may be sent.

        The gate exists to stop *repeated* questions, never to stop the model
        from asking at all: trace comparison against the stock agent showed the
        winning baseline units are exactly the ones where the model asks which
        flavour, which address or what time it is, and the answer changes the
        consequential argument (E-048). Blocking those questions turned a
        partial-credit trajectory into a zero.
        """
        if not self.is_question(text):
            return QuestionDecision(True, counts_against_budget=False)
        if runtime.phase == RuntimePhase.READY_TO_PAY:
            if runtime.authorization.pay_declined:
                return QuestionDecision(False, reason="payment was declined")
            return QuestionDecision(
                True, "payment_confirmation", counts_against_budget=False
            )
        if runtime.phase == RuntimePhase.NEED_INFO:
            # A question is already pending the user's answer; asking another
            # one now would duplicate it.
            return QuestionDecision(
                False, reason="a question is already waiting for the user's answer"
            )
        if runtime.dimension_budget_spent:
            return QuestionDecision(False, reason="question budget exhausted")
        dimension = self._dimension_for(text)
        if dimension in runtime.asked_dimensions:
            return QuestionDecision(
                False, reason=f"the {dimension} question was already asked"
            )
        if (
            dimension == "candidate_choice"
            and runtime.forbid_redundant_candidate_question
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
        if dimension == "candidate_choice" and runtime.authorization.choice_delegated:
            return QuestionDecision(
                False,
                reason="the user delegated the choice; select a compliant candidate",
            )
        if (
            dimension == "candidate_choice"
            and runtime.authorization.candidate_choice_authorized
        ):
            return QuestionDecision(
                False,
                reason=(
                    "the direct execution request already authorizes candidate "
                    "selection; expand required details or commit without reconfirming"
                ),
            )
        if dimension == "candidate_choice" and runtime.phase == RuntimePhase.SELECT and (
            runtime.selection_made or runtime.authorization.choice_delegated
        ) and not runtime.authorization.create_authorized:
            # The user already endorsed or delegated the candidate, so asking
            # about the candidate again would loop; a question here can only be
            # about proceeding. A non-declining answer authorizes the write
            # (E-036).
            if "execution_confirmation" in runtime.asked_dimensions:
                return QuestionDecision(
                    False, reason="execution confirmation was already asked"
                )
            return QuestionDecision(True, "execution_confirmation")
        return QuestionDecision(True, dimension)

    @staticmethod
    def _dimension_for(text: str) -> str:
        """Classify a clarifying question by the decision dimension it asks."""
        content = text or ""
        markers = (
            ("address", ("送到哪", "送到哪里", "地址", "送家里", "送公司", "送医院")),
            ("time", ("几点", "什么时间", "什么时候", "哪天", "周六还", "周日还")),
            ("quantity", ("几杯", "几份", "几张", "几件", "数量", "多少人", "几人")),
            ("size", ("多大", "尺码", "尺寸", "几号", "大杯", "中杯")),
            ("preference_choice", ("哪个", "哪款", "哪家", "哪种", "选哪")),
        )
        for dimension, terms in markers:
            if any(term in content for term in terms):
                return dimension
        return "candidate_choice"

    @staticmethod
    def commit(decision: QuestionDecision, runtime: TaskRuntime) -> None:
        if decision.allowed and decision.counts_against_budget and decision.dimension:
            runtime.commit_question(decision.dimension)
            if decision.dimension == "candidate_choice":
                runtime.phase = RuntimePhase.SELECT
            if decision.dimension == "execution_confirmation":
                # The next user turn answers "shall I proceed?"; a
                # non-declining answer authorizes the write (E-036).
                runtime.execution_confirmation_pending = True

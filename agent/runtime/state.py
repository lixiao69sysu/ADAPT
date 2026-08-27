"""Per-subtask state machine and user authorization semantics."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from agent.decision import TaskSpec
from agent.intent import (
    accepts_visible_recommendation,
    has_create_authorization,
    selected_ordinal,
)


class RuntimePhase(str, Enum):
    START = "start"
    NEED_INFO = "need_info"
    SEARCH = "search"
    SELECT = "select"
    READY_TO_CREATE = "ready_to_create"
    WAIT_CREATE_RESULT = "wait_create_result"
    READY_TO_PAY = "ready_to_pay"
    WAIT_PAY_RESULT = "wait_pay_result"
    READY_TO_WORKFLOW = "ready_to_workflow"
    WAIT_WORKFLOW_RESULT = "wait_workflow_result"
    REPORT = "report"
    DONE = "done"
    UNSATISFIABLE = "unsatisfiable"


class PaymentIntent(str, Enum):
    """Contextual meaning of a reply to the framework payment question."""

    AUTHORIZE = "authorize"
    DECLINE = "decline"
    DEFER = "defer"
    SELF_PAY = "self_pay"
    QUESTION = "question"
    UNKNOWN = "unknown"


class PaymentDisposition(str, Enum):
    """Resolution of the separate payment authorization boundary."""

    UNRESOLVED = "unresolved"
    AUTHORIZED = "authorized"
    DECLINED = "declined"
    DEFERRED = "deferred"
    SELF_PAY = "self_pay"
    COMPLETED = "completed"


class UserEventKind(str, Enum):
    """One mutually exclusive primary meaning for an observed user turn."""

    PAYMENT_AUTHORIZE = "payment_authorize"
    PAYMENT_DECLINE = "payment_decline"
    PAYMENT_DEFER = "payment_defer"
    PAYMENT_SELF_PAY = "payment_self_pay"
    PAYMENT_QUESTION = "payment_question"
    PAYMENT_UNKNOWN = "payment_unknown"
    INFORMATION_ANSWER = "information_answer"
    CURRENT_CORRECTION = "current_correction"
    CANDIDATE_SELECTION = "candidate_selection"
    DELEGATION = "delegation"
    OTHER = "other"


_PAYMENT_EVENT_KINDS = frozenset(
    {
        UserEventKind.PAYMENT_AUTHORIZE,
        UserEventKind.PAYMENT_DECLINE,
        UserEventKind.PAYMENT_DEFER,
        UserEventKind.PAYMENT_SELF_PAY,
        UserEventKind.PAYMENT_QUESTION,
        UserEventKind.PAYMENT_UNKNOWN,
    }
)


@dataclass(frozen=True)
class UserEvent:
    """Typed output of the sole runtime user-message interpreter."""

    kind: UserEventKind
    text: str
    payment_intent: PaymentIntent = PaymentIntent.UNKNOWN
    selection_index: int = 0
    create_authorized: bool = False
    delegated: bool = False

    @property
    def is_payment(self) -> bool:
        return self.kind in _PAYMENT_EVENT_KINDS


@dataclass
class AuthorizationState:
    choice_delegated: bool = False
    candidate_choice_authorized: bool = False
    create_authorized: bool = False
    pay_authorized: bool = False
    pay_declined: bool = False
    payment_disposition: PaymentDisposition = PaymentDisposition.UNRESOLVED


_DELEGATION_MARKERS = (
    "随便",
    "你看着办",
    "看着办",
    "帮我选",
    "都行",
    "不太清楚",
    "你决定",
)
_CREATE_MARKERS = (
    "下单",
    "直接买",
    "帮我买",
    "帮我点",
    "帮我订",
    "预定",
    "预约",
    "团个券",
    "就这个",
    "第一双",
)
_PAY_AUTHORIZE_MARKERS = (
    "确认支付",
    "现在支付",
    "立即支付",
    "帮我付",
    "直接付",
    "支付吧",
    "付款吧",
    "付了吧",
)
_PAY_DECLINE_MARKERS = (
    "不用了",
    "不用付",
    "不要支付",
    "不需要支付",
    "别付",
    "不付了",
    "取消支付",
)
_PAY_DEFER_MARKERS = (
    "晚点再说",
    "稍后再说",
    "之后再说",
    "回头再说",
    "晚点再付",
    "稍后再付",
    "之后再付",
    "先不付",
    "暂时不付",
    "先放着",
    "先去忙",
)
_PAY_QUESTION_MARKERS = (
    "怎么支付",
    "如何支付",
    "支付方式",
    "怎么付款",
    "多少钱",
    "是否支付",
    "能否支付",
    "可以支付吗",
)
_PAY_CONTEXT_ACKS = {
    "可以",
    "可以的",
    "行",
    "好的",
    "好",
    "确认",
    "没问题",
    "嗯",
    "嗯嗯",
}
_REVISION_MARKERS = (
    "换个",
    "换成",
    "更换",
    "重新选",
    "重新找",
    "改成",
    "大点",
    "小点",
    "不是这个",
    "不是",
    "搞错",
    "我要的是",
    "怎么行",
)
_SIZE_RE = re.compile(
    r"\b(3[4-9]|4[0-9]|5[0-2])(?:\s*[-~到]\s*(3[4-9]|4[0-9]|5[0-2]))?\s*码?"
)
_COUNT_RE = re.compile(r"([一二两三四五六七八九十\d]+)\s*(?:人|位|张|份|双|个)")


def classify_payment_intent(
    text: str, *, payment_question_sent: bool
) -> PaymentIntent:
    """Classify a payment reply without mutating runtime state.

    Short acknowledgements such as ``可以`` are authorization only inside an
    active payment-confirmation exchange. Transaction/category words such as
    ``买票`` are deliberately not authorization evidence.
    """

    if not payment_question_sent:
        return PaymentIntent.UNKNOWN
    content = " ".join((text or "").strip().split())
    if not content:
        return PaymentIntent.UNKNOWN
    if any(marker in content for marker in _PAY_QUESTION_MARKERS) or (
        ("?" in content or "？" in content)
        and not any(marker in content for marker in _PAY_AUTHORIZE_MARKERS)
    ):
        return PaymentIntent.QUESTION
    if re.search(
        r"(?:我?自己.{0,3}(?:付|支付|付款|弄|处理)|我来(?:付|支付|付款|处理))",
        content,
    ):
        return PaymentIntent.SELF_PAY
    if any(marker in content for marker in _PAY_DEFER_MARKERS):
        return PaymentIntent.DEFER
    if any(marker in content for marker in _PAY_DECLINE_MARKERS):
        return PaymentIntent.DECLINE
    if any(marker in content for marker in _PAY_AUTHORIZE_MARKERS):
        return PaymentIntent.AUTHORIZE
    normalized = re.sub(r"[\s，。！？!?、,.]+", "", content)
    if normalized in _PAY_CONTEXT_ACKS:
        return PaymentIntent.AUTHORIZE
    return PaymentIntent.UNKNOWN


def classify_user_event(
    text: str,
    *,
    phase: RuntimePhase,
    payment_question_sent: bool,
    pending_question_dimension: str = "",
    prior_correction: bool = False,
) -> UserEvent:
    """Interpret a user turn once, with the current phase as its boundary."""

    content = (text or "").strip()
    selection_index = selected_ordinal(content)
    if not selection_index and accepts_visible_recommendation(content):
        selection_index = 1
    delegated = any(marker in content for marker in _DELEGATION_MARKERS)
    create_authorized = has_create_authorization(content)

    if phase == RuntimePhase.READY_TO_PAY:
        if any(marker in content for marker in _REVISION_MARKERS):
            kind = UserEventKind.CURRENT_CORRECTION
            intent = PaymentIntent.UNKNOWN
        else:
            intent = classify_payment_intent(
                content, payment_question_sent=payment_question_sent
            )
            kind = {
                PaymentIntent.AUTHORIZE: UserEventKind.PAYMENT_AUTHORIZE,
                PaymentIntent.DECLINE: UserEventKind.PAYMENT_DECLINE,
                PaymentIntent.DEFER: UserEventKind.PAYMENT_DEFER,
                PaymentIntent.SELF_PAY: UserEventKind.PAYMENT_SELF_PAY,
                PaymentIntent.QUESTION: UserEventKind.PAYMENT_QUESTION,
                PaymentIntent.UNKNOWN: UserEventKind.PAYMENT_UNKNOWN,
            }[intent]
        return UserEvent(
            kind,
            content,
            payment_intent=intent,
            selection_index=selection_index,
            create_authorized=create_authorized,
            delegated=delegated,
        )

    if pending_question_dimension:
        kind = UserEventKind.INFORMATION_ANSWER
    elif any(
        marker in content
        for marker in (
            "不是",
            "不对",
            "错了",
            "我说的是",
            "应该是",
            "改成",
            "换成",
            "换个",
            "搞错",
            "我要的是",
        )
    ) or (
        phase
        in {
            RuntimePhase.SELECT,
            RuntimePhase.READY_TO_CREATE,
            RuntimePhase.DONE,
        }
        and any(marker in content for marker in ("不要", "别"))
    ) or (prior_correction and any(marker in content for marker in ("不要", "别"))):
        kind = UserEventKind.CURRENT_CORRECTION
        # Bind a replacement ordinal from the correction clause, not from a
        # rejected candidate mentioned earlier in the same sentence.
        for marker in ("换成", "改成", "我要的是", "应该是"):
            marker_at = content.rfind(marker)
            if marker_at >= 0:
                replacement = selected_ordinal(content[marker_at + len(marker) :])
                if replacement:
                    selection_index = replacement
                break
    elif selection_index:
        kind = UserEventKind.CANDIDATE_SELECTION
    elif delegated:
        kind = UserEventKind.DELEGATION
    else:
        kind = UserEventKind.OTHER
    return UserEvent(
        kind,
        content,
        selection_index=selection_index,
        create_authorized=create_authorized,
        delegated=delegated,
    )


@dataclass
class TaskRuntime:
    spec: TaskSpec
    authorization: AuthorizationState = field(default_factory=AuthorizationState)
    phase: RuntimePhase = RuntimePhase.START
    resolved_slots: dict[str, str] = field(default_factory=dict)
    asked_dimensions: set[str] = field(default_factory=set)
    pending_question_dimension: str = ""
    pending_question_id: str = ""
    pending_question_tool_family: str = ""
    pending_question_expected_type: str = ""
    pending_question_persist_as_preference: bool = False
    selected_candidate_id: str = ""
    planned_create_tool: str = ""
    planned_create_arguments: dict[str, object] = field(default_factory=dict)
    selection_made: bool = False
    validation_failures: int = 0
    force_decision_after_candidates: bool = False
    require_payment_completion_check: bool = False
    forbid_redundant_candidate_question: bool = False
    forbid_question_with_tool: bool = False
    last_user_answer: str = ""
    last_tool_error: str = ""
    revision_requested: bool = False
    execution_ready: bool = False
    payment_question_sent: bool = False
    payment_round: int = 0
    payment_question_round: int = 0
    authorized_payment_round: int = 0
    payment_clarification_pending: bool = False
    payment_clarifications: int = 0
    current_correction_count: int = 0
    write_succeeded: bool = False
    events: list[dict] = field(default_factory=list)

    @classmethod
    def begin(cls, spec: TaskSpec) -> TaskRuntime:
        runtime = cls(spec=spec, resolved_slots=dict(spec.resolved_slots))
        runtime.authorization.create_authorized = spec.action == "commit"
        # A direct execution request delegates the concrete candidate choice
        # unless a genuinely critical slot still needs clarification.
        runtime.authorization.candidate_choice_authorized = spec.action == "commit"
        runtime.phase = (
            RuntimePhase.NEED_INFO if runtime.critical_gaps() else RuntimePhase.SEARCH
        )
        runtime.record("begin", instruction=spec.instruction, phase=runtime.phase.value)
        return runtime

    def has_current_payment_question(self) -> bool:
        """Whether a payment confirmation was emitted for the active order round."""
        return (
            self.phase == RuntimePhase.READY_TO_PAY
            and self.payment_round > 0
            and self.payment_question_sent
            and self.payment_question_round == self.payment_round
        )

    def commit_payment_question(self) -> None:
        """Commit a payment question only after it has actually been emitted."""
        if self.payment_round <= 0:
            return
        if self.has_current_payment_question():
            self.payment_clarifications += 1
            self.payment_clarification_pending = False
        else:
            self.payment_question_sent = True
            self.payment_question_round = self.payment_round
        self.record("payment_question", payment_round=self.payment_round)

    def can_execute_payment(self) -> bool:
        """Require authorization from the confirmation boundary of this order round."""
        auth = self.authorization
        return (
            self.phase == RuntimePhase.READY_TO_PAY
            and self.has_current_payment_question()
            and auth.payment_disposition == PaymentDisposition.AUTHORIZED
            and auth.pay_authorized
            and not auth.pay_declined
            and self.authorized_payment_round == self.payment_round
        )

    def critical_gaps(self) -> list[str]:
        gaps = [
            slot for slot in self.spec.unknown_slots if slot not in self.resolved_slots
        ]
        # Account aliases are resolvable without asking.
        return [
            slot
            for slot in gaps
            if slot not in {"address", "product", "shop_or_service", "city"}
        ]

    def apply_memory_slots(self, slots: dict[str, str]) -> None:
        """Resolve stable preference-choice gaps without replacing live answers."""
        applied = {}
        for dimension, value in slots.items():
            if dimension in self.spec.unknown_slots and dimension not in self.resolved_slots:
                self.resolved_slots[dimension] = value
                applied[dimension] = value
        if applied:
            self.record("memory_slots", dimensions=sorted(applied))
            self._advance_from_observation()

    def observe_user(self, text: str) -> UserEvent:
        content = (text or "").strip()
        event = classify_user_event(
            content,
            phase=self.phase,
            payment_question_sent=self.has_current_payment_question(),
            pending_question_dimension=self.pending_question_dimension,
            prior_correction=self.current_correction_count > 0,
        )
        self.last_user_answer = content[:240]
        if event.delegated:
            self.authorization.choice_delegated = True
        selection_authorizes = (
            self.spec.action == "commit"
            and self.phase in {RuntimePhase.SELECT, RuntimePhase.DONE}
            and bool(event.selection_index or re.search(r"就这个|选这个", content))
        )
        if event.create_authorized or selection_authorizes:
            self.authorization.create_authorized = True
            self.authorization.candidate_choice_authorized = True
        if event.selection_index or re.search(r"就这个|选这个", content):
            self.selection_made = True
        if self.pending_question_dimension:
            self._record_slot_answer(self.pending_question_dimension, content)
            self.asked_dimensions.add(self.pending_question_dimension)
            self.pending_question_dimension = ""
            self.pending_question_id = ""
            self.pending_question_tool_family = ""
            self.pending_question_expected_type = ""
            self.pending_question_persist_as_preference = False
        can_revise = self.phase == RuntimePhase.READY_TO_PAY or (
            self.phase == RuntimePhase.DONE and not self.write_succeeded
        )
        if can_revise and event.kind == UserEventKind.CURRENT_CORRECTION:
            self.current_correction_count += 1
            self.revision_requested = True
            self.authorization.pay_authorized = False
            self.authorization.pay_declined = False
            self.authorization.payment_disposition = PaymentDisposition.UNRESOLVED
            self.authorized_payment_round = 0
            self.payment_question_sent = False
            self.payment_question_round = 0
            self.payment_clarification_pending = False
            self.selection_made = False
            self.phase = RuntimePhase.SEARCH
        elif self.phase == RuntimePhase.READY_TO_PAY:
            intent = event.payment_intent
            self._apply_payment_intent(intent)
            if intent in {
                PaymentIntent.DECLINE,
                PaymentIntent.DEFER,
                PaymentIntent.SELF_PAY,
            }:
                self.phase = RuntimePhase.DONE
        else:
            if event.kind == UserEventKind.CURRENT_CORRECTION:
                self.current_correction_count += 1
            self._advance_from_observation()
        # A recommendation-only task may legitimately become a transaction
        # after the user selects a currently visible candidate. Reopen the
        # terminal state instead of repeating the recommendation forever.
        if self.phase == RuntimePhase.DONE and not self.write_succeeded:
            if self.selection_made and self.authorization.create_authorized:
                self.phase = (
                    RuntimePhase.READY_TO_CREATE
                    if self.execution_ready
                    else RuntimePhase.SELECT
                )
        self.record(
            "user",
            text=content,
            phase=self.phase.value,
            delegated=self.authorization.choice_delegated,
            create_authorized=self.authorization.create_authorized,
            payment_disposition=self.authorization.payment_disposition.value,
            user_event=event.kind.value,
        )
        return event

    def _apply_payment_intent(self, intent: PaymentIntent) -> None:
        """Apply one contextual payment decision while preserving compatibility flags."""

        auth = self.authorization
        if intent == PaymentIntent.AUTHORIZE:
            auth.payment_disposition = PaymentDisposition.AUTHORIZED
            auth.pay_authorized = True
            auth.pay_declined = False
            self.authorized_payment_round = (
                self.payment_round if self.has_current_payment_question() else 0
            )
            self.payment_clarification_pending = False
        elif intent == PaymentIntent.DECLINE:
            auth.payment_disposition = PaymentDisposition.DECLINED
            auth.pay_authorized = False
            auth.pay_declined = True
            self.authorized_payment_round = 0
            self.payment_clarification_pending = False
        elif intent == PaymentIntent.DEFER:
            auth.payment_disposition = PaymentDisposition.DEFERRED
            auth.pay_authorized = False
            auth.pay_declined = True
            self.authorized_payment_round = 0
            self.payment_clarification_pending = False
        elif intent == PaymentIntent.SELF_PAY:
            auth.payment_disposition = PaymentDisposition.SELF_PAY
            auth.pay_authorized = False
            auth.pay_declined = True
            self.authorized_payment_round = 0
            self.payment_clarification_pending = False
        elif intent in {PaymentIntent.QUESTION, PaymentIntent.UNKNOWN}:
            auth.payment_disposition = PaymentDisposition.UNRESOLVED
            auth.pay_authorized = False
            auth.pay_declined = False
            self.authorized_payment_round = 0
            if self.payment_clarifications == 0:
                self.payment_clarification_pending = True
            else:
                auth.payment_disposition = PaymentDisposition.DEFERRED
                auth.pay_declined = True
                self.payment_clarification_pending = False
                self.phase = RuntimePhase.DONE
        self.record("payment_intent", intent=intent.value)

    def _record_slot_answer(self, dimension: str, text: str) -> None:
        if self.pending_question_id:
            value = text.strip()[:160]
            if self.pending_question_expected_type in {"integer", "number"}:
                match = re.search(r"-?\d+(?:\.\d+)?", text)
                if match:
                    value = match.group(0)
            if any(marker in text for marker in _DELEGATION_MARKERS):
                value = "__delegated__"
            self.resolved_slots[dimension] = value
            self.record(
                "question_answer_bound",
                question_id=self.pending_question_id,
                tool_family=self.pending_question_tool_family,
                argument=dimension,
            )
            return
        if dimension == "size":
            match = _SIZE_RE.search(text)
            if match:
                self.resolved_slots[dimension] = match.group(0).rstrip("码")
                return
        if dimension == "quantity":
            match = _COUNT_RE.search(text)
            if match:
                self.resolved_slots[dimension] = match.group(1)
                return
        if dimension == "caffeine":
            if any(marker in text for marker in ("低咖啡因", "脱因", "下午", "晚上", "晚间")):
                self.resolved_slots[dimension] = "低咖啡因"
                return
            if any(marker in text for marker in ("高咖啡因", "上午", "早上", "早晨")):
                self.resolved_slots[dimension] = "高咖啡因"
                return
        if dimension == "taste":
            taste_families = (
                ("麻辣", ("麻辣", "牛油", "红油", "辣锅")),
                ("菌汤", ("菌汤", "菌菇", "竹荪")),
                ("番茄", ("番茄",)),
                ("清汤", ("清汤", "清淡", "养生")),
            )
            for canonical, markers in taste_families:
                if any(marker in text for marker in markers):
                    self.resolved_slots[dimension] = canonical
                    return
        if any(marker in text for marker in _DELEGATION_MARKERS):
            self.resolved_slots[dimension] = "__delegated__"
            return
        self.resolved_slots[dimension] = text[:80]

    def _advance_from_observation(self) -> None:
        gaps = self.critical_gaps()
        unresolved = [
            gap for gap in gaps if self.resolved_slots.get(gap) != "__delegated__"
        ]
        if unresolved and not self.authorization.choice_delegated:
            self.phase = RuntimePhase.NEED_INFO
        elif self.phase not in {
            RuntimePhase.WAIT_CREATE_RESULT,
            RuntimePhase.WAIT_PAY_RESULT,
            RuntimePhase.DONE,
        }:
            self.phase = RuntimePhase.SEARCH

    def next_question_dimension(self) -> str:
        if len(self.asked_dimensions) >= 2 or self.authorization.choice_delegated:
            return ""
        for dimension in self.critical_gaps():
            if (
                dimension not in self.asked_dimensions
                and dimension not in self.resolved_slots
            ):
                return dimension
        return ""

    def commit_question(
        self,
        dimension: str,
        *,
        question_id: str = "",
        tool_family: str = "",
        expected_type: str = "",
        persist_as_preference: bool = False,
    ) -> None:
        self.pending_question_dimension = dimension
        self.pending_question_id = question_id
        self.pending_question_tool_family = tool_family
        self.pending_question_expected_type = expected_type
        self.pending_question_persist_as_preference = persist_as_preference
        self.asked_dimensions.add(dimension)
        self.phase = RuntimePhase.NEED_INFO
        self.record("question", dimension=dimension)

    def observe_candidates(self, count: int, execution_ready: bool = True) -> None:
        self.execution_ready = execution_ready
        if count:
            self.phase = RuntimePhase.SELECT
            if (
                self.authorization.choice_delegated
                or self.authorization.candidate_choice_authorized
                or self.selection_made
                or self.force_decision_after_candidates
            ) and self.authorization.create_authorized and execution_ready:
                self.phase = RuntimePhase.READY_TO_CREATE
        self.record(
            "candidates",
            count=count,
            execution_ready=execution_ready,
            phase=self.phase.value,
        )

    def apply_candidate_decision(self, decision) -> None:
        """Apply the single candidate authority's immutable result."""
        self.execution_ready = bool(decision.admissible)
        if decision.selected is not None:
            self.planned_create_tool = decision.selected.create_tool
            self.planned_create_arguments = decision.selected.as_arguments()
        else:
            self.planned_create_tool = ""
            self.planned_create_arguments = {}
        terminal_selection_reopened = (
            self.phase == RuntimePhase.DONE
            and self.selection_made
            and self.authorization.create_authorized
        )
        if terminal_selection_reopened or self.phase not in {
            RuntimePhase.WAIT_CREATE_RESULT,
            RuntimePhase.WAIT_PAY_RESULT,
            RuntimePhase.DONE,
            RuntimePhase.UNSATISFIABLE,
        }:
            self.phase = decision.next_phase
        self.record(
            "candidate_decision",
            phase=self.phase.value,
            admissible=len(decision.admissible),
            selected=(
                decision.selected.leaf_ids if decision.selected is not None else ()
            ),
            selection_basis=decision.selection_basis,
        )

    def select_candidate(
        self,
        candidate_id: str,
        *,
        execution_ready: bool = False,
        decision=None,
    ) -> None:
        """Record a user's concrete choice without replaying SEARCH state.

        Candidate discovery and candidate selection are different events.  A
        selection may advance SELECT to READY_TO_CREATE, but must never emit a
        second candidate-observation event or reopen search policy.
        """
        self.selected_candidate_id = candidate_id
        self.selection_made = True
        if decision is not None:
            self.apply_candidate_decision(decision)
            execution_ready = bool(decision.admissible)
        else:
            self.execution_ready = execution_ready
            if self.authorization.create_authorized and execution_ready:
                self.phase = RuntimePhase.READY_TO_CREATE
            else:
                self.phase = RuntimePhase.SELECT
        self.record(
            "selection",
            candidate_id=candidate_id,
            execution_ready=execution_ready,
            phase=self.phase.value,
        )

    def observe_tool_result(
        self, tool_name: str, content: str, error: bool = False, role: str = ""
    ) -> None:
        from agent.runtime.outcomes import ToolOutcomeNormalizer

        outcome = ToolOutcomeNormalizer.normalize(
            tool_name=tool_name,
            tool_role=role,
            content=content,
            error=error,
        )
        self.observe_tool_outcome(tool_name, outcome, raw_error=str(content or ""))

    def observe_tool_outcome(self, tool_name: str, outcome, *, raw_error: str = "") -> None:
        from agent.runtime.outcomes import ToolEffect

        if not outcome.ok:
            self.last_tool_error = raw_error[:240]
            if self.phase == RuntimePhase.WAIT_CREATE_RESULT:
                self.phase = RuntimePhase.READY_TO_CREATE
            elif self.phase == RuntimePhase.WAIT_PAY_RESULT:
                self.phase = RuntimePhase.READY_TO_PAY
            elif self.phase == RuntimePhase.WAIT_WORKFLOW_RESULT:
                self.phase = RuntimePhase.READY_TO_WORKFLOW
            self.record("tool_error", tool=tool_name, content=raw_error[:200])
            return
        if outcome.effect in {ToolEffect.CREATED, ToolEffect.CREATED_PENDING_PAYMENT}:
            self.revision_requested = False
            self.write_succeeded = True
            self.phase = (
                RuntimePhase.READY_TO_PAY
                if outcome.effect == ToolEffect.CREATED_PENDING_PAYMENT
                else RuntimePhase.DONE
            )
            if outcome.effect == ToolEffect.CREATED_PENDING_PAYMENT:
                self.payment_round += 1
            self.payment_question_sent = False
            self.payment_question_round = 0
            self.authorized_payment_round = 0
            self.payment_clarification_pending = False
            self.payment_clarifications = 0
            self.authorization.pay_authorized = False
            self.authorization.pay_declined = False
            self.authorization.payment_disposition = PaymentDisposition.UNRESOLVED
        elif outcome.effect in {
            ToolEffect.PAID,
            ToolEffect.CANCELLED,
            ToolEffect.MODIFIED,
        }:
            self.write_succeeded = True
            self.phase = RuntimePhase.DONE
            if outcome.effect == ToolEffect.PAID:
                self.authorization.payment_disposition = PaymentDisposition.COMPLETED
        self.record(
            "tool_result",
            tool=tool_name,
            effect=outcome.effect.value,
            phase=self.phase.value,
        )

    def mark_proposal(self, role: str) -> None:
        if role == "create":
            self.phase = RuntimePhase.WAIT_CREATE_RESULT
        elif role == "pay":
            self.phase = RuntimePhase.WAIT_PAY_RESULT
        elif role in {"cancel", "modify"}:
            self.phase = RuntimePhase.WAIT_WORKFLOW_RESULT
        self.record("proposal", role=role, phase=self.phase.value)

    def observe_workflow_state(self, *, has_state: bool) -> None:
        """Advance state-backed tasks after one successful observable lookup."""
        if self.spec.action == "modify":
            self.phase = (
                RuntimePhase.READY_TO_WORKFLOW
                if has_state
                else RuntimePhase.REPORT
            )
        elif any(
            marker in self.spec.instruction.casefold()
            for marker in (
                "状态",
                "订单",
                "预约记录",
                "预订记录",
                "status",
                "order",
                "booking",
                "reservation",
            )
        ):
            self.phase = RuntimePhase.REPORT
        self.record("workflow_state", has_state=has_state, phase=self.phase.value)

    def workflow_role(self) -> str:
        """Return the irreversible role requested by the current instruction."""
        text = self.spec.instruction.casefold()
        return "cancel" if any(marker in text for marker in ("取消", "退票", "cancel", "refund")) else "modify"

    def has_unresolved_payment_failure(self, pending_payment_ids) -> bool:
        """Whether the agent, rather than the user, failed to progress payment."""
        if self.phase != RuntimePhase.READY_TO_PAY or not pending_payment_ids:
            return False
        if self.authorization.payment_disposition in {
            PaymentDisposition.DECLINED,
            PaymentDisposition.DEFERRED,
            PaymentDisposition.SELF_PAY,
        } or self.authorization.pay_declined:
            return False
        # If payment was already authorized, failing to call PAY is actionable.
        if self.can_execute_payment():
            return True
        # Without authorization, asking once is the correct terminal action.
        return not self.has_current_payment_question()

    def record(self, event: str, **payload) -> None:
        self.events.append({"event": event, **payload})

    def render(self) -> str:
        auth = self.authorization
        gaps = self.critical_gaps()
        return (
            "## Deterministic runtime\n"
            f"PHASE={self.phase.value}\n"
            f"CHOICE_DELEGATED={auth.choice_delegated}\n"
            f"CANDIDATE_CHOICE_AUTHORIZED={auth.candidate_choice_authorized}\n"
            f"CREATE_AUTHORIZED={auth.create_authorized}\n"
            f"SELECTION_MADE={self.selection_made}\n"
            f"EXECUTION_READY={self.execution_ready}\n"
            f"LEARNED_FORCE_DECISION={self.force_decision_after_candidates}\n"
            f"LEARNED_PAYMENT_CHECK={self.require_payment_completion_check}\n"
            f"PAY_AUTHORIZED={auth.pay_authorized}\n"
            f"PAY_DECLINED={auth.pay_declined}\n"
            f"PAYMENT_DISPOSITION={auth.payment_disposition.value}\n"
            f"PAYMENT_ROUND={self.payment_round}\n"
            f"PAYMENT_QUESTION_ROUND={self.payment_question_round}\n"
            f"AUTHORIZED_PAYMENT_ROUND={self.authorized_payment_round}\n"
            f"CRITICAL_GAPS={','.join(gaps) or 'none'}\n"
            f"RECENT_USER_ANSWER={self.last_user_answer or 'none'}\n"
            f"LAST_TOOL_ERROR={self.last_tool_error or 'none'}\n"
            "Obey the phase and use only the tools exposed in this call."
        )

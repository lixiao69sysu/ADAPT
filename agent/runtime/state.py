"""Per-subtask state machine and user authorization semantics."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from agent.decision import TaskSpec


class RuntimePhase(str, Enum):
    START = "start"
    NEED_INFO = "need_info"
    SEARCH = "search"
    SELECT = "select"
    READY_TO_CREATE = "ready_to_create"
    WAIT_CREATE_RESULT = "wait_create_result"
    READY_TO_PAY = "ready_to_pay"
    WAIT_PAY_RESULT = "wait_pay_result"
    DONE = "done"
    UNSATISFIABLE = "unsatisfiable"


@dataclass
class AuthorizationState:
    choice_delegated: bool = False
    candidate_choice_authorized: bool = False
    create_authorized: bool = False
    pay_authorized: bool = False
    pay_declined: bool = False


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
_PAY_MARKERS = ("支付", "付款", "帮我付", "直接付", "买票", "购票")
# General completion-style purchase phrasings ("帮我团一张", "订个", "来一份").
# A bare "帮我看看有没有团购券" must NOT authorize a write, so the pattern
# requires the action verb to be followed by a quantity/unit.
_CREATE_INTENT_RE = re.compile(
    r"(?:帮我|给我|替我|麻烦)?(?:团|买|订|下|来)"
    r"(?:一|两|二|三|\d+)?(?:张|个|份|单|杯|碗|套)"
)
_PAY_DECLINE_MARKERS = ("自己付", "不用付", "不要支付", "不需要支付")
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


@dataclass
class TaskRuntime:
    spec: TaskSpec
    authorization: AuthorizationState = field(default_factory=AuthorizationState)
    phase: RuntimePhase = RuntimePhase.START
    resolved_slots: dict[str, str] = field(default_factory=dict)
    asked_dimensions: set[str] = field(default_factory=set)
    pending_question_dimension: str = ""
    selected_candidate_id: str = ""
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

    def observe_user(self, text: str) -> None:
        content = (text or "").strip()
        self.last_user_answer = content[:240]
        if any(marker in content for marker in _DELEGATION_MARKERS):
            self.authorization.choice_delegated = True
        if any(marker in content for marker in _CREATE_MARKERS) or _CREATE_INTENT_RE.search(
            content
        ):
            self.authorization.create_authorized = True
        if re.search(r"第[一二三四五1-5](?:个|双|款|家|项)?|就这个|选这个", content):
            self.selection_made = True
        if any(marker in content for marker in _PAY_MARKERS):
            self.authorization.pay_authorized = True
        if any(marker in content for marker in _PAY_DECLINE_MARKERS) or re.search(
            r"(?:我?自己.{0,3}付|我来付)", content
        ):
            self.authorization.pay_declined = True
            self.authorization.pay_authorized = False
        if self.pending_question_dimension:
            self._record_slot_answer(self.pending_question_dimension, content)
            self.asked_dimensions.add(self.pending_question_dimension)
            self.pending_question_dimension = ""
        can_revise = self.phase == RuntimePhase.READY_TO_PAY or (
            self.phase == RuntimePhase.DONE and not self.write_succeeded
        )
        if can_revise and any(
            marker in content for marker in _REVISION_MARKERS
        ):
            self.revision_requested = True
            self.authorization.pay_authorized = False
            self.authorization.pay_declined = False
            self.selection_made = False
            self.phase = RuntimePhase.SEARCH
        elif self.phase == RuntimePhase.READY_TO_PAY:
            # Payment is a separate authorization boundary.  A confirmation
            # must keep the runtime at READY_TO_PAY so the pay tool becomes
            # available; a refusal completes the task without payment.
            if self.authorization.pay_declined:
                self.phase = RuntimePhase.DONE
        else:
            self._advance_from_observation()
        self.record(
            "user",
            text=content,
            phase=self.phase.value,
            delegated=self.authorization.choice_delegated,
            create_authorized=self.authorization.create_authorized,
        )

    def _record_slot_answer(self, dimension: str, text: str) -> None:
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

    def commit_question(self, dimension: str) -> None:
        self.pending_question_dimension = dimension
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

    def observe_tool_result(
        self, tool_name: str, content: str, error: bool = False
    ) -> None:
        text = content or ""
        if error:
            self.last_tool_error = text[:240]
            self.record("tool_error", tool=tool_name, content=text[:200])
            return
        if tool_name.startswith("create_") or tool_name in {
            "instore_book",
            "instore_reservation",
        }:
            self.revision_requested = False
            self.write_succeeded = True
            self.phase = (
                RuntimePhase.READY_TO_PAY if ("unpaid" in text) else RuntimePhase.DONE
            )
            self.payment_question_sent = False
        elif tool_name.startswith("pay_") and (
            "successful" in text.lower() or "成功" in text
        ):
            self.write_succeeded = True
            self.phase = RuntimePhase.DONE
        self.record("tool_result", tool=tool_name, phase=self.phase.value)

    def mark_proposal(self, role: str) -> None:
        if role == "create":
            self.phase = RuntimePhase.WAIT_CREATE_RESULT
        elif role == "pay":
            self.phase = RuntimePhase.WAIT_PAY_RESULT
        self.record("proposal", role=role, phase=self.phase.value)

    def has_unresolved_payment_failure(self, pending_payment_ids) -> bool:
        """Whether the agent, rather than the user, failed to progress payment."""
        if self.phase != RuntimePhase.READY_TO_PAY or not pending_payment_ids:
            return False
        if self.authorization.pay_declined:
            return False
        # If payment was already authorized, failing to call PAY is actionable.
        if self.authorization.pay_authorized:
            return True
        # Without authorization, asking once is the correct terminal action.
        return not self.payment_question_sent

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
            f"CRITICAL_GAPS={','.join(gaps) or 'none'}\n"
            f"RECENT_USER_ANSWER={self.last_user_answer or 'none'}\n"
            f"LAST_TOOL_ERROR={self.last_tool_error or 'none'}\n"
            "Obey the phase and use only the tools exposed in this call."
        )

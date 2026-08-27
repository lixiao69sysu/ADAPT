import json

from agent.action_audit import audit
from agent.adapt_agent import ADAPTAgent
from agent.decision import CandidateLedger, TaskSpec
from agent.lessons import ExecutionLessonStore
from agent.runtime import (
    DebugEventStore,
    FailureOwner,
    PaymentIntent,
    RuntimePhase,
    RuntimePolicyStore,
    OperationJournal,
    TaskRuntime,
    UserEvent,
    UserEventKind,
    attribute_preflight_failure,
    classify_user_event,
)


def test_same_negative_phrase_is_phase_scoped_to_payment_not_correction():
    payment = classify_user_event(
        "不用了，我自己弄就行",
        phase=RuntimePhase.READY_TO_PAY,
        payment_question_sent=True,
    )
    selection = classify_user_event(
        "不要第一个，换成第二个",
        phase=RuntimePhase.SELECT,
        payment_question_sent=False,
    )

    assert payment.kind == UserEventKind.PAYMENT_SELF_PAY
    assert payment.payment_intent == PaymentIntent.SELF_PAY
    assert selection.kind == UserEventKind.CURRENT_CORRECTION
    assert selection.selection_index == 2


def test_pending_question_answer_owns_negative_words_before_correction_logic():
    event = classify_user_event(
        "不用加辣",
        phase=RuntimePhase.NEED_INFO,
        payment_question_sent=False,
        pending_question_dimension="taste",
    )
    assert event.kind == UserEventKind.INFORMATION_ANSWER


def test_payment_event_cannot_create_user_correction_lesson():
    agent = object.__new__(ADAPTAgent)
    agent.enable_lessons = True
    agent.task_spec = TaskSpec.compile("请执行一个虚构交易")
    agent.lessons = ExecutionLessonStore("synthetic-user")
    agent.runtime_policies = RuntimePolicyStore("synthetic-user")
    agent.debug = DebugEventStore()
    payment = UserEvent(
        UserEventKind.PAYMENT_DECLINE,
        "不用支付",
        payment_intent=PaymentIntent.DECLINE,
    )

    agent._record_user_correction_lesson(payment)

    assert agent.lessons.all() == []
    assert agent.debug.events[-1]["event"] == "lesson_suppressed"


def test_typed_current_correction_can_create_only_soft_session_lesson():
    agent = object.__new__(ADAPTAgent)
    agent.enable_lessons = True
    agent.task_spec = TaskSpec.compile("请执行一个虚构交易")
    agent.lessons = ExecutionLessonStore("synthetic-user")
    agent.runtime_policies = RuntimePolicyStore("synthetic-user")
    agent.runtime_policies.begin_subtask("synthetic-user")
    agent.ledger = CandidateLedger()
    agent.operations = OperationJournal()
    agent.debug = DebugEventStore()
    event = UserEvent(UserEventKind.CURRENT_CORRECTION, "不是这个，换一个")

    agent._record_user_correction_lesson(event)

    assert [lesson.failure_class for lesson in agent.lessons.all()] == [
        "user_correction"
    ]
    assert agent.runtime_policies.rules() == []


def test_preflight_attribution_distinguishes_framework_model_and_validator():
    framework = attribute_preflight_failure(
        ["tool is not exposed"], allowed_tool_count=0, proposed_call_count=1
    )
    model = attribute_preflight_failure(
        ["required argument missing"], allowed_tool_count=1, proposed_call_count=1
    )
    validator = attribute_preflight_failure(
        ["candidate conflicts with a hard constraint"],
        allowed_tool_count=1,
        proposed_call_count=1,
    )

    assert framework.owner == FailureOwner.FRAMEWORK
    assert model.owner == FailureOwner.MODEL_POLICY
    assert validator.owner == FailureOwner.VALIDATOR


def test_trace_audit_flags_payment_to_correction_harness_conflict(tmp_path):
    path = tmp_path / "visible.jsonl"
    events = [
        {
            "event": "user_observation",
            "user_event": "payment_decline",
        },
        {"event": "lesson_recorded", "failure_class": "user_correction"},
        {
            "event": "failure_attributed",
            "owner": "harness",
        },
        {
            "event": "decision_surface",
            "phase": "ready_to_create",
            "allowed_tool_count": 0,
        },
    ]
    path.write_text(
        "\n".join(json.dumps(event, ensure_ascii=False) for event in events),
        encoding="utf-8",
    )

    report = audit(path)

    assert report["harness_event_conflicts"] == 1
    assert report["failure_attribution_owners"] == {"harness": 1}
    assert report["blocked_action_surfaces"] == 1


def test_trace_audit_replays_legacy_payment_boundary_without_raw_text(tmp_path):
    path = tmp_path / "legacy-visible.jsonl"
    events = [
        {"event": "payment_question"},
        {"event": "user_observation", "phase": "done"},
        {"event": "lesson_recorded", "failure_class": "user_correction"},
    ]
    path.write_text(
        "\n".join(json.dumps(event, ensure_ascii=False) for event in events),
        encoding="utf-8",
    )

    report = audit(path)

    assert report["typed_user_events"] == {"inferred_payment_reply": 1}
    assert report["harness_event_conflicts"] == 1

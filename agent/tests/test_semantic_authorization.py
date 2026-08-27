"""Open-world turn composition and scoped authorization gates."""

from agent.decision import TaskSpec
from agent.runtime import (
    PaymentDisposition,
    RuntimePhase,
    SemanticActKind,
    TaskRuntime,
    interpret_user_turn,
)


def test_one_turn_preserves_selection_correction_create_and_self_pay():
    interpretation = interpret_user_turn(
        "第一个可以，但地址改成公司，直接下单，支付我自己来付",
        phase=RuntimePhase.READY_TO_PAY,
        payment_question_sent=True,
    )

    assert interpretation.has(SemanticActKind.CANDIDATE_SELECTION)
    assert interpretation.has(SemanticActKind.CURRENT_CORRECTION)
    assert interpretation.has(SemanticActKind.CREATE_AUTHORIZATION)
    assert interpretation.has(SemanticActKind.PAYMENT_SELF_PAY)
    assert all(span.text for span in interpretation.evidence_spans)


def test_short_ack_never_authorizes_without_payment_question_context():
    interpretation = interpret_user_turn(
        "可以", phase=RuntimePhase.SELECT, payment_question_sent=False
    )
    assert not interpretation.has(SemanticActKind.PAYMENT_AUTHORIZE)


def test_create_grant_is_candidate_scoped_and_revision_expires_it():
    runtime = TaskRuntime.begin(TaskSpec.compile("帮我买一个虚构物件"))
    runtime.authorization.create_authorized = True
    runtime.grant_authorization(
        "create", candidate_id="glyph-7", snapshot_id="1:3", evidence="第一个"
    )

    assert runtime.can_execute_create("glyph-7")
    assert not runtime.can_execute_create("glyph-8")
    runtime.invalidate_authorizations("create")
    assert not runtime.can_execute_create("glyph-7")


def test_payment_grant_is_round_scoped_and_create_never_upgrades_to_pay():
    runtime = TaskRuntime.begin(TaskSpec.compile("帮我买一个虚构物件"))
    runtime.phase = RuntimePhase.READY_TO_PAY
    runtime.payment_round = 2
    runtime.commit_payment_question()
    runtime.observe_user("可以")

    assert runtime.authorization.payment_disposition == PaymentDisposition.AUTHORIZED
    assert runtime.has_authorization("pay", payment_round=2)
    assert not runtime.has_authorization("pay", payment_round=1)
    runtime.payment_round = 3
    assert not runtime.can_execute_payment()


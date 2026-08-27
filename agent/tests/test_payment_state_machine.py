from agent.adapt_agent import ADAPTAgent
from agent.decision import CandidateLedger, TaskSpec
from agent.runtime import (
    OperationJournal,
    PaymentDisposition,
    PaymentIntent,
    QuestionGate,
    ResponseJournal,
    RuntimePhase,
    TaskRuntime,
    ToolErrorLedger,
    ToolRegistry,
    ToolRole,
    classify_payment_intent,
)
from agent.runtime.tools import ToolMeta
from agent.runtime.outcomes import ToolEffect, ToolOutcome
from vita.data_model.message import AssistantMessage, ToolCall


class _Tool:
    def __init__(self, name: str):
        self.name = name


def _payment_runtime() -> TaskRuntime:
    runtime = TaskRuntime.begin(TaskSpec.compile("请执行一个虚构交易"))
    runtime.phase = RuntimePhase.READY_TO_PAY
    runtime.payment_round = 1
    runtime.commit_payment_question()
    runtime.write_succeeded = True
    return runtime


def _payment_registry() -> tuple[ToolRegistry, _Tool, _Tool]:
    pay = _Tool("settle_workflow")
    state = _Tool("inspect_workflow")
    registry = ToolRegistry()
    registry.tools = [pay, state]
    registry.meta = {
        pay.name: ToolMeta(pay.name, ToolRole.PAY),
        state.name: ToolMeta(state.name, ToolRole.STATE_READ),
    }
    return registry, pay, state


def test_payment_classifier_is_contextual_and_pure():
    assert (
        classify_payment_intent("可以", payment_question_sent=True)
        == PaymentIntent.AUTHORIZE
    )
    assert (
        classify_payment_intent("可以", payment_question_sent=False)
        == PaymentIntent.UNKNOWN
    )
    assert (
        classify_payment_intent("我想买票", payment_question_sent=True)
        == PaymentIntent.UNKNOWN
    )
    assert (
        classify_payment_intent("支付方式是什么？", payment_question_sent=True)
        == PaymentIntent.QUESTION
    )


def test_contextual_ack_authorizes_only_pay_tool():
    runtime = _payment_runtime()
    registry, pay, _ = _payment_registry()
    ledger = CandidateLedger()
    ledger.pending_payment_ids.add("W-1")

    runtime.observe_user("可以")

    assert runtime.phase == RuntimePhase.READY_TO_PAY
    assert runtime.authorization.pay_authorized
    assert runtime.authorization.payment_disposition == PaymentDisposition.AUTHORIZED
    assert [tool.name for tool in registry.allowed_tools(runtime, ledger)] == [pay.name]


def test_stale_authorization_flags_cannot_cross_payment_boundary():
    runtime = TaskRuntime.begin(TaskSpec.compile("请执行一个虚构交易"))
    runtime.phase = RuntimePhase.READY_TO_PAY
    runtime.payment_round = 2
    runtime.authorization.pay_authorized = True
    runtime.authorization.payment_disposition = PaymentDisposition.AUTHORIZED
    registry, _, _ = _payment_registry()

    assert not runtime.can_execute_payment()
    assert not registry.allowed_tools(runtime, CandidateLedger())


def test_authorization_is_invalidated_by_a_new_payment_round():
    runtime = TaskRuntime.begin(TaskSpec.compile("请执行一个虚构交易"))
    pending = ToolOutcome(True, ToolEffect.CREATED_PENDING_PAYMENT)
    runtime.observe_tool_outcome("create_workflow", pending)
    runtime.commit_payment_question()
    runtime.observe_user("确认支付")
    assert runtime.can_execute_payment()

    runtime.observe_tool_outcome("create_workflow", pending)

    assert runtime.payment_round == 2
    assert not runtime.can_execute_payment()


def test_multiple_pay_tools_are_scoped_by_observed_create_provenance():
    runtime = _payment_runtime()
    runtime.observe_user("确认支付")
    alpha = _Tool("pay_alpha_order")
    beta = _Tool("pay_beta_order")
    registry = ToolRegistry()
    registry.tools = [alpha, beta]
    registry.meta = {
        alpha.name: ToolMeta(
            alpha.name, ToolRole.PAY, {"order_id"}, {"order_id": "order"}
        ),
        beta.name: ToolMeta(
            beta.name, ToolRole.PAY, {"order_id"}, {"order_id": "order"}
        ),
    }
    ledger = CandidateLedger()
    ledger.pending_payment_ids.add("W-1")
    ledger.state_ids["order"] = {"W-1"}
    ledger.state_sources["order"] = {"W-1": {"create_alpha_order"}}

    assert [tool.name for tool in registry.allowed_tools(runtime, ledger)] == [
        alpha.name
    ]


def test_payment_deferral_finishes_without_exposing_state_read():
    runtime = _payment_runtime()
    registry, _, _ = _payment_registry()
    ledger = CandidateLedger()
    ledger.pending_payment_ids.add("W-1")

    runtime.observe_user("不用了，我先去忙会儿，晚点再说。")

    assert runtime.phase == RuntimePhase.DONE
    assert runtime.authorization.pay_declined
    assert not runtime.authorization.pay_authorized
    assert runtime.authorization.payment_disposition == PaymentDisposition.DEFERRED
    assert not registry.allowed_tools(runtime, ledger)
    assert "W-1" in ledger.pending_payment_ids


def test_self_payment_and_direct_decline_are_terminal():
    self_pay = _payment_runtime()
    self_pay.observe_user("我自己来付")
    assert self_pay.phase == RuntimePhase.DONE
    assert self_pay.authorization.payment_disposition == PaymentDisposition.SELF_PAY

    declined = _payment_runtime()
    declined.observe_user("别付了")
    assert declined.phase == RuntimePhase.DONE
    assert declined.authorization.payment_disposition == PaymentDisposition.DECLINED


def test_payment_terminal_message_is_truthful_for_deferral():
    agent = object.__new__(ADAPTAgent)
    agent.runtime = _payment_runtime()
    agent.runtime.observe_user("暂时不付，晚点再说")
    agent.operations = OperationJournal()
    agent.responses = ResponseJournal()
    agent.ledger = CandidateLedger()

    assert "保持未支付" in agent._terminal_response().content


def test_transaction_words_before_payment_question_do_not_authorize():
    runtime = TaskRuntime.begin(TaskSpec.compile("帮我看看有什么演出票"))
    runtime.observe_user("我想买票")
    assert not runtime.authorization.pay_authorized
    assert runtime.authorization.payment_disposition == PaymentDisposition.UNRESOLVED


def test_unknown_payment_reply_gets_one_clarification_then_safely_defers():
    runtime = _payment_runtime()
    runtime.observe_user("我再想想")
    assert runtime.phase == RuntimePhase.READY_TO_PAY
    assert runtime.payment_clarification_pending

    runtime.payment_clarification_pending = False
    runtime.payment_clarifications = 1
    runtime.observe_user("还是再看看")
    assert runtime.phase == RuntimePhase.DONE
    assert runtime.authorization.payment_disposition == PaymentDisposition.DEFERRED


def test_preflight_rejects_two_irreversible_calls_in_one_message():
    registry, pay, _ = _payment_registry()
    agent = object.__new__(ADAPTAgent)
    agent.runtime = _payment_runtime()
    agent.runtime.observe_user("确认支付")
    agent.tool_registry = registry
    agent.operations = OperationJournal()
    agent.tool_errors = ToolErrorLedger()
    agent.question_gate = QuestionGate()
    agent.enable_candidate_validation = False

    message = AssistantMessage(
        role="assistant",
        tool_calls=[
            ToolCall(id="pay-1", name=pay.name, arguments={}),
            ToolCall(id="pay-2", name=pay.name, arguments={}),
        ],
    )
    problems = agent._preflight(message, [pay])
    assert any("at most one irreversible" in problem for problem in problems)


def test_preflight_rejects_pay_with_only_stale_boolean_authorization():
    registry, pay, _ = _payment_registry()
    agent = object.__new__(ADAPTAgent)
    agent.runtime = TaskRuntime.begin(TaskSpec.compile("请执行一个虚构交易"))
    agent.runtime.phase = RuntimePhase.READY_TO_PAY
    agent.runtime.payment_round = 1
    agent.runtime.authorization.pay_authorized = True
    agent.runtime.authorization.payment_disposition = PaymentDisposition.AUTHORIZED
    agent.tool_registry = registry
    agent.operations = OperationJournal()
    agent.tool_errors = ToolErrorLedger()
    agent.question_gate = QuestionGate()
    agent.enable_candidate_validation = False

    message = AssistantMessage(
        role="assistant",
        tool_calls=[ToolCall(id="pay-stale", name=pay.name, arguments={})],
    )
    assert "PAY is not authorized by the user" in agent._preflight(message, [pay])

"""Order-intent coverage for spec compilation and runtime authorization (E-034).

Both sides must agree: an instruction such as "帮我定张车票" asks for a
transaction, so the compiled action is ``commit`` and the runtime authorizes a
write. Pure recommendation requests and information requests ("帮我看看有没有
团购券") must never authorize a write.
"""

from __future__ import annotations

import pytest

from agent.decision import TaskSpec
from agent.intent import is_completion_style_request, is_transaction_request
from agent.runtime.question_gate import QuestionGate
from agent.runtime.state import RuntimePhase, TaskRuntime


@pytest.mark.parametrize(
    "instruction",
    [
        "周六要去绵阳找朋友，帮我定张车票",
        "帮我定张车票",
        "定两张明天去成都的动车票",
        "帮我订一间明晚的大床房",
        "26号开会，提前给我点个咖啡提神",
        "下午来杯冰咖啡",
        "明天晚上帮我找个养生休闲的地方，给我团一张",
        "帮我预约周六下午三点的按摩",
        "再来一份上次那个外卖",
        "帮我买一下那个键盘",
        "帮我点个",
    ],
)
def test_order_requests_compile_as_commit(instruction):
    assert TaskSpec.compile(instruction).action == "commit"
    assert is_transaction_request(instruction)


@pytest.mark.parametrize(
    "instruction",
    [
        "周末又想去摘草莓了，你给我推荐一个适合的采摘园呗",
        "推荐一个适合的采摘园",
        "帮我看看有没有团购券",
        "帮我查一下明天的天气",
        "我打算周末去普吉岛玩，推荐个酒店",
        "帮我看看这家店几点关门",
        "帮我查一下订单状态",
        "帮我看下订单还有多久到",
    ],
)
def test_non_order_requests_stay_recommend(instruction):
    assert TaskSpec.compile(instruction).action == "recommend"
    assert not is_transaction_request(instruction)


def test_completion_style_requires_a_quantity_or_item():
    assert is_completion_style_request("给我团一张")
    assert is_completion_style_request("帮我定张车票")
    # A bare verb without a quantity/unit or item noun is information seeking.
    assert not is_completion_style_request("帮我看看有没有团购券")
    assert not is_completion_style_request("我想了解一下")
    # Endorsing a recommendation is a selection, not a transaction request.
    assert not is_completion_style_request("行，那就第一个吧。")


def test_ticket_order_waits_for_a_settled_choice_before_the_write():
    """E-049: an authorization to buy is not knowledge of what to buy.

    The runtime stays in SELECT - where the model may still ask which train or
    which seat - and only exposes CREATE once something observable settles the
    candidate.
    """
    spec = TaskSpec.compile("周六要去绵阳找朋友，帮我定张车票")
    runtime = TaskRuntime.begin(spec)
    assert runtime.authorization.create_authorized
    assert runtime.authorization.candidate_choice_authorized
    assert not runtime.forbid_redundant_candidate_question
    runtime.observe_candidates(6, execution_ready=True)
    assert runtime.phase == RuntimePhase.SELECT
    assert not runtime.choice_settled()[0]
    runtime.observe_user("那就 D1835 二等座吧，你看着办")
    runtime.observe_candidates(6, execution_ready=True)
    assert runtime.choice_settled()[0]
    assert runtime.phase == RuntimePhase.READY_TO_CREATE


def test_information_request_does_not_authorize_write():
    spec = TaskSpec.compile("帮我看看有没有团购券")
    runtime = TaskRuntime.begin(spec)
    assert not runtime.authorization.create_authorized
    runtime.observe_candidates(6, execution_ready=True)
    assert runtime.phase == RuntimePhase.SELECT


def test_recommendation_endorsement_needs_an_explicit_order_phrase():
    spec = TaskSpec.compile("周末又想去摘草莓了，你给我推荐一个适合的采摘园呗")
    runtime = TaskRuntime.begin(spec)
    assert not runtime.authorization.create_authorized
    runtime.observe_candidates(6, execution_ready=True)
    runtime.observe_user("行，那就第一个吧。")
    assert runtime.selection_made
    assert not runtime.authorization.create_authorized
    # Only an explicit transaction phrase authorizes the write.
    runtime.observe_user("那就帮我订一张吧")
    assert runtime.authorization.create_authorized
    runtime.observe_candidates(6, execution_ready=True)
    assert runtime.phase == RuntimePhase.READY_TO_CREATE


def test_commit_spec_authorizes_write_from_the_instruction():
    runtime = TaskRuntime.begin(TaskSpec.compile("给我团一张按摩券"))
    assert runtime.authorization.create_authorized
    assert runtime.authorization.candidate_choice_authorized


def test_execution_confirmation_is_allowed_once_after_endorsement():
    runtime = TaskRuntime.begin(
        TaskSpec.compile("周末又想去摘草莓了，你给我推荐一个适合的采摘园呗")
    )
    runtime.observe_candidates(3, execution_ready=True)
    runtime.observe_user("行，那就第一个吧。")
    runtime.observe_candidates(3, execution_ready=True)
    assert runtime.phase == RuntimePhase.SELECT
    gate = QuestionGate()
    decision = gate.evaluate("需要我帮你预订第一项吗？", runtime)
    assert decision.allowed
    assert decision.dimension == "execution_confirmation"
    gate.commit(decision, runtime)
    assert runtime.execution_confirmation_pending
    assert not gate.evaluate("需要我帮你预订第一项吗？", runtime).allowed


def test_confirmation_answer_authorizes_execution_and_promotes():
    runtime = TaskRuntime.begin(
        TaskSpec.compile("周末又想去摘草莓了，你给我推荐一个适合的采摘园呗")
    )
    runtime.observe_candidates(4, execution_ready=True)
    runtime.observe_user("行，那就第一个吧。")
    runtime.observe_candidates(4, execution_ready=True)
    gate = QuestionGate()
    gate.commit(gate.evaluate("需要我帮你预订第一项吗？", runtime), runtime)
    # A non-declining answer to the agent's own question is the authorization.
    runtime.observe_user("周六下午吧，你看着办。")
    assert runtime.authorization.create_authorized
    assert not runtime.execution_confirmation_pending
    runtime.observe_candidates(4, execution_ready=True)
    assert runtime.phase == RuntimePhase.READY_TO_CREATE


def test_declining_the_confirmation_does_not_authorize_execution():
    runtime = TaskRuntime.begin(
        TaskSpec.compile("周末又想去摘草莓了，你给我推荐一个适合的采摘园呗")
    )
    runtime.observe_candidates(4, execution_ready=True)
    runtime.observe_user("行，那就第一个吧。")
    runtime.observe_candidates(4, execution_ready=True)
    gate = QuestionGate()
    gate.commit(gate.evaluate("需要我帮你预订第一项吗？", runtime), runtime)
    runtime.observe_user("不用了，我先看看。")
    assert not runtime.authorization.create_authorized
    runtime.observe_candidates(4, execution_ready=True)
    assert runtime.phase == RuntimePhase.SELECT


def test_finalized_recommendation_reopens_for_a_follow_up_order():
    runtime = TaskRuntime.begin(
        TaskSpec.compile("周末又想去摘草莓了，你给我推荐一个适合的采摘园呗")
    )
    runtime.observe_candidates(3, execution_ready=True)
    # The framework finalized its recommendation and parked the runtime.
    runtime.phase = RuntimePhase.DONE
    runtime.observe_user("那就帮我订一张吧")
    assert runtime.authorization.create_authorized
    # The follow-up order reopens observation instead of firing a write the
    # user never specified (E-049); the choice is still open here.
    assert runtime.phase == RuntimePhase.SEARCH
    assert not runtime.choice_settled()[0]
    runtime.observe_user("就要第一个吧")
    assert runtime.choice_settled()[0]
    runtime.observe_candidates(3, execution_ready=True)
    assert runtime.phase == RuntimePhase.READY_TO_CREATE


@pytest.mark.parametrize(
    "instruction,facet",
    [
        ("周六要去绵阳找朋友，帮我定张车票", "train"),
        ("帮我订两张回家的火车票", "train"),
        ("订一张去北京的高铁票", "train"),
        ("帮我买张去上海的机票", "flight"),
        ("帮我订个普吉岛的酒店", "hotel"),
        ("下周末去成都玩，帮我订个民宿", "hotel"),
    ],
)
def test_ticket_vocabulary_compiles_to_the_right_domain(instruction, facet):
    """A ticket request must not fall through to the delivery default.

    The framework only expands parent candidates for the facet it inferred, so
    a misclassified ticket request never sees the seats or rooms it needs.
    """
    spec = TaskSpec.compile(instruction)
    assert spec.domain == "ota"
    assert spec.facet == facet
    assert spec.action == "commit"

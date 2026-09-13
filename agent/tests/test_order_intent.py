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

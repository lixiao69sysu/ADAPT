import json

import pytest

from vita.data_model.message import (
    AssistantMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from vita.environment.tool import as_tool
from vita.memory.null_memory import NullMemory

import agent.adapt_agent as adapt_agent_module
from agent.adapt_agent import ADAPTAgent


def pay_order(order_id: str) -> str:
    """Pay an existing order."""
    return "done"


def make_agent():
    return ADAPTAgent(
        tools=[as_tool(pay_order)],
        domain_policy="time={time}",
        memory=NullMemory(language="chinese"),
        llm="fake-model",
        llm_args={},
        time="2026-08-06 10:00:00",
        language="chinese",
    )


def test_invalid_tool_draft_is_regenerated_before_return(monkeypatch):
    drafts = iter(
        [
            AssistantMessage(
                role="assistant",
                tool_calls=[
                    ToolCall(
                        id="bad",
                        name="pay_order",
                        arguments={"order_id": "ORD-FAKE"},
                    )
                ],
                usage={"completion_tokens": 10, "total_tokens": 30},
                cost=0.1,
            ),
            AssistantMessage(
                role="assistant",
                content="请提供需要支付的订单号。",
                usage={"completion_tokens": 8, "total_tokens": 20},
                cost=0.05,
            ),
        ]
    )
    monkeypatch.setattr(adapt_agent_module, "generate", lambda **_: next(drafts))
    agent = make_agent()
    state = agent.get_init_state()

    message, state = agent.generate_next_message(
        UserMessage(role="user", content="帮我支付订单"),
        state,
    )

    assert message.content == "请提供需要支付的订单号。"
    assert all(not getattr(item, "tool_calls", None) for item in state.messages)
    assert state.rejected_tool_drafts == 1
    assert message.cost == pytest.approx(0.15)
    assert message.usage["completion_tokens"] == 18
    assert message.usage["total_tokens"] == 50


def test_public_tool_result_allows_follow_up_id(monkeypatch):
    draft = AssistantMessage(
        role="assistant",
        tool_calls=[
            ToolCall(
                id="pay",
                name="pay_order",
                arguments={"order_id": "ORD-12345"},
            )
        ],
    )
    monkeypatch.setattr(adapt_agent_module, "generate", lambda **_: draft)
    agent = make_agent()
    state = agent.get_init_state()

    inbound = ToolMessage(
        id="create",
        name="create_order",
        role="tool",
        content=json.dumps({"order_id": "ORD-12345"}),
        requestor="assistant",
        error=False,
    )
    message, state = agent.generate_next_message(inbound, state)

    assert message.tool_calls[0].arguments["order_id"] == "ORD-12345"
    assert state.rejected_tool_drafts == 0


def test_update_tools_refreshes_guard(monkeypatch):
    def search(keyword: str) -> list:
        """Search for products."""
        return []

    agent = make_agent()
    agent.update_tools([as_tool(search)])
    state = agent.get_init_state()
    draft = AssistantMessage(
        role="assistant",
        tool_calls=[
            ToolCall(
                id="search",
                name="search",
                arguments={"keyword": "低糖饮料"},
            )
        ],
    )
    monkeypatch.setattr(adapt_agent_module, "generate", lambda **_: draft)

    message, _ = agent.generate_next_message(
        UserMessage(role="user", content="找低糖饮料"),
        state,
    )

    assert message.tool_calls[0].name == "search"


def test_agent_states_do_not_share_entity_ids():
    agent = make_agent()
    first = agent.get_init_state()
    second = agent.get_init_state()

    first.known_entity_ids.add("ORD-ONLY-FIRST")

    assert "ORD-ONLY-FIRST" not in second.known_entity_ids


def test_long_tool_results_are_paged_only_in_model_view(monkeypatch):
    captured = {}

    def fake_generate(**kwargs):
        captured["messages"] = kwargs["messages"]
        return AssistantMessage(role="assistant", content="已找到合适商品。")

    monkeypatch.setattr(adapt_agent_module, "generate", fake_generate)
    agent = make_agent()
    agent.max_tool_result_chars = 120
    agent.max_prompt_message_chars = 300
    state = agent.get_init_state()
    full_result = "product_id=ITEM-12345 " + ("商品详情" * 200)
    inbound = ToolMessage(
        id="search",
        name="search_products",
        role="tool",
        content=full_result,
        requestor="assistant",
        error=False,
    )

    _, state = agent.generate_next_message(inbound, state)

    prompt_tool = next(
        message for message in captured["messages"] if message.role == "tool"
    )
    assert len(prompt_tool.content) < len(full_result)
    assert "ITEM-12345" in prompt_tool.content
    assert "ADAPT context page" in prompt_tool.content
    assert state.messages[0].content == full_result
    assert "ITEM-12345" in state.known_entity_ids


def test_context_paging_shrinks_oldest_tool_results_first():
    agent = make_agent()
    agent.max_tool_result_chars = 200
    agent.min_tool_result_chars = 120
    agent.max_prompt_message_chars = 360
    messages = [
        ToolMessage(
            id="old",
            name="search_products",
            role="tool",
            content="OLD-ID " + ("旧结果" * 100),
            requestor="assistant",
            error=False,
        ),
        UserMessage(role="user", content="继续找新的商品"),
        ToolMessage(
            id="new",
            name="search_products",
            role="tool",
            content="NEW-ID " + ("新结果" * 100),
            requestor="assistant",
            error=False,
        ),
    ]

    paged = agent._page_messages(messages)

    assert len(paged[0].content) < len(paged[2].content)
    assert "OLD-ID" in paged[0].content
    assert "NEW-ID" in paged[2].content
    assert messages[0].content.startswith("OLD-ID")
    assert len(messages[0].content) > len(paged[0].content)


def test_third_identical_tool_call_is_internally_corrected(monkeypatch):
    repeated = AssistantMessage(
        role="assistant",
        tool_calls=[ToolCall(id="search-3", name="pay_order", arguments={"order_id": "ORD-1"})],
    )
    corrected = AssistantMessage(role="assistant", content="不再重复调用。")
    drafts = iter([repeated, corrected])
    monkeypatch.setattr(adapt_agent_module, "generate", lambda **_: next(drafts))
    agent = make_agent()
    state = agent.get_init_state()
    state.known_entity_ids.add("ORD-1")
    state.tool_call_counts['pay_order:{"order_id":"ORD-1"}'] = 2

    message, state = agent.generate_next_message(
        UserMessage(role="user", content="继续"), state
    )

    assert message.content == "不再重复调用。"
    assert state.rejected_tool_drafts == 1
    assert "pay_order" in state.disabled_tool_names


def test_repeated_search_disables_search_family_during_regeneration(monkeypatch):
    def search_products(keyword: str) -> list:
        """Search products."""
        return []

    def search_stores(keyword: str) -> list:
        """Search stores."""
        return []

    def create_order(product_id: str) -> str:
        """Create an order."""
        return "done"

    observed_tools = []

    def fake_generate(**kwargs):
        observed_tools.append([tool.name for tool in kwargs["tools"]])
        if len(observed_tools) == 1:
            return AssistantMessage(
                role="assistant",
                tool_calls=[
                    ToolCall(
                        id="repeat",
                        name="search_products",
                        arguments={"keyword": "苹果"},
                    )
                ],
            )
        return AssistantMessage(
            role="assistant",
            tool_calls=[
                ToolCall(
                    id="create",
                    name="create_order",
                    arguments={"product_id": "PRODUCT-1"},
                )
            ],
        )

    monkeypatch.setattr(adapt_agent_module, "generate", fake_generate)
    agent = ADAPTAgent(
        tools=[as_tool(search_products), as_tool(search_stores), as_tool(create_order)],
        domain_policy="time={time}",
        memory=NullMemory(language="chinese"),
        llm="fake-model",
        llm_args={},
        time="2026-08-06 10:00:00",
        language="chinese",
    )
    state = agent.get_init_state()
    state.known_entity_ids.add("PRODUCT-1")
    state.tool_call_counts['search_products:{"keyword":"苹果"}'] = 2

    message, state = agent.generate_next_message(
        UserMessage(role="user", content="继续下单"), state
    )

    assert message.tool_calls[0].name == "create_order"
    assert "search_products" in observed_tools[0]
    assert "search_products" not in observed_tools[1]
    assert "search_stores" not in observed_tools[1]
    assert "create_order" in observed_tools[1]


def test_internal_correction_keeps_a_single_leading_system_message(monkeypatch):
    calls = []

    def fake_generate(**kwargs):
        calls.append(kwargs["messages"])
        if len(calls) == 1:
            return AssistantMessage(
                role="assistant",
                tool_calls=[
                    ToolCall(
                        id="bad",
                        name="pay_order",
                        arguments={"order_id": "ORD-MISSING"},
                    )
                ],
            )
        return AssistantMessage(role="assistant", content="请提供有效订单。")

    monkeypatch.setattr(adapt_agent_module, "generate", fake_generate)
    agent = make_agent()
    state = agent.get_init_state()

    agent.generate_next_message(UserMessage(role="user", content="支付"), state)

    second_messages = calls[1]
    assert sum(message.role == "system" for message in second_messages) == 1
    assert second_messages[0].role == "system"
    assert "ADAPT 内部纠错" in second_messages[0].content


def test_successful_payment_returns_terminal_confirmation_without_second_order(monkeypatch):
    def should_not_generate(**kwargs):
        raise AssertionError("payment completion should not call the model")

    monkeypatch.setattr(adapt_agent_module, "generate", should_not_generate)
    agent = make_agent()
    state = agent.get_init_state()
    state.last_order_result = (
        "Order(order_id:ORDER-1, hotel_name:青岛亚朵酒店, "
        "check_in:2024-08-06, check_out:2024-08-09)"
    )
    state.pending_tool_names["pay-call"] = "pay_order"
    inbound = ToolMessage(
        id="pay-call",
        name="pay_order",
        role="tool",
        content="Payment successful",
        requestor="assistant",
        error=False,
    )

    message, state = agent.generate_next_message(inbound, state)

    assert state.goal_completed is True
    assert "支付成功" in message.content
    assert "青岛亚朵酒店" in message.content
    assert "2024-08-09" in message.content
    assert "###STOP###" in message.content


def test_context_paging_collapses_duplicate_search_results():
    agent = make_agent()
    content = "product_id=ITEM-1\nproduct_id=ITEM-2"
    messages = [
        ToolMessage(id="a", name="search_products", role="tool", content=content, requestor="assistant", error=False),
        ToolMessage(id="b", name="search_products", role="tool", content=content, requestor="assistant", error=False),
    ]

    paged = agent._page_messages(messages)

    assert paged[0].content == content
    assert "duplicate result" in paged[1].content
    assert "Do not repeat" in paged[1].content


def test_context_page_ends_on_complete_record_and_pushes_action():
    agent = make_agent()
    agent.max_tool_result_chars = 145
    content = "\n".join(f"product_id=ITEM-{index} name=商品{index}" for index in range(20))
    message = ToolMessage(id="a", name="search_products", role="tool", content=content, requestor="assistant", error=False)

    paged = agent._page_messages([message])[0].content

    assert "must choose from the shown candidates" in paged
    assert "use a narrower lookup" not in paged
    assert paged.split("\n...")[0].splitlines()[-1].startswith("product_id=")

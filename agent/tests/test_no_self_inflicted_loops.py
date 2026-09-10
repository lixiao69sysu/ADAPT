"""Guards against self-inflicted loops and capability removal (E-042).

Trace comparison against the stock agent on the same users showed three
regressions introduced by ADAPT's own control layer: the runtime re-promoted a
finished subtask back into READY_TO_CREATE (14 duplicate orders in one unit),
the framework re-asked the payment question every turn, and the registry hid
the memory-query tool the stock agent uses to ground its choice.
"""

from __future__ import annotations

from agent.decision import Candidate, CandidateLedger, DecisionCard, TaskSpec
from agent.memory.adapt_memory import ADAPTMemory
from agent.runtime.question_gate import QuestionDecision, QuestionGate
from agent.runtime.state import RuntimePhase, TaskRuntime
from agent.runtime.tool_errors import ToolErrorLedger
from agent.runtime.tools import ToolMeta, ToolRegistry, ToolRole
from vita.data_model.message import AssistantMessage, ToolCall


class _Debug:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emit(self, event: str, **payload) -> None:
        self.events.append({"event": event, **payload})


def _runtime_with_order() -> TaskRuntime:
    runtime = TaskRuntime.begin(TaskSpec.compile("帮我买个垃圾桶送到家"))
    runtime.observe_candidates(5, execution_ready=True)
    runtime.observe_tool_result(
        "create_delivery_order",
        "Order(order_id:O123, order_type:delivery, status:unpaid)",
    )
    return runtime


def test_a_succeeded_write_is_never_re_promoted():
    runtime = _runtime_with_order()
    assert runtime.write_succeeded
    runtime.phase = RuntimePhase.SEARCH  # a later user turn lands here
    runtime.observe_user("好的")
    assert runtime.phase != RuntimePhase.READY_TO_CREATE
    runtime.observe_candidates(5, execution_ready=True)
    assert runtime.phase != RuntimePhase.READY_TO_CREATE


def test_payment_question_is_asked_once_per_subtask():
    runtime = _runtime_with_order()
    assert runtime.phase == RuntimePhase.READY_TO_PAY
    # observe_tool_result no longer re-arms the question.
    assert runtime.payment_question_sent is False
    runtime.payment_question_sent = True
    runtime.observe_tool_result(
        "create_delivery_order", "Order(order_id:O124, status:unpaid)"
    )
    assert runtime.payment_question_sent is True


def build_agent(runtime: TaskRuntime | None = None):
    from agent.adapt_agent import ADAPTAgent

    agent = ADAPTAgent.__new__(ADAPTAgent)
    agent.debug = _Debug()
    agent.ledger = CandidateLedger()
    agent.tool_errors = ToolErrorLedger()
    agent.tool_registry = ToolRegistry()
    agent.tool_registry.meta = {
        "create_delivery_order": ToolMeta(
            "create_delivery_order",
            ToolRole.CREATE,
            {"product_ids"},
            {},
        )
    }
    agent.runtime = runtime or TaskRuntime.begin(
        TaskSpec.compile("帮我买个垃圾桶送到家")
    )
    agent.decision_card = DecisionCard()
    agent.task_spec = TaskSpec.compile("帮我买个垃圾桶送到家")
    agent._succeeded_writes = set()
    agent._replan_limit = 2
    agent._select_turns = 0
    agent.home_tokens = []
    agent.enable_candidate_validation = False
    agent.enable_lessons = False
    agent.question_gate = QuestionGate()
    agent._pending_question_decision = QuestionDecision(
        True, counts_against_budget=False
    )
    agent._record_lesson = lambda *args, **kwargs: None
    agent._gap_search_tool = ""
    agent._recommendation_delivered = False
    return agent


def test_recreating_the_same_order_is_rejected():
    agent = build_agent()
    call = ToolCall(
        id="c1",
        name="create_delivery_order",
        arguments={"product_ids": ["P1"], "user_id": "U1"},
    )
    agent._succeeded_writes.add(
        agent._write_signature(call.name, call.arguments)
    )
    problems = agent._preflight(
        AssistantMessage(role="assistant", tool_calls=[call]),
        [type("T", (), {"name": "create_delivery_order"})()],
    )
    assert any("already created" in problem for problem in problems)
    assert any(event["event"] == "duplicate_write_blocked" for event in agent.debug.events)


def test_a_different_order_is_still_allowed():
    agent = build_agent()
    first = ToolCall(
        id="c1",
        name="create_delivery_order",
        arguments={"product_ids": ["P1"], "user_id": "U1"},
    )
    agent._succeeded_writes.add(agent._write_signature(first.name, first.arguments))
    second = ToolCall(
        id="c2",
        name="create_delivery_order",
        arguments={"product_ids": ["P2"], "user_id": "U1"},
    )
    problems = agent._preflight(
        AssistantMessage(role="assistant", tool_calls=[second]),
        [type("T", (), {"name": "create_delivery_order"})()],
    )
    assert not any("already created" in problem for problem in problems)


def test_memory_read_tools_are_exposed_but_writes_stay_internal():
    registry = ToolRegistry()

    def tool(name):
        return type("T", (), {"name": name})()

    registry.rebuild(
        [
            tool("query_preference_memory"),
            tool("read_preference_memory"),
            tool("suggest_question_tool"),
            tool("record_preference_answer"),
            tool("delivery_product_search_recommand"),
        ]
    )
    assert registry.role("query_preference_memory") == ToolRole.READ
    assert registry.role("read_preference_memory") == ToolRole.READ
    assert registry.role("suggest_question_tool") == ToolRole.MEMORY
    assert registry.role("record_preference_answer") == ToolRole.MEMORY
    runtime = TaskRuntime.begin(TaskSpec.compile("帮我买个垃圾桶送到家"))
    exposed = {t.name for t in registry.allowed_tools(runtime, CandidateLedger())}
    assert "query_preference_memory" in exposed
    assert "suggest_question_tool" not in exposed


def test_memory_query_tool_returns_the_bounded_read():
    memory = ADAPTMemory()
    assert isinstance(memory.query_preference_memory("奶茶 偏好"), str)
    assert isinstance(memory.read_preference_memory(), str)


def test_recommendation_fallback_waits_for_the_model():
    agent = build_agent()
    agent.task_spec = TaskSpec.compile("推荐一个适合的采摘园")
    agent.runtime = TaskRuntime.begin(agent.task_spec)
    agent.runtime.phase = RuntimePhase.SELECT
    agent.ledger.candidates["S1"] = Candidate(
        "S1", "shop", "某采摘园", "Shop(shop_name=某采摘园)", "search"
    )
    agent._recommendation_delivered = False
    agent._select_turns = 1
    assert agent._framework_recommendation() is None
    agent._select_turns = 2
    message = agent._framework_recommendation()
    assert message is not None
    assert "推荐" in message.content

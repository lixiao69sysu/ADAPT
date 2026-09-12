"""E-050: a decision-critical slot must never be a dead end.

Reading the R8 unit traces showed two subtasks that ended with the terminal
refusal text, zero tool calls and no question ever sent:

- instore/wellness "又得去理发店了，帮我买个套餐。" -> TaskSpec leaves the slot
  ``time`` open, so the runtime starts in NEED_INFO;
- ota "想订张机票去旅游，你有什么好地方推荐吗？" (a unit the stock agent solves
  in 4/4 trials) -> open slots ``departure``/``date``/``quantity``.

NEED_INFO exposed no SEARCH tool and the question gate rejected every question
with "a question is already waiting for the user's answer" -- although no
question had been sent, because the phase came from an unresolved *slot* rather
than from a committed question. The model had no legal action, so three replans
ended in "现有候选无法满足硬约束" and the user gave up.
"""

from __future__ import annotations

from agent.decision import CandidateLedger, TaskSpec
from agent.runtime import QuestionGate, RuntimePhase, TaskRuntime
from agent.runtime.tools import ToolMeta, ToolRegistry, ToolRole

GAP_INSTRUCTIONS = (
    ("又得去理发店了，帮我买个套餐。", "time"),
    ("想订张机票去旅游，你有什么好地方推荐吗？", "departure"),
)


def test_open_slot_starts_in_need_info_without_a_pending_question():
    for instruction, dimension in GAP_INSTRUCTIONS:
        runtime = TaskRuntime.begin(TaskSpec.compile(instruction))
        assert runtime.phase == RuntimePhase.NEED_INFO, instruction
        assert runtime.pending_question_dimension == ""
        assert dimension in runtime.critical_gaps(), instruction


def test_the_model_may_ask_the_question_that_fills_an_open_slot():
    for instruction, _dimension in GAP_INSTRUCTIONS:
        runtime = TaskRuntime.begin(TaskSpec.compile(instruction))
        decision = QuestionGate().evaluate("请问是从哪里出发？", runtime)
        assert decision.allowed, instruction
        assert decision.counts_against_budget


def test_a_committed_question_still_blocks_a_second_one():
    runtime = TaskRuntime.begin(TaskSpec.compile("又得去理发店了，帮我买个套餐。"))
    gate = QuestionGate()
    decision = gate.evaluate("请问你希望什么时候去？", runtime)
    assert decision.allowed
    gate.commit(decision, runtime)
    assert runtime.pending_question_dimension
    blocked = gate.evaluate("那要什么价位的？", runtime)
    assert not blocked.allowed
    assert "already waiting" in blocked.reason


def test_need_info_still_exposes_search_tools():
    class Tool:
        def __init__(self, name, params):
            self.name = name
            self.params = params

    class SearchParams:
        @classmethod
        def model_json_schema(cls):
            return {"properties": {}}

    registry = ToolRegistry()
    registry.rebuild(
        [
            Tool("instore_service_search", SearchParams),
            Tool("get_instore_shop_info", SearchParams),
            Tool("create_instore_product_order", SearchParams),
        ]
    )
    runtime = TaskRuntime.begin(TaskSpec.compile("又得去理发店了，帮我买个套餐。"))
    names = [tool.name for tool in registry.allowed_tools(runtime, CandidateLedger())]
    assert "instore_service_search" in names
    assert "get_instore_shop_info" in names
    # An irreversible write is still hidden while the slot is open.
    assert "create_instore_product_order" not in names
    assert registry.role("instore_service_search") == ToolRole.SEARCH


def test_answering_the_slot_question_moves_the_runtime_on():
    runtime = TaskRuntime.begin(TaskSpec.compile("又得去理发店了，帮我买个套餐。"))
    gate = QuestionGate()
    decision = gate.evaluate("请问你希望什么时候去？", runtime)
    gate.commit(decision, runtime)
    runtime.observe_user("明天下午吧")
    assert runtime.phase != RuntimePhase.NEED_INFO or not runtime.critical_gaps()
    assert runtime.pending_question_dimension == ""

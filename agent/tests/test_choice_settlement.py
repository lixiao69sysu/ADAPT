"""E-049: an authorization to buy is not knowledge of what to buy.

The stock agent wins exactly the units where it asks which flavour, which
address or what time it is and only then writes. ADAPT used to treat the
purchase instruction as if it also chose the product, so the first observed
candidate immediately exposed CREATE and the model had no legal way to ask.
These tests pin the replacement contract: the write is exposed only once
something observable settles the concrete candidate, and the settlement rules
are all checkable from the visible trajectory.
"""

from __future__ import annotations

import pytest

from agent.adapt_agent import ADAPTAgent
from agent.decision import CandidateLedger, DecisionCard, TaskSpec
from agent.runtime import QuestionGate, RuntimePhase, TaskRuntime
from agent.runtime.tool_errors import ToolErrorLedger
from agent.runtime.tools import ToolMeta, ToolRegistry, ToolRole
from vita.data_model.message import AssistantMessage


class _Debug:
    def emit(self, *args, **kwargs):
        pass


def _runtime(instruction: str = "帮我点杯喝的送到公司") -> TaskRuntime:
    return TaskRuntime.begin(TaskSpec.compile(instruction))


def _observed_ledger() -> CandidateLedger:
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "\n".join(
            [
                "StoreProduct(store_id=S1_S00001, product_id=S1_P00001, "
                "product_name=榛仁拿铁, quantity=5, price=22)",
                "StoreProduct(store_id=S1_S00002, product_id=S1_P00002, "
                "product_name=生椰拿铁, quantity=5, price=20)",
            ]
        ),
    )
    return ledger


def test_authorized_commit_does_not_expose_create_before_the_choice_is_settled():
    runtime = _runtime()
    assert runtime.authorization.create_authorized
    assert runtime.authorization.candidate_choice_authorized
    runtime.observe_candidates(2, execution_ready=True)
    assert runtime.phase == RuntimePhase.SELECT
    assert runtime.choice_settled() == (False, "")

    registry = ToolRegistry()
    registry.meta = {
        "delivery_product_search_recommand": ToolMeta(
            "delivery_product_search_recommand", ToolRole.SEARCH, set(), {}
        ),
        "create_delivery_order": ToolMeta(
            "create_delivery_order", ToolRole.CREATE, set(), {}
        ),
    }

    class _Tool:
        def __init__(self, name):
            self.name = name

    registry.tools = [
        _Tool("delivery_product_search_recommand"),
        _Tool("create_delivery_order"),
    ]
    names = [tool.name for tool in registry.allowed_tools(runtime, _observed_ledger())]
    assert "create_delivery_order" not in names


def test_answer_to_a_candidate_question_settles_the_choice():
    runtime = _runtime()
    runtime.observe_candidates(2, execution_ready=True)
    gate = QuestionGate()
    decision = gate.evaluate("您想要哪种口味的？", runtime)
    assert decision.allowed
    gate.commit(decision, runtime)
    assert runtime.phase == RuntimePhase.NEED_INFO

    runtime.observe_user("要榛仁的那款")
    assert runtime.choice_settled() == (True, "candidate question answered")
    assert runtime.phase == RuntimePhase.READY_TO_CREATE


def test_discriminating_evidence_settles_the_choice():
    runtime = _runtime()
    runtime.observe_choice_evidence(leader_id="S1_P00001", executable_count=2)
    runtime.observe_candidates(2, execution_ready=True)
    assert runtime.choice_settled() == (True, "unique preference-evidence leader")
    assert runtime.phase == RuntimePhase.READY_TO_CREATE


def test_a_single_compliant_candidate_leaves_nothing_to_ask():
    runtime = _runtime()
    runtime.observe_choice_evidence(leader_id="", executable_count=1)
    runtime.observe_candidates(1, execution_ready=True)
    assert runtime.choice_settled() == (True, "single compliant candidate")
    assert runtime.phase == RuntimePhase.READY_TO_CREATE


def test_a_spent_question_budget_settles_the_choice():
    runtime = _runtime()
    runtime.observe_candidates(2, execution_ready=True)
    runtime.commit_question("address")
    runtime.observe_user("送到公司")
    runtime.commit_question("time")
    runtime.observe_user("下午三点")
    runtime.observe_candidates(2, execution_ready=True)
    assert runtime.dimension_budget_spent
    assert runtime.choice_settled() == (True, "question budget spent")
    assert runtime.phase == RuntimePhase.READY_TO_CREATE


def test_bounded_select_budget_prevents_a_dead_end():
    runtime = _runtime()
    runtime.observe_candidates(30, execution_ready=True)
    assert runtime.phase == RuntimePhase.SELECT
    runtime.note_select_turn()
    assert runtime.phase == RuntimePhase.SELECT
    runtime.note_select_turn()
    assert runtime.choice_settled() == (True, "bounded select budget")
    assert runtime.phase == RuntimePhase.READY_TO_CREATE
    # A new user turn is a new decision opportunity.
    runtime.observe_user("等等，换成热的")
    assert runtime.select_turns == 0


def test_delegation_is_a_settlement_and_revocation_is_not():
    runtime = _runtime(instruction="帮我找个养生休闲的地方")
    assert not runtime.authorization.create_authorized
    runtime.observe_candidates(3, execution_ready=True)
    runtime.observe_user("随便，你看着办吧")
    assert runtime.choice_settled() == (True, "user delegated the choice")
    # Delegation alone does not authorize a write: only an order phrase does.
    assert runtime.phase in {RuntimePhase.SEARCH, RuntimePhase.SELECT}
    runtime.observe_user("那就帮我团一张")
    runtime.observe_candidates(3, execution_ready=True)
    assert runtime.phase == RuntimePhase.READY_TO_CREATE


def test_the_gate_blocks_only_settled_or_premature_choice_questions():
    gate = QuestionGate()
    runtime = _runtime()
    runtime.observe_candidates(2, execution_ready=True)
    # Open choice: asking is the point.
    assert gate.evaluate("您想选哪一款？", runtime).allowed

    runtime.observe_user("随便，你看着办吧")
    blocked = gate.evaluate("您想选哪一款？", runtime)
    assert not blocked.allowed
    assert "already settled" in blocked.reason

    premature = _runtime("帮我订明晚重庆的酒店")
    premature.observe_candidates(1, execution_ready=False)
    early = gate.evaluate("订第一家商务大床房可以吗？", premature)
    assert not early.allowed
    assert "expand required details" in early.reason


def test_a_recommendation_task_keeps_asking_freely():
    """Without a write authorization no settlement is needed, so questions stay open."""
    gate = QuestionGate()
    runtime = _runtime("帮我找个适合的采摘园")
    assert not runtime.authorization.create_authorized
    runtime.observe_candidates(3, execution_ready=True)
    runtime.observe_user("行，那就第一个吧。")
    assert runtime.selection_made
    assert gate.evaluate("需要我帮你预订第一项吗？", runtime).allowed


def test_promote_if_settled_is_the_recovery_from_a_blocked_question():
    """A blocked question in SELECT must not end in the refusal fallback."""
    gate = QuestionGate()
    runtime = _runtime()
    runtime.observe_candidates(2, execution_ready=True)
    assert not runtime.promote_if_settled()
    runtime.observe_user("随便，你看着办吧")
    assert gate.evaluate("您想选哪一款？", runtime).allowed is False
    assert runtime.promote_if_settled()
    assert runtime.phase == RuntimePhase.READY_TO_CREATE


def test_explicit_selection_is_the_only_hard_divergence_veto():
    ledger = _observed_ledger()
    card = DecisionCard(prefer=["生椰"])
    arguments = {"store_id": "S1_S00002", "product_ids": ["S1_P00002"]}
    assert ledger.validate_ranked_choice(arguments, card) == []
    problems = ledger.validate_ranked_choice(arguments, card, "S1_P00001")
    assert problems and "explicitly selected" in problems[0]


def test_a_ranked_out_candidate_is_observed_not_rejected():
    """Position in the rendered crop is an advisory, never a veto (E-049)."""
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "\n".join(
            [
                f"StoreProduct(store_id=S1_S0000{index}, product_id=S1_P0000{index}, "
                f"product_name=鼠标{index}, quantity=5, price={index + 10})"
                for index in range(9)
            ]
        ),
    )
    card = DecisionCard(must=["鼠标"])
    last = ledger.candidates["S1_P00008"]
    assert ledger.shortlist_position(last.candidate_id, card) == 0
    assert ledger.validate_ranked_choice(
        {"store_id": "S1_S00008", "product_ids": [last.candidate_id]}, card
    ) == []


def _agent_with_executable_candidate(instruction: str) -> ADAPTAgent:
    ledger = _observed_ledger()
    agent = object.__new__(ADAPTAgent)
    agent.task_spec = TaskSpec.compile(instruction)
    agent.runtime = TaskRuntime.begin(agent.task_spec)
    agent.runtime.observe_candidates(2, execution_ready=True)
    agent.tool_registry = ToolRegistry()
    agent.tool_registry.meta = {
        "create_delivery_order": ToolMeta(
            "create_delivery_order", ToolRole.CREATE, set(), {}
        )
    }
    agent.tool_errors = ToolErrorLedger()
    agent.ledger = ledger
    agent.decision_card = DecisionCard()
    agent.debug = _Debug()
    agent.enable_lessons = True
    agent.observed: list[str] = []
    agent._record_lesson = lambda failure, trigger, correction: agent.observed.append(
        failure
    )
    return agent


@pytest.mark.parametrize(
    "settled,expected",
    [(False, []), (True, ["missed_write"])],
)
def test_missed_write_is_learned_only_from_a_settled_choice(settled, expected):
    """Learning "force create after any candidate" is what armed E-049's bug."""
    agent = _agent_with_executable_candidate("帮我点杯喝的送到公司")
    if settled:
        agent.runtime.observe_user("随便，你看着办")
    else:
        assert not agent.runtime.choice_settled()[0]
    agent._finalize_visible_trajectory()
    assert agent.observed == expected


def test_preflight_does_not_veto_a_lower_coverage_write():
    agent = _agent_with_executable_candidate("帮我点杯喝的送到公司")
    agent.user_profile = {}
    agent.question_gate = QuestionGate()
    agent.enable_candidate_validation = True
    agent.ledger.require_max_preference_coverage = True
    agent.decision_card = DecisionCard(prefer=["榛仁拿铁"])
    from vita.data_model.message import ToolCall

    class _Tool:
        name = "create_delivery_order"

    call = ToolCall(
        id="lower",
        name="create_delivery_order",
        arguments={"store_id": "S1_S00002", "product_ids": ["S1_P00002"]},
    )
    assert not agent._preflight(
        AssistantMessage(role="assistant", tool_calls=[call]), [_Tool()]
    )
    assert agent.observed == ["preference_undercoverage"]

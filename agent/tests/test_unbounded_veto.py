"""E-052: a derived constraint must never veto without bound.

R10's first user livelocked on one subtask: "后天要去遵义喝朋友的喜酒...先定30和31号的
就行" asks for two nights. The single-day pattern matched only "31号", so the card
demanded the 31st as a hard requirement and rejected every attempt to book the
30th -- 103 identical rejections, 78 steps, 35 user turns, and the same false
refusal text repeated to the user until the step budget was gone.
"""

from __future__ import annotations

from agent.adapt_agent import ADAPTAgent
from agent.decision import CandidateLedger, DecisionCard, TaskSpec, build_decision_card
from agent.runtime import RuntimePhase, TaskRuntime
from agent.runtime.tool_errors import ToolErrorLedger

TWO_NIGHTS = (
    "后天要去遵义喝朋友的喜酒，顺便在那玩几天，"
    "你给我在会议会址3km范围内订个酒店吗，先定30和31号的就行"
)


class _Debug:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emit(self, event: str, **payload) -> None:
        self.events.append({"event": event, **payload})


def _agent(instruction: str = TWO_NIGHTS) -> ADAPTAgent:
    agent = object.__new__(ADAPTAgent)
    agent.task_spec = TaskSpec.compile(instruction)
    agent.runtime = TaskRuntime.begin(agent.task_spec)
    agent.decision_card = DecisionCard()
    agent.ledger = CandidateLedger()
    agent.tool_errors = ToolErrorLedger()
    agent.debug = _Debug()
    agent.enable_lessons = False
    agent.lessons: list[str] = []
    agent._record_lesson = lambda failure, trigger, correction: agent.lessons.append(
        failure
    )
    return agent


def test_a_day_list_states_both_nights_without_vetoing():
    card = build_decision_card(TaskSpec.compile(TWO_NIGHTS), [])
    dates = [constraint for constraint in card.constraints if constraint.kind == "date"]
    assert {constraint.value for constraint in dates} == {"30号", "31号"}
    assert all(not constraint.hard for constraint in dates)


def test_a_room_covering_one_requested_night_is_accepted():
    ledger = CandidateLedger()
    ledger.observe(
        "get_ota_hotel_info",
        "Hotel(hotel_id=S1_H00001, products=HotelProduct(product_id=S1_P00001, "
        "room_type=大床房, date=2026-01-30, quantity=1))",
    )
    card = build_decision_card(TaskSpec.compile(TWO_NIGHTS), [])
    assert not ledger.validate_write(
        "create_hotel_order",
        {"hotel_id": "S1_H00001", "room_id": "S1_P00001", "user_id": "U1"},
        card,
    )


def test_an_environment_fact_is_still_enforced():
    """Provenance and hard user constraints are not derived: never capped."""
    ledger = CandidateLedger()
    card = build_decision_card(TaskSpec.compile(TWO_NIGHTS), [])
    problems = ledger.validate_write(
        "create_hotel_order",
        {"hotel_id": "S1_H99999", "room_id": "S1_P99999", "user_id": "U1"},
        card,
    )
    assert any("was not returned by a tool" in problem for problem in problems)


def test_a_repeated_derived_veto_is_capped_after_three_attempts():
    agent = _agent()
    problem = "selected candidate does not satisfy required date: 31号"
    kept = [
        agent._cap_repeated_vetoes([problem]) for _ in range(agent._VETO_CAP + 1)
    ]
    assert all(entries == [problem] for entries in kept[: agent._VETO_CAP])
    assert kept[-1] == []
    assert agent.runtime.constraint_veto_counts[problem] == agent._VETO_CAP + 1
    assert agent.lessons == ["constraint_veto_capped"]
    assert any(
        event["event"] == "constraint_veto_capped" for event in agent.debug.events
    )


def test_provenance_and_authorization_are_never_capped():
    agent = _agent()
    facts = [
        "room_id=S1_P99999 was not returned by a tool in this subtask",
        "CREATE is not authorized by the user",
        "selected candidate contains forbidden value: 花生",
        "this exact order was already created in this subtask",
        "tool failure guard: the same address argument already failed 2 times",
    ]
    for _ in range(10):
        assert agent._cap_repeated_vetoes(facts) == facts
    assert agent.runtime.constraint_veto_counts == {}


def test_the_fallback_never_repeats_the_false_refusal_after_a_cap():
    agent = _agent()
    agent.runtime.phase = RuntimePhase.SELECT
    agent.runtime.constraint_veto_counts = {"date: 31号": 4}
    message = agent._fallback_message()
    assert "无法满足硬约束" not in message
    assert agent.runtime.phase == RuntimePhase.SELECT


def test_the_fallback_still_refuses_when_a_real_constraint_blocks():
    agent = _agent()
    agent.runtime.phase = RuntimePhase.SELECT
    message = agent._fallback_message()
    assert "无法满足硬约束" in message
    assert agent.runtime.phase == RuntimePhase.UNSATISFIABLE

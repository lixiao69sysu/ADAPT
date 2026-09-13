"""E-052: a derived constraint must never veto without bound.

R10's first user livelocked on one subtask: "后天要去遵义喝朋友的喜酒...先定30和31号的
就行" asks for two nights. The single-day pattern matched only "31号", so the card
demanded the 31st as a hard requirement and rejected every attempt to book the
30th -- 103 identical rejections, 78 steps, 35 user turns, and the same false
refusal text repeated to the user until the step budget was gone.
"""

from __future__ import annotations

from agent.decision import TaskSpec, build_decision_card
from agent.candidate_ledger import CandidateLedger

TWO_NIGHTS = (
    "后天要去遵义喝朋友的喜酒，顺便在那玩几天，"
    "你给我在会议会址3km范围内订个酒店吗，先定30和31号的就行"
)


class _Debug:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emit(self, event: str, **payload) -> None:
        self.events.append({"event": event, **payload})




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









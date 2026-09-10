"""Order-class tags become observable preference evidence (E-040).

A user's order history says which class of product they actually buy. Those
class words used to be dropped unless a hand-written dimension whitelist
covered them, so a rider whose history is full of 动车/二等座 looked identical
to one who only ever took 高铁.
"""

from __future__ import annotations

from agent.decision import Candidate, CandidateLedger, DecisionCard, TaskSpec
from agent.memory.adapt_memory import ADAPTMemory
from agent.memory.facts import fact_from_signal
from agent.memory.grounding import ground_facts_to_candidates
from agent.memory.signals import SignalParser

TRAIN_ORDER = {
    "scenario": "travel_ticket",
    "merchant_name": "12306",
    "tags": ["动车", "二等座"],
    "items": [
        {"product_name": "D2372 成都东-黄山北 二等座 8月5日", "price": 498, "quantity": 1}
    ],
}


def parse(content, behavior_type="order"):
    """Parse one behavior through the real init-gen interaction format."""
    interaction = {
        "date": "2022-07-23",
        "behavior": [{"behavior_type": behavior_type, "content": content}],
    }
    return SignalParser().parse([interaction])


def class_atoms(signals):
    return [
        signal.object
        for signal in signals
        if signal.predicate == "attribute_preference"
    ]


def test_order_class_tags_become_preference_signals():
    assert {"动车", "二等座"} <= set(class_atoms(parse(TRAIN_ORDER)))


def test_merchant_name_tag_is_not_a_class_preference():
    signals = parse(
        {
            "merchant_name": "肯德基",
            "tags": ["肯德基"],
            "items": [{"product_name": "香辣鸡腿堡"}],
        }
    )
    assert "肯德基" not in class_atoms(signals)


def test_numeric_and_operational_tags_are_skipped():
    signals = parse(
        {
            "merchant_name": "某店",
            "tags": ["12", "营业时间:09:00-21:00", "这个标签实在是太长了不该进来"],
            "items": [{"product_name": "套餐"}],
        }
    )
    assert class_atoms(signals) == []


def test_whitelisted_dimensions_are_not_duplicated():
    signals = parse(
        {"merchant_name": "某店", "tags": ["近地铁"], "items": [{"product_name": "套餐"}]}
    )
    assert class_atoms(signals).count("近地铁") == 1


def train(number: str, tag: str, observed_turn: int = 1) -> Candidate:
    candidate_id = f"S1_T{number}"
    raw = (
        f"Train(train_id={candidate_id}, train_number={number}, "
        f"departure_city=成都, arrival_city=绵阳, tags=['{tag}', '成绵线'])"
    )
    return Candidate(
        candidate_id,
        "train",
        number,
        raw,
        "train_ticket_search",
        {"train_number": number, "tags": f"['{tag}', '成绵线']"},
        [],
        None,
        None,
        observed_turn,
    )


def test_a_history_class_preference_grounds_against_live_candidates():
    facts = [
        fact_from_signal(signal, "e0")
        for signal in parse(TRAIN_ORDER)
        if signal.predicate == "attribute_preference"
    ]
    trains = [train("D1839", "动车"), train("G2054", "高铁")]
    grounded = ground_facts_to_candidates(facts, trains)
    values = {fact.value for fact in grounded}
    assert "动车" in values
    assert "二等座" not in values  # no seat was observed for these trains


def test_grounded_history_class_prefers_the_matching_candidate():
    memory = ADAPTMemory()
    memory.update(
        [
            {
                "date": "2022-07-23",
                "behavior": [{"behavior_type": "order", "content": TRAIN_ORDER}],
            }
        ]
    )
    ledger = CandidateLedger()
    ledger.candidates["S1_TD1839"] = train("D1839", "动车", observed_turn=1)
    ledger.candidates["S1_TG2054"] = train("G2054", "高铁", observed_turn=2)
    card = DecisionCard()
    memory.apply_candidate_grounding(card, list(ledger.candidates.values()))
    assert "动车" in card.preference_pool
    ranked = ledger.shortlist(card, limit=2)
    assert [candidate.name for candidate in ranked] == ["D1839", "G2054"]


def test_memory_keeps_the_travel_class_as_a_fact():
    memory = ADAPTMemory()
    memory.update(
        [
            {
                "date": "2022-07-23",
                "behavior": [{"behavior_type": "order", "content": TRAIN_ORDER}],
            }
        ]
    )
    assert any(fact.value == "动车" for fact in memory.facts)


def test_a_task_without_travel_class_still_compiles():
    spec = TaskSpec.compile("周六要去绵阳找朋友，帮我定张车票")
    assert spec.action == "commit"

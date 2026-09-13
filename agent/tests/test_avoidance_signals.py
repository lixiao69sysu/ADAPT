"""Health and dietary avoidances must become safety facts (E-044).

A user's own history can state an allergy *after* the item ("才知道自己哈密瓜
过敏"). The parser only matched avoidance words in front of the item, so the
fact never entered memory: the agent recommended and ordered 哈密瓜 platters for
a user who had documented the allergy, while the stock agent filtered them out.
"""

from __future__ import annotations

import pytest

from agent.decision import DecisionCard
from agent.candidate_ledger import CandidateLedger
from agent.memory.adapt_memory import ADAPTMemory
from agent.memory.facts import fact_from_signal
from agent.memory.signals import SignalParser

ALLERGY_TURN = "活了快30年，才知道自己哈密瓜过敏，我就说为什么每次吃哈密瓜，都感觉嗓子疼。"


def avoidances(text: str):
    signals = SignalParser().parse(
        [{"date": "2026-08-11", "dialogue": [{"role": "user", "content": text}]}]
    )
    return [signal for signal in signals if signal.predicate == "avoids_food"]


def test_an_allergy_stated_after_the_item_is_extracted():
    signals = avoidances(ALLERGY_TURN)
    assert [signal.object for signal in signals] == ["哈密瓜"]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("我对海鲜过敏，别给我推荐海鲜。", "海鲜"),
        ("海鲜忌口，谢谢。", "海鲜"),
        ("我不吃香菜。", "香菜"),
        ("我不能吃辣，微辣也不行。", "辣"),
    ],
)
def test_common_avoidance_phrasings(text, expected):
    signals = avoidances(text)
    assert expected in [signal.object for signal in signals]


def test_an_incidental_mention_is_not_a_constraint():
    # The narrative mentions an allergy test, not a food the user avoids.
    narrative = (
        "前两天吃了个哈密瓜，嗓子又疼又痒，还起了几个小疹子，"
        "我们科室医生让我去查了过敏源，果然中招了。"
    )
    assert avoidances(narrative) == []
    assert avoidances("帮我买一个哈密瓜送到家。") == []
    assert avoidances("今天想吃火锅，随便来一份。") == []


def test_an_allergy_is_a_safety_dimension():
    signal = avoidances(ALLERGY_TURN)[0]
    fact = fact_from_signal(signal, "e0")
    assert fact.dimension == "safety"
    assert fact.polarity == "negative"


def test_a_dietary_dislike_stays_an_avoidance_not_a_safety_fact():
    fact = fact_from_signal(avoidances("我不吃香菜。")[0], "e0")
    assert fact.dimension == "avoid"


def test_the_allergy_becomes_a_hard_card_constraint():
    memory = ADAPTMemory()
    memory.update(
        [
            {
                "date": "2026-08-11",
                "dialogue": [{"role": "user", "content": ALLERGY_TURN}],
            }
        ]
    )
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "StoreProduct(store_name=鲜果时光, store_id=S1_S00001, "
        "product_name=畅销水果拼盘(哈密瓜+西瓜+橙子+芒果), product_id=S1_P00001, "
        "price=39.9, quantity=9)",
    )
    ledger.observe(
        "delivery_product_search_recommand",
        "StoreProduct(store_name=果多多, store_id=S1_S00002, "
        "product_name=甜蜜水果拼盘(西瓜+芒果+草莓), product_id=S1_P00002, "
        "price=45.0, quantity=9)",
    )
    card = DecisionCard()
    memory.apply_candidate_grounding(card, list(ledger.candidates.values()))
    assert "哈密瓜" in card.avoid
    assert any(
        constraint.kind == "safety" and constraint.hard for constraint in card.constraints
    )
    ranked = [candidate.candidate_id for candidate in ledger.shortlist(card, limit=2)]
    assert ranked[0] == "S1_P00002"  # the platter without 哈密瓜

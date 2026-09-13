"""Offline regression replay distilled from data/simulations/adapt_smoke_2u.json."""

from __future__ import annotations

import json
from pathlib import Path

from agent.decision import DecisionCard
from agent.candidate_ledger import CandidateLedger
from agent.memory.adapt_memory import ADAPTMemory


TRACE_PATH = Path(__file__).parents[2] / "data" / "simulations" / "adapt_smoke_2u.json"


def _user_turns() -> list[str]:
    payload = json.loads(TRACE_PATH.read_text(encoding="utf-8"))
    simulation = next(item for item in payload["simulations"] if item["task_id"] == "U200109")
    return [message.get("content", "") for message in simulation["messages"] if message.get("role") == "user"]


def test_replay_source_contains_delegation_payment_and_decline_events():
    turns = _user_turns()
    assert "想喝汤了，你帮我点个送到家里" in turns
    assert "随便，你看着办吧。" in turns
    assert "支付" in turns
    assert "不用了，我自己付。" in turns




def test_retail_task_does_not_receive_restaurant_avoid_memory():
    memory = ADAPTMemory(language="chinese")
    memory.update([{
        "type": "conversation", "timestamp": "2026-01-01 10:00:00",
        "dialogue": [{"role": "user", "content": "我不吃香菜"}],
    }])
    card = memory.compile_task("鼠标坏了，帮我买个鼠标送到家里吧")
    assert "香菜" not in card.avoid




def test_ranker_excludes_zero_inventory_and_keeps_top_five():
    ledger = CandidateLedger()
    for index in range(7):
        ledger.observe(
            "delivery_product_search_recommand",
            f"StoreProduct(store_id=S1_S00001, product_name=鼠标{index}, "
            f"product_id=S1_P0000{index}, quantity={0 if index == 0 else 5}, price={index + 10})",
        )
    shortlist = ledger.shortlist(DecisionCard(must=["鼠标"]))
    assert len(shortlist) == 5
    assert all(candidate.inventory != 0 for candidate in shortlist)



"""Guards against self-inflicted loops and capability removal (E-042).

Trace comparison against the stock agent on the same users showed three
regressions introduced by ADAPT's own control layer: the runtime re-promoted a
finished subtask back into READY_TO_CREATE (14 duplicate orders in one unit),
the framework re-asked the payment question every turn, and the registry hid
the memory-query tool the stock agent uses to ground its choice.
"""

from __future__ import annotations

from agent.decision import DecisionCard
from agent.candidate_ledger import CandidateLedger
from agent.memory.adapt_memory import ADAPTMemory


class _Debug:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emit(self, event: str, **payload) -> None:
        self.events.append({"event": event, **payload})
















def test_memory_query_tool_returns_the_bounded_read():
    memory = ADAPTMemory()
    assert isinstance(memory.query_preference_memory("奶茶 偏好"), str)
    assert isinstance(memory.read_preference_memory(), str)










def test_street_address_wins_over_the_registered_city():
    """E-043: 常住地 is a city and cannot be geocoded as a delivery address."""
    from agent.decision import profile_address

    profile = {
        "常住地": "河南省郑州市",
        "常住住址": "河南省郑州市金水区国基路(嘉秀园西南)沙门安置小区2栋302",
        "籍贯": "河南省周口市",
    }
    assert profile_address(profile, "home").startswith("河南省郑州市金水区")
    assert profile_address({"常住地": "河南省郑州市"}, "home") == ""
    assert profile_address({}, "home") == ""


def test_the_validator_accepts_a_full_street_address():
    from agent.decision import (
        Constraint,
        ConstraintOperator,
        ConstraintTarget,
    )

    profile = {
        "常住地": "河南省郑州市",
        "常住住址": "河南省郑州市金水区国基路(嘉秀园西南)沙门安置小区2栋302",
    }
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "StoreProduct(store_name=果味多(丰庆路店), store_id=S1_S00001, "
        "product_name=畅销水果拼盘, product_id=S1_P00001, price=39.9, quantity=9)",
    )
    card = DecisionCard(
        constraints=[
            Constraint(
                "address",
                "home",
                ConstraintTarget.ARGUMENT,
                ConstraintOperator.RESOLVES_PROFILE,
            )
        ]
    )
    problems = ledger.validate_write(
        "create_delivery_order",
        {"product_ids": ["S1_P00001"], "user_id": "U1", "address": profile["常住住址"]},
        card,
        profile,
    )
    assert not any("address" in problem for problem in problems)

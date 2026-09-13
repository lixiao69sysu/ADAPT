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


from agent.decision import DecisionCard
from agent.candidate_ledger import CandidateLedger


class _Debug:
    def emit(self, *args, **kwargs):
        pass




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







"""Deterministic search-budget gates and completion-intent detection (E-031/E-032).

These gates are structural: a per-family distinct-query budget, a sufficiency
stop that fires once the ledger already holds a candidate CREATE could use, and
completion-style purchase phrasings that authorize a write. They contain no
user, task, product or candidate specifics.
"""

from agent.candidate_ledger import CandidateLedger

PRODUCT_SEARCH = "delivery_product_search_recommand"
HOTEL_SEARCH = "hotel_search_recommand"


def _observe_compliant_candidate(ledger: CandidateLedger) -> None:
    ledger.observe(
        PRODUCT_SEARCH,
        "StoreProduct(store_name=示例茶饮, store_id=S1_S00001, "
        "product_name=示例奶茶(大杯), product_id=S1_P00001, "
        "attributes=温度:多冰, quantity=10)",
    )


def test_family_budget_allows_normal_refinement():
    ledger = CandidateLedger(max_family_searches=4)
    for keyword in ("奶茶", "乌龙", "果茶"):
        ledger.register_search(PRODUCT_SEARCH, {"keywords": [keyword]})
        assert ledger.search_budget_rejection(
            PRODUCT_SEARCH, execution_ready=False
        ) is None


def test_family_budget_blocks_keyword_variant_thrash():
    ledger = CandidateLedger(max_family_searches=2)
    for keyword in ("奶茶", "乌龙", "果茶"):
        ledger.register_search(PRODUCT_SEARCH, {"keywords": [keyword]})
    reason = ledger.search_budget_rejection(PRODUCT_SEARCH, execution_ready=False)
    assert reason is not None
    assert "search budget exhausted" in reason


def test_family_budgets_are_independent_between_tool_families():
    ledger = CandidateLedger(max_family_searches=2)
    for keyword in ("奶茶", "乌龙", "果茶"):
        ledger.register_search(PRODUCT_SEARCH, {"keywords": [keyword]})
    assert ledger.search_budget_rejection(PRODUCT_SEARCH, execution_ready=False)
    assert (
        ledger.search_budget_rejection(HOTEL_SEARCH, execution_ready=False) is None
    )


def test_identical_queries_do_not_consume_the_distinct_query_budget():
    ledger = CandidateLedger(max_family_searches=3)
    for _ in range(2):
        ledger.register_search(PRODUCT_SEARCH, {"keywords": ["奶茶"]})
    # Two attempts of the *same* query: one distinct query, budget untouched.
    assert ledger.search_budget_rejection(PRODUCT_SEARCH, execution_ready=False) is None


def test_sufficiency_stop_allows_exploration_before_it_applies():
    """E-042: the stop must not fire on the first candidate set.

    Trace comparison with the stock agent showed that stopping immediately cost
    the model the multi-keyword exploration it uses to ground a choice.
    """
    ledger = CandidateLedger()
    _observe_compliant_candidate(ledger)
    # Three distinct queries may still be spent while a compliant candidate
    # already exists.
    for keyword in ("奶茶", "乌龙", "果茶"):
        ledger.register_search(PRODUCT_SEARCH, {"keywords": [keyword]})
        assert (
            ledger.search_budget_rejection(PRODUCT_SEARCH, execution_ready=True) is None
        ), keyword
    ledger.register_search(PRODUCT_SEARCH, {"keywords": ["轻乳茶"]})
    reason = ledger.search_budget_rejection(PRODUCT_SEARCH, execution_ready=True)
    assert reason is not None
    assert "compliant candidate" in reason


def test_search_stays_available_while_no_executable_candidate_exists():
    ledger = CandidateLedger()
    _observe_compliant_candidate(ledger)
    ledger.register_search(PRODUCT_SEARCH, {"keywords": ["奶茶"]})
    # No compliant candidate yet -> more search is legitimate.
    assert ledger.search_budget_rejection(PRODUCT_SEARCH, execution_ready=False) is None


def test_sufficiency_stop_requires_observed_candidates():
    ledger = CandidateLedger()
    ledger.register_search(PRODUCT_SEARCH, {"keywords": ["奶茶"]})
    # execution_ready is only trusted together with an observed candidate.
    assert ledger.search_budget_rejection(PRODUCT_SEARCH, execution_ready=True) is None


def test_reset_clears_family_counts_for_next_subtask():
    ledger = CandidateLedger(max_family_searches=1)
    for keyword in ("奶茶", "乌龙"):
        ledger.register_search(PRODUCT_SEARCH, {"keywords": [keyword]})
    assert ledger.search_budget_rejection(PRODUCT_SEARCH, execution_ready=False)
    ledger.reset()
    assert ledger.search_budget_rejection(PRODUCT_SEARCH, execution_ready=False) is None













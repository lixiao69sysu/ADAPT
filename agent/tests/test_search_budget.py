"""Deterministic search-budget gates and completion-intent detection (E-031/E-032).

These gates are structural: a per-family distinct-query budget, a sufficiency
stop that fires once the ledger already holds a candidate CREATE could use, and
completion-style purchase phrasings that authorize a write. They contain no
user, task, product or candidate specifics.
"""

from agent.decision import CandidateLedger, TaskSpec
from agent.runtime.state import RuntimePhase, TaskRuntime

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


def test_completion_style_purchase_phrasing_authorizes_create():
    runtime = TaskRuntime.begin(TaskSpec.compile("帮我找个养生休闲的地方，顺便团一张券"))
    for text in ("帮我团一张", "给我团个券", "订个位子吧", "来一份", "买两张"):
        runtime.authorization.create_authorized = False
        runtime.observe_user(text)
        assert runtime.authorization.create_authorized, text


def test_looking_for_a_voucher_does_not_authorize_a_write():
    runtime = TaskRuntime.begin(TaskSpec.compile("帮我找个养生休闲的地方"))
    runtime.observe_user("帮我看看有没有团购券")
    assert not runtime.authorization.create_authorized


def test_selection_after_recommendation_reaches_write_phase():
    runtime = TaskRuntime.begin(TaskSpec.compile("帮我找个地方"))
    runtime.observe_candidates(3, execution_ready=True)
    assert runtime.phase == RuntimePhase.SELECT
    # The user picks one and asks for the voucher in the same turn.
    runtime.observe_user("好的，就选第一个吧，帮我团一张")
    runtime.observe_candidates(3, execution_ready=True)
    assert runtime.authorization.create_authorized
    assert runtime.selection_made
    assert runtime.phase == RuntimePhase.READY_TO_CREATE


def test_explicit_purchase_request_never_dead_ends_in_search():
    """E-033 + E-049: an authorized write must not be stranded in SEARCH.

    SEARCH forbids CREATE and so does SELECT, but SELECT still lets the model
    ask about the open choice, and the bounded SELECT budget guarantees that an
    unanswered choice cannot terminalise the subtask.
    """
    runtime = TaskRuntime.begin(TaskSpec.compile("帮我找个养生休闲的地方"))
    runtime.observe_candidates(30, execution_ready=True)
    assert runtime.phase == RuntimePhase.SELECT
    # The user never names an ordinal; they ask to be served directly.
    runtime.observe_user("那不行，你赶紧给我团一张啊，我都说好了。")
    assert runtime.authorization.create_authorized
    assert runtime.authorization.candidate_choice_authorized
    # The turn reopens observation, where the model may search or ask. The
    # choice is still open, so CREATE is not exposed yet.
    assert runtime.phase in {RuntimePhase.SEARCH, RuntimePhase.SELECT}, runtime.phase
    assert not runtime.choice_settled()[0]
    runtime.observe_candidates(30, execution_ready=True)
    assert runtime.phase == RuntimePhase.SELECT
    # Two model generations in SELECT settle the choice for the model, so the
    # write is always reachable.
    runtime.note_select_turn()
    assert runtime.phase == RuntimePhase.SELECT
    runtime.note_select_turn()
    assert runtime.choice_settled() == (True, "bounded select budget")
    assert runtime.phase == RuntimePhase.READY_TO_CREATE


def test_user_turn_without_purchase_intent_does_not_promote():
    runtime = TaskRuntime.begin(TaskSpec.compile("帮我找个养生休闲的地方"))
    runtime.observe_candidates(30, execution_ready=True)
    runtime.observe_user("这个看起来不错，还有别的吗？")
    assert not runtime.authorization.create_authorized
    assert runtime.phase != RuntimePhase.READY_TO_CREATE



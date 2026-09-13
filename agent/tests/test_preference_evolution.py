"""Preference *evolution* conformance suite: what may be superseded, and what
must never be.

CLAUDE.md fixes the rule this file exists to guard:

    Preference drift is scoped by ``(scope, facet, dimension, category)``.
    Avoids, allergies and brands are multi-valued sets. Only genuinely
    single-valued, same-scope dimensions may supersede an older value.

Before E-090 the two mechanisms that were supposed to enforce it disagreed
about what "the same preference" was — ``DriftDetector`` keyed on
``(scope, facet, dimension, "default")`` and admitted one predicate, while
``FactStore`` superseded only on ``confirmed_drift`` and compared
``category`` as well — so supersession fired zero times over all eight dev
users' histories and single-valued slots kept every contradictory value,
rendering both to the model. The audit's success criterion was that
``superseded`` becomes non-zero and ``unresolved_single_slot_conflicts``
falls; these tests pin the *direction* of each individual transition, because
a total can improve while an individual guarantee regresses.

The multi-valued half is the regression gate, not a nicety: an added aversion
must never evict a previous aversion, and a brand must never evict a brand.
Those two tests fail loudly if the single-valued boundary is ever widened to
make a count look better.

Zero-model: no API call, no evaluator, no benchmark data.

    python -m pytest agent/tests/test_preference_evolution.py -v
"""

from __future__ import annotations

from agent.memory.adapt_memory import ADAPTMemory
from agent.memory.drift import DriftDetector
from agent.memory.fact_store import FactStore
from agent.memory.facts import SCALAR_DIMENSIONS, PreferenceFact, fact_from_signal
from agent.memory.signals import Signal


def _fact(
    fact_id: str,
    value: str,
    *,
    dimension: str,
    scope: str = "local_commerce",
    facet: str = "beverage",
    category: str = "奶茶",
    polarity: str = "positive",
    observed_at: str = "2026-01-01 00:00:00",
    source_type: str = "order",
    confidence: float = 0.8,
) -> PreferenceFact:
    return PreferenceFact(
        fact_id=fact_id,
        scope=scope,
        facet=facet,
        dimension=dimension,
        value=value,
        polarity=polarity,
        confidence=confidence,
        observed_at=observed_at,
        source_type=source_type,
        category=category,
    )


def _status(store: FactStore, value: str) -> str:
    return next(fact.status for fact in store.facts if fact.value == value)


def _active_values(store: FactStore, slot: PreferenceFact) -> set[str]:
    key = slot.slot_key
    return {
        fact.value
        for fact in store.facts
        if fact.status == "active" and fact.slot_key == key
    }


# ---------------------------------------------------------------------------
# 1. A newer value on a single-valued dimension supersedes the older one
# ---------------------------------------------------------------------------


def test_newer_temperature_supersedes_the_older_one():
    store = FactStore()
    hot = _fact("f1", "热饮", dimension="temperature", observed_at="2026-01-01 00:00:00")
    iced = _fact("f2", "冰饮", dimension="temperature", observed_at="2026-06-01 00:00:00")
    store.ingest(hot)
    assert hot.status == "active"
    store.ingest(iced)
    assert hot.status == "superseded"
    assert iced.status == "active"
    assert _active_values(store, iced) == {"冰饮"}


def test_supersession_needs_no_third_observation():
    """The change is believed on the second observation, not the third.

    The old path required ``confirmed_drift`` (two conflicts on the drift
    detector's own slot), so a change could only be recorded after the user
    had already contradicted themselves three times. A single newer value on
    an occupied slot *is* the change.
    """
    store = FactStore()
    store.ingest(_fact("f1", "热饮", dimension="temperature"))
    store.ingest(_fact("f2", "冰饮", dimension="temperature"))
    assert store.facts[0].status == "superseded"


def test_reactivated_value_retires_whichever_value_was_active_meanwhile():
    """A user may change back. 热饮 -> 冰饮 -> 热饮 leaves one active value.

    Re-observing the first value reactivates its fact; if that reactivation
    did not also retire the competing value the slot would silently hold both
    again, which is how the last eight conflicts survived the first fix.
    """
    store = FactStore()
    hot = _fact("f1", "热饮", dimension="temperature", observed_at="2026-01-01 00:00:00")
    iced = _fact("f2", "冰饮", dimension="temperature", observed_at="2026-06-01 00:00:00")
    hot_again = _fact(
        "f3", "热饮", dimension="temperature", observed_at="2026-12-01 00:00:00"
    )
    for fact in (hot, iced, hot_again):
        store.ingest(fact)
    assert hot.status == "active"
    assert iced.status == "superseded"
    assert _active_values(store, hot) == {"热饮"}


def test_the_audited_temperature_contradiction_is_resolved_end_to_end():
    """The exact slot E-090 reported: beverage temperature holding 冰饮 AND 热饮."""
    memory = ADAPTMemory()
    memory.update([{
        "date": "2026-10-01",
        "behavior": [{"behavior_type": "order", "content": {
            "merchant_name": "某奶茶店",
            "items": [{"product_name": "珍珠奶茶（冰）", "quantity": 1}],
        }}],
    }])
    memory.update([{
        "date": "2027-02-04",
        "behavior": [{"behavior_type": "order", "content": {
            "merchant_name": "某奶茶店",
            "items": [{"product_name": "布蕾奶茶（热）", "quantity": 1}],
        }}],
    }])
    temperatures = [
        fact for fact in memory.facts if fact.dimension == "temperature"
    ]
    assert temperatures, "no temperature evidence was extracted at all"
    active = {fact.value for fact in temperatures if fact.status == "active"}
    assert len(active) == 1, active
    rendered = memory.read("帮我再点一杯奶茶")
    assert "冰饮" not in rendered and "热饮" in rendered


# ---------------------------------------------------------------------------
# 2. The multi-valued gate: added aversions accumulate, never supersede
# ---------------------------------------------------------------------------


def test_added_aversions_accumulate_and_never_supersede_each_other():
    """Regression gate. An aversion is a set, not a current value."""
    store = FactStore()
    peanut = _fact(
        "n1", "花生", dimension="safety", facet="restaurant",
        category="花生", polarity="negative", observed_at="2026-01-01 00:00:00",
    )
    cilantro = _fact(
        "n2", "香菜", dimension="avoid", facet="restaurant",
        category="香菜", polarity="negative", observed_at="2026-06-01 00:00:00",
    )
    store.ingest(peanut)
    store.ingest(cilantro)
    assert peanut.status == "active", "an allergy was evicted by another aversion"
    assert cilantro.status == "active"
    assert {fact.value for fact in store.active()} == {"花生", "香菜"}


def test_two_aversions_in_one_negative_dimension_both_survive():
    """Same dimension, same facet, positive-adjacent — still additive."""
    store = FactStore()
    store.ingest(_fact(
        "n1", "内脏", dimension="avoid", facet="restaurant", category="内脏",
        polarity="negative", observed_at="2026-01-01 00:00:00",
    ))
    store.ingest(_fact(
        "n2", "肥肉", dimension="avoid", facet="restaurant", category="肥肉",
        polarity="negative", observed_at="2026-06-01 00:00:00",
    ))
    assert len(store.active()) == 2


def test_aversions_accumulate_through_the_memory_pipeline():
    memory = ADAPTMemory()
    memory.update([{
        "date": "2026-03-01", "behavior": [],
        "dialogue": [{"role": "user", "content": "我对花生过敏。"}],
    }])
    memory.update([{
        "date": "2026-03-05", "behavior": [],
        "dialogue": [{"role": "user", "content": "我也不吃香菜。"}],
    }])
    negatives = {
        fact.value for fact in memory.facts
        if fact.polarity == "negative" and fact.status == "active"
    }
    assert {"花生", "香菜"} <= negatives, negatives


# ---------------------------------------------------------------------------
# 3. Brands and products accumulate, never supersede
# ---------------------------------------------------------------------------


def test_brands_accumulate_and_never_supersede_each_other():
    """Regression gate. ``brand`` is a multi-valued set (milk tea AND hotels)."""
    memory = ADAPTMemory()
    memory.update([{
        "date": "2026-01-03",
        "behavior": [{"behavior_type": "order", "content": {
            "merchant_name": "霸王茶姬", "items": [{"product_name": "奶茶", "quantity": 1}],
        }}],
    }])
    memory.update([{
        "date": "2026-01-04",
        "behavior": [{"behavior_type": "order", "content": {
            "scenario": "hotel", "merchant_name": "亚朵酒店",
            "items": [{"product_name": "大床房", "quantity": 1}],
        }}],
    }])
    active_brands = {
        fact.value for fact in memory.facts
        if fact.dimension == "brand" and fact.status == "active"
    }
    assert len(active_brands) >= 2, active_brands
    assert not any(fact.status == "superseded" for fact in memory.facts)


def test_products_accumulate_and_never_supersede_each_other():
    store = FactStore()
    first = _fact("p1", "糯糯青山", dimension="product", source_type="order")
    second = _fact(
        "p2", "豆乳黑麒麟", dimension="product", source_type="order",
        observed_at="2026-06-01 00:00:00",
    )
    store.ingest(first)
    store.ingest(second)
    assert first.status == "active" and second.status == "active"


def test_multi_valued_dimensions_are_outside_the_single_valued_set():
    """The boundary itself, asserted directly."""
    for multi_valued in ("avoid", "safety", "brand", "product", "like", "searches"):
        assert multi_valued not in SCALAR_DIMENSIONS


# ---------------------------------------------------------------------------
# 4. Scope isolation
# ---------------------------------------------------------------------------


def test_a_fact_in_another_scope_does_not_supersede():
    store = FactStore()
    beverage = _fact("f1", "热饮", dimension="temperature", scope="local_commerce")
    hotel = _fact(
        "f2", "冰饮", dimension="temperature", scope="ota", facet="hotel",
        category="hotel", observed_at="2026-06-01 00:00:00",
    )
    store.ingest(beverage)
    store.ingest(hotel)
    assert beverage.status == "active"
    assert {fact.status for fact in store.facts} == {"active"}


def test_another_category_in_the_same_scope_does_not_supersede():
    """奶茶's temperature is not 咖啡's temperature."""
    store = FactStore()
    milk_tea = _fact("f1", "热饮", dimension="temperature", category="奶茶")
    coffee = _fact(
        "f2", "冰饮", dimension="temperature", category="咖啡",
        observed_at="2026-06-01 00:00:00",
    )
    store.ingest(milk_tea)
    store.ingest(coffee)
    assert milk_tea.status == "active"
    assert coffee.status == "active"


def test_another_facet_in_the_same_scope_does_not_supersede():
    store = FactStore()
    drink = _fact("f1", "热饮", dimension="temperature", facet="beverage")
    restaurant = _fact(
        "f2", "冰镇", dimension="temperature", facet="restaurant",
        category="restaurant", observed_at="2026-06-01 00:00:00",
    )
    store.ingest(drink)
    store.ingest(restaurant)
    assert drink.status == "active" and restaurant.status == "active"


# ---------------------------------------------------------------------------
# 5. Re-observing the same value reinforces rather than supersedes
# ---------------------------------------------------------------------------


def test_same_value_reinforces_without_superseding():
    store = FactStore()
    first = _fact("f1", "热饮", dimension="temperature", confidence=0.6)
    again = _fact(
        "f2", "热饮", dimension="temperature", observed_at="2026-06-01 00:00:00",
        confidence=0.9,
    )
    store.ingest(first)
    store.ingest(again)
    assert len(store.facts) == 1, "a repeat created a second fact for one slot"
    assert store.facts[0].status == "active"
    assert store.facts[0].confidence == 0.9
    assert store.facts[0].observed_at == "2026-06-01 00:00:00"


def test_reinforcement_and_supersession_are_idempotent_together():
    """Drift-confirmed and new-value supersession may both apply; one outcome."""
    store = FactStore()
    store.ingest(_fact("f1", "热饮", dimension="temperature"))
    store.ingest(
        _fact("f2", "冰饮", dimension="temperature", observed_at="2026-06-01 00:00:00"),
        confirmed_drift=True,
    )
    assert [fact.status for fact in store.facts] == ["superseded", "active"]


# ---------------------------------------------------------------------------
# 6. Negatives never participate in drift
# ---------------------------------------------------------------------------


def test_a_negative_fact_has_no_drift_slot():
    detector = DriftDetector(drift_threshold=2)
    assert detector._slot_key(
        Signal("avoids_food", "花生", 0.9, "2026-01-01", "complaint")
    ) is None


def test_negatives_never_drift_even_when_repeated():
    detector = DriftDetector(drift_threshold=2)
    for value in ("花生", "香菜", "内脏"):
        for index in range(3):
            assert detector.observe(Signal(
                "avoids_food", value, 0.9, f"2026-01-0{index + 1}", "complaint",
            )) is None
    assert detector.drift_summary() == []
    assert detector.slots == {}


def test_the_drift_slot_is_the_slot_the_store_supersedes():
    """The two mechanisms must agree on what "the same preference" is.

    Disagreement here is the whole of E-090 root cause 2: the detector keyed on
    ``(scope, facet, dimension, "default")`` while the store compared
    ``category`` too, so a store supersession could never be confirmed.
    """
    detector = DriftDetector(drift_threshold=2)
    for signal in (
        Signal("taste_preference", "少糖", 0.8, "2026-01-01", "order"),
        Signal("explicit_preference", "大床房", 0.8, "2026-01-01", "conversation",
               raw="酒店要房型大床房"),
        Signal("brand_loyalty", "霸王茶姬", 0.8, "2026-01-01", "order"),
        Signal("avoids_food", "花生", 0.9, "2026-01-01", "complaint"),
    ):
        key = detector._slot_key(signal)
        if key is None:
            continue
        assert key == fact_from_signal(signal).slot_key


def test_a_counterexample_against_taste_preference_alone_still_drifts():
    """Widening participation must not have removed the original capability."""
    detector = DriftDetector(drift_threshold=2)
    detector.observe(Signal("taste_preference", "少糖", 0.8, "2026-01-01", "order"))
    detector.observe(Signal("taste_preference", "无糖", 0.8, "2026-02-01", "order"))
    assert detector.observe(Signal("taste_preference", "无糖", 0.8, "2026-03-01", "order"))
    assert detector.drift_summary()[0]["value"] == "无糖"


def test_a_scalar_dimension_from_another_predicate_participates():
    """``explicit_preference`` yielding a single-valued dimension used to be
    invisible to the detector because only ``taste_preference`` was whitelisted.
    """
    detector = DriftDetector(drift_threshold=2)
    first = Signal(
        "explicit_preference", "大床房", 0.8, "2026-01-01", "conversation",
        raw="酒店要房型大床房",
    )
    second = Signal(
        "explicit_preference", "双床房", 0.8, "2026-02-01", "conversation",
        raw="酒店要房型双床房",
    )
    assert detector.observe(first) is None
    assert detector.observe(second) is None
    assert detector.observe(second) == "explicit_preference"


# ---------------------------------------------------------------------------
# 7. Answers recorded from the proactive loop still land as before
# ---------------------------------------------------------------------------


def test_a_recorded_answer_still_lands_as_a_fact():
    memory = ADAPTMemory()
    memory.begin_subtask("下周去上海，帮我买票")
    assert memory.commit_question("这次出行您想用哪种方式？", slot="transport")
    assert memory.record_user_answer("坐高铁")
    stored = {fact.value: fact for fact in memory.facts}
    assert "坐高铁" in stored
    assert stored["坐高铁"].dimension == "transport"
    assert stored["坐高铁"].status == "active"


def test_a_recorded_answer_supersedes_the_value_it_corrects():
    """Answering "不是" is a change of mind, not a second competing value."""
    memory = ADAPTMemory()
    memory.begin_subtask("帮我点杯奶茶")
    # The slot the proactive answer will land in: record_user_answer takes its
    # scope/facet/category from the compiled TaskSpec, not from the reply text.
    memory.fact_store.ingest(_fact(
        "old", "无糖", dimension="sweetness", scope="delivery", facet="beverage",
        category="beverage", observed_at="2026-01-01 00:00:00",
    ))
    resolved_slot = _fact(
        "probe", "少糖", dimension="sweetness", scope="delivery", facet="beverage",
        category="beverage",
    ).slot_key
    assert memory.fact_store.facts[0].slot_key == resolved_slot
    memory.commit_question(
        "您之前提到无糖，这次还是这样吗？",
        slot="sweetness", value="无糖", is_confirmation=True,
    )
    assert memory.record_user_answer("不是，这次少糖")
    sweetness = [
        fact for fact in memory.facts if fact.dimension == "sweetness"
    ]
    # The corrected-away value stays in the store as history (superseded, not
    # deleted); it must no longer be *active*.
    assert {fact.value for fact in sweetness if fact.status == "active"} == {"少糖"}
    assert "无糖" not in memory.read("帮我点杯奶茶")
    assert "无糖" not in memory.read("帮我点杯奶茶")

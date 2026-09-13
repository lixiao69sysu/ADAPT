"""Integration-focused tests for the complete ADAPT agent architecture."""

from __future__ import annotations

from agent.decision import (
    Constraint,
    ConstraintOperator,
    ConstraintTarget,
    DecisionCard,
    TaskSpec,
    build_decision_card,
)
from agent.candidate_ledger import CandidateLedger
from agent.memory.adapt_memory import ADAPTMemory
from agent.memory.drift import DriftDetector
from agent.memory.fact_store import FactStore
from agent.memory.facts import PreferenceFact, fact_from_signal
from agent.memory.signals import Signal, SignalParser
from agent.runtime.alignment import EvidenceAlignment
from agent.runtime.ranking import CandidateRanker


def test_read_is_pure_across_framework_repeats():
    memory = ADAPTMemory(language="chinese")
    query = "下周去上海，帮我买票"
    before = memory.proactive.asked_this_subtask
    outputs = [memory.read(query) for _ in range(3)]
    assert outputs[0] == outputs[1] == outputs[2]
    assert memory.proactive.asked_this_subtask == before


def test_question_budget_changes_only_on_commit_and_answer_is_recorded():
    memory = ADAPTMemory(language="chinese")
    memory.begin_subtask("下周去上海，帮我买票")
    question = memory.propose_question("下周去上海，帮我买票")
    assert question
    assert memory.proactive.asked_this_subtask == 0
    assert memory.commit_question(question)
    assert not memory.commit_question(question)
    assert memory.proactive.asked_this_subtask == 1
    assert memory.record_user_answer("坐高铁")
    assert any(fact.value == "坐高铁" for fact in memory.facts)
    assert any(fact.dimension == "transport" for fact in memory.facts)




def test_weak_or_conflicting_room_history_does_not_suppress_question():
    """A weak trace must not pin the slot; a settled change must settle it.

    The second half of this test used to assert the *opposite* observable:
    that two active ``room_type`` values left the slot unresolved, so the
    question was still asked. That assertion pinned the defect E-090 fixes —
    a single-valued slot holding both the old and the new value forever. With
    supersession live, the later value is the answer, and the observable is
    that the weak search trace neither blocks it nor is blocked by it.
    """
    memory = ADAPTMemory(language="chinese")
    memory.fact_store.ingest(PreferenceFact(
        "weak", "ota", "hotel", "product", "临湖双床房", "positive",
        0.5, "2026-01-01", "search", category="hotel",
        decision_eligible=False,
    ))
    instruction = "帮我订一家酒店"
    assert memory.propose_question(instruction, "ota")
    memory.fact_store.ingest(PreferenceFact(
        "twin", "ota", "hotel", "room_type", "双床房", "positive",
        0.9, "2026-01-02", "conversation", category="hotel",
    ))
    memory.fact_store.ingest(PreferenceFact(
        "queen", "ota", "hotel", "room_type", "大床房", "positive",
        0.9, "2026-01-03", "conversation", category="hotel",
    ))
    assert memory.resolve_task_slots(instruction).get("room_type") == "大床房"
    # The slot is settled, so whatever gap the policy asks about next is not
    # this one. (It may ask about the city, which is a genuine user-only gap.)
    follow_up = memory.propose_question(instruction, "ota") or ""
    assert not any(marker in follow_up for marker in ("房型", "大床", "双床"))


def test_framework_answer_is_written_to_the_typed_task_slot():
    memory = ADAPTMemory(language="chinese")
    memory.begin_subtask("帮我买一双拖鞋送到家")
    assert memory.commit_question("请告诉我需要的尺码。")
    assert memory.record_user_answer("42-43码", dimension="size")
    fact = next(fact for fact in memory.facts if fact.value == "42-43码")
    assert fact.dimension == "size"
    assert fact.facet == "retail"


def test_multiple_avoids_and_brands_do_not_compete():
    drift = DriftDetector(drift_threshold=2)
    signals = [
        Signal("avoids_food", "香菜", 0.9, "2026-01-01", "complaint"),
        Signal("avoids_food", "花生", 0.9, "2026-01-02", "complaint"),
        Signal("brand_loyalty", "霸王茶姬", 0.8, "2026-01-03", "order", raw="奶茶"),
        Signal("brand_loyalty", "亚朵酒店", 0.8, "2026-01-04", "order", raw="酒店"),
    ]
    for signal in signals:
        drift.observe(signal)
    assert not drift.drift_summary()


def test_candidate_ledger_rejects_unobserved_id_and_wrong_variant():
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "StoreProduct(store_name=霸王茶姬, store_id=S1_S00001, "
        "product_name=糯糯青山(大杯), product_id=S1_P00001, "
        "attributes=口味:半糖, 温度:多冰, quantity=2)",
    )
    card = DecisionCard(must=["糯糯青山", "少糖", "多冰"])
    wrong = ledger.validate_write(
        "create_delivery_order",
        {"user_id": "U1", "store_id": "S1_S00001", "product_ids": ["S1_P99999"]},
        card,
    )
    assert any("was not returned" in error for error in wrong)
    assert any("少糖" in error for error in wrong)


def test_candidate_ledger_accepts_exact_observed_variant():
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "StoreProduct(store_name=霸王茶姬, store_id=S1_S00001, "
        "product_name=糯糯青山, product_id=S1_P00001, "
        "attributes=口味:少糖, 温度:多冰, quantity=2)",
    )
    card = DecisionCard(must=["糯糯青山", "少糖", "多冰"])
    assert not ledger.validate_write(
        "create_delivery_order",
        {"user_id": "U1", "store_id": "S1_S00001", "product_ids": ["S1_P00001"]},
        card,
    )


def test_write_can_choose_within_shortlist_but_respects_explicit_selection():
    ledger = CandidateLedger()
    ledger.observe(
        "instore_product_search_recommend",
        "\n".join(
            [
                "ShopProduct(shop_id=S1_I00001, product_id=S1_P00001, "
                "name=酸菜鱼汤锅套餐（含茅蒿/冰汤圆）, price=118, quantity=20)",
                "ShopProduct(shop_id=S1_I00002, product_id=S1_P00002, "
                "name=麻辣火锅套餐（含茅蒿/冰汤圆）, price=168, quantity=20)",
            ]
        ),
    )
    # Both candidates have equal active evidence, so framework ranking must
    # leave the choice to the policy model until the user explicitly selects.
    card = DecisionCard(prefer=["茅蒿", "冰汤圆"])
    assert not ledger.validate_ranked_choice(
        {"shop_id": "S1_I00001", "product_id": "S1_P00001"}, card
    )
    assert not ledger.validate_ranked_choice(
        {"shop_id": "S1_I00002", "product_id": "S1_P00002"}, card
    )
    errors = ledger.validate_ranked_choice(
        {"shop_id": "S1_I00001", "product_id": "S1_P00001"},
        card,
        selected_candidate_id="S1_P00002",
    )
    assert any("explicitly selected" in error for error in errors)


def test_unique_preference_evidence_leader_is_advisory_not_a_gate():
    """E-045: a leading preference score must not veto the model's choice.

    The leader is still computed (it drives the framework directive and the
    preflight divergence event), but blocking a write over noisy atom counts
    cost replans and ended units in a refusal, so it no longer rejects.
    """
    ledger = CandidateLedger()
    ledger.observe(
        "instore_product_search_recommend",
        "\n".join(
            [
                "ShopProduct(shop_id=S1_I00001, product_id=S1_P00081, "
                "name=酸菜鱼汤锅套餐（含茼蒿/冰汤圆）, price=118, quantity=20)",
                "ShopProduct(shop_id=S1_I00002, product_id=S1_P00094, "
                "name=麻辣火锅聚餐4人套餐（含茼蒿/毛肚/鸭血/肥牛/冰汤圆）, "
                "price=168, quantity=20)",
            ]
        ),
    )
    card = DecisionCard(prefer=["麻辣火锅", "茼蒿", "冰汤圆"])
    leader = ledger.unique_evidence_leader(card)
    assert leader is not None
    assert leader.candidate_id == "S1_P00094"
    assert not ledger.validate_ranked_choice(
        {"shop_id": "S1_I00001", "product_id": "S1_P00081"}, card
    )
    assert not ledger.validate_ranked_choice(
        {"shop_id": "S1_I00002", "product_id": "S1_P00094"}, card
    )
    # An explicit user selection is still enforced.
    assert ledger.validate_ranked_choice(
        {"shop_id": "S1_I00001", "product_id": "S1_P00081"},
        card,
        selected_candidate_id="S1_P00094",
    )


def test_tied_preference_evidence_does_not_lock_framework_choice():
    ledger = CandidateLedger()
    ledger.observe(
        "instore_product_search_recommend",
        "\n".join(
            [
                "ShopProduct(shop_id=S1_I00001, product_id=S1_P00001, "
                "name=麻辣火锅套餐A, price=118, quantity=20)",
                "ShopProduct(shop_id=S1_I00002, product_id=S1_P00002, "
                "name=麻辣火锅套餐B, price=168, quantity=20)",
            ]
        ),
    )
    card = DecisionCard(prefer=["麻辣火锅"])
    assert ledger.unique_evidence_leader(card) is None
    assert not ledger.validate_ranked_choice(
        {"shop_id": "S1_I00001", "product_id": "S1_P00001"}, card
    )
    assert not ledger.validate_ranked_choice(
        {"shop_id": "S1_I00002", "product_id": "S1_P00002"}, card
    )


def test_open_world_alignment_induces_unseen_attribute_keys_from_candidates():
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "\n".join(
            [
                "StoreProduct(store_id=S1_S00001, product_id=S1_P00001, "
                "product_name=星云机械键盘, attributes=轴体：静音红轴, "
                "配列：75键, 连接：三模, quantity=8, "
                "tags=['静音红轴', '75键', '三模'])",
                "StoreProduct(store_id=S1_S00002, product_id=S1_P00002, "
                "product_name=极光机械键盘, attributes=轴体：青轴, "
                "配列：104键, 连接：有线, quantity=8, "
                "tags=['青轴', '104键', '有线'])",
            ]
        ),
    )
    candidates = [
        candidate
        for candidate in ledger.candidates.values()
        if candidate.entity_type == "product"
    ]
    card = DecisionCard(prefer=["静音红轴75键三模"])
    alignment = EvidenceAlignment(
        candidates, card.prefer, lambda value, raw: value in raw
    )
    atoms = {(atom.attribute_key, atom.value) for atom in alignment.atoms}
    assert {"静音红轴", "75键", "三模"} == {value for _, value in atoms}
    assert any(key == "轴体" for key, value in atoms if value == "静音红轴")
    counts = CandidateRanker.preference_match_counts(candidates, card)
    assert counts["S1_P00001"] == 3
    assert counts["S1_P00002"] == 0


def test_composite_preference_coverage_uses_same_atoms_for_every_candidate():
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "\n".join(
            [
                "StoreProduct(store_id=S1_S00001, product_id=S1_P00001, "
                "product_name=系列A角色甲Q版摆件, quantity=5, "
                "tags=['系列A', '角色甲', 'Q版', '摆件'])",
                "StoreProduct(store_id=S1_S00002, product_id=S1_P00002, "
                "product_name=系列A角色甲挂件, quantity=5, "
                "tags=['系列A', '角色甲', '挂件'])",
                "StoreProduct(store_id=S1_S00003, product_id=S1_P00003, "
                "product_name=系列A随机款, quantity=5, "
                "tags=['系列A', '随机'])",
            ]
        ),
    )
    card = DecisionCard(prefer=["系列A角色甲Q版挂件"])
    candidates = [
        candidate
        for candidate in ledger.candidates.values()
        if candidate.entity_type == "product"
    ]
    counts = CandidateRanker.preference_match_counts(candidates, card)
    assert counts == {
        "S1_P00001": 3,
        "S1_P00002": 3,
        "S1_P00003": 1,
    }
    assert ledger.unique_evidence_leader(card) is None
    gap = ledger.preference_coverage_gap(
        {"store_id": "S1_S00003", "product_ids": ["S1_P00003"]}, card
    )
    assert "maximum observable preference-coverage set" in gap


def test_open_world_alignment_does_not_transfer_attributes_across_candidate_sets():
    retail = CandidateLedger()
    retail.observe(
        "delivery_product_search_recommand",
        "StoreProduct(store_id=S1_S00001, product_id=S1_P00001, "
        "product_name=轻量跑鞋, attributes=重量：轻量, quantity=5, "
        "tags=['轻量'])",
    )
    hotel = CandidateLedger()
    hotel.observe(
        "hotel_search_recommand",
        "Hotel(hotel_id=S1_H00001, hotel_name=安静花园酒店, "
        "attributes=环境：安静, tags=['安静'])",
    )
    retail_counts = CandidateRanker.preference_match_counts(
        list(retail.candidates.values()), DecisionCard(prefer=["轻量"])
    )
    hotel_counts = CandidateRanker.preference_match_counts(
        list(hotel.candidates.values()), DecisionCard(prefer=["轻量"])
    )
    assert retail_counts["S1_P00001"] == 1
    assert hotel_counts["S1_H00001"] == 0


def test_structured_hotel_scenario_scopes_unknown_room_name_without_text_markers():
    signal = Signal(
        "prefers_product",
        "静谧睡眠空间一晚",
        0.7,
        "2026-01-01 10:00:00",
        "order",
        '{"scenario":"hotel","merchant_name":"云栖居",'
        '"items":[{"product_name":"静谧睡眠空间一晚"}]}',
    )
    fact = fact_from_signal(signal)
    assert fact.scope == "ota"
    assert fact.facet == "hotel"


def test_structured_travel_scenario_uses_observed_transport_evidence():
    signal = Signal(
        "prefers_product",
        "晨间经济舱",
        0.7,
        "2026-01-01 10:00:00",
        "order",
        '{"scenario":"travel_ticket","tags":["飞机"],'
        '"items":[{"product_name":"晨间经济舱"}]}',
    )
    fact = fact_from_signal(signal)
    assert fact.scope == "ota"
    assert fact.facet == "flight"


def test_hotel_location_preference_aligns_to_more_specific_observed_tag():
    memory = ADAPTMemory(language="chinese")
    memory.update(
        [
            {
                "date": "2026-01-01",
                "behavior": [
                    {
                        "behavior_type": "order",
                        "content": {
                            "scenario": "hotel",
                            "merchant_name": "云栖居",
                            "tags": ["近地铁口"],
                            "items": [
                                {
                                    "product_name": "静谧睡眠空间一晚",
                                    "price": 300,
                                    "quantity": 1,
                                }
                            ],
                        },
                    }
                ],
                "dialogue": [],
            }
        ]
    )
    card = memory.compile_task("帮我订一晚酒店")
    ledger = CandidateLedger()
    ledger.observe(
        "hotel_search_recommand",
        "Hotel(hotel_id=S9_H00001, hotel_name=远山居, "
        "score=4.8, tags=['近地铁口'])",
    )
    candidate = ledger.candidates["S9_H00001"]
    alignment = CandidateRanker.preference_alignment([candidate], card)
    assert any(atom.value == "近地铁" for atom in alignment.matches(candidate))


def test_candidate_grounding_recovers_fact_even_when_fixed_facet_is_wrong():
    """Facet routing may miss, but observed attributes get the final say."""
    fact = PreferenceFact(
        fact_id="f-brand",
        scope="delivery",
        facet="restaurant",
        dimension="product",
        value="青岛啤酒经典",
        polarity="positive",
        confidence=0.9,
        observed_at="2024-11-01",
        source_type="order",
    )
    card = build_decision_card(
        TaskSpec.compile("帮我买两瓶酒送到家"), [fact]
    )
    # It stays out of the compact prompt because the inferred facet differs,
    # but remains eligible for post-search grounding.
    assert "青岛啤酒经典" not in card.prefer
    assert "青岛啤酒经典" in card.preference_pool

    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "StoreProduct(store_id=S1_S00001, product_id=S1_P00001, "
        "product_name=青岛经典啤酒500ml, attributes=品牌：青岛, "
        "类型：啤酒, quantity=10, tags=['青岛', '经典', '啤酒'])\n"
        "StoreProduct(store_id=S1_S00002, product_id=S1_P00002, "
        "product_name=雪花清爽啤酒500ml, attributes=品牌：雪花, "
        "类型：啤酒, quantity=10, tags=['雪花', '清爽', '啤酒'])",
    )
    candidates = [
        candidate for candidate in ledger.candidates.values()
        if candidate.entity_type == "product"
    ]
    counts = CandidateRanker.preference_match_counts(candidates, card)
    assert counts["S1_P00001"] > counts["S1_P00002"]


def test_second_stage_retrieval_recovers_fact_crowded_out_before_search():
    memory = ADAPTMemory(language="chinese")
    for index in range(100):
        memory.fact_store.ingest(PreferenceFact(
            f"noise-{index}", "ota", "hotel", "product", f"旅居历史商品{index}",
            "positive", 0.99, f"2026-03-{index % 28 + 1:02d}", "order",
            category="hotel",
        ))
    remembered = PreferenceFact(
        "remembered", "instore", "service", "product", "星河静音三模鼠标",
        "positive", 0.55, "2025-01-01", "order", category="retail",
    )
    memory.fact_store.ingest(remembered)
    card = memory.compile_task("帮我买个鼠标送到家")
    assert remembered.value not in card.preference_pool

    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "StoreProduct(store_id=S1_S00001, product_id=S1_P00001, "
        "product_name=星河静音三模鼠标, quantity=5, tags=['静音', '三模'])\n"
        "StoreProduct(store_id=S1_S00002, product_id=S1_P00002, "
        "product_name=普通有线鼠标, quantity=5, tags=['有线'])",
    )
    stats = memory.apply_candidate_grounding(
        card, list(ledger.candidates.values())
    )
    assert remembered.value in card.preference_pool
    assert not any(value.startswith("旅居历史商品") for value in card.preference_pool)
    assert stats["grounded_positive"] == 1
    assert ledger.unique_evidence_leader(card).candidate_id == "S1_P00001"


def test_second_stage_retrieval_uses_candidate_fields_for_wrong_facet_and_avoid():
    memory = ADAPTMemory(language="chinese")
    memory.fact_store.ingest(PreferenceFact(
        "location", "delivery", "retail", "attribute", "近地铁", "positive",
        0.8, "2026-01-01", "order", category="retail",
    ))
    memory.fact_store.ingest(PreferenceFact(
        "avoid", "delivery", "beverage", "avoid", "临街嘈杂", "negative",
        0.9, "2026-01-02", "complaint", category="临街嘈杂",
    ))
    card = memory.compile_task("帮我订一晚酒店")
    ledger = CandidateLedger()
    ledger.observe(
        "hotel_search_recommand",
        "Hotel(hotel_id=S9_H00001, hotel_name=云庭酒店, "
        "tags=['近地铁口', '临街嘈杂'], quantity=3)\n"
        "Hotel(hotel_id=S9_H00002, hotel_name=山居酒店, "
        "tags=['安静'], quantity=3)",
    )
    stats = memory.apply_candidate_grounding(card, list(ledger.candidates.values()))
    assert "近地铁" in card.preference_pool
    assert "临街嘈杂" in card.avoid
    assert stats["grounded_negative"] == 1
    ranked = ledger.shortlist(card)
    assert [candidate.candidate_id for candidate in ranked] == ["S9_H00002"]


def test_second_stage_retrieval_preserves_multisource_decisiveness():
    memory = ADAPTMemory(language="chinese")
    for fact_id, source in (("search", "search"), ("browse", "high_freq_browse")):
        memory.facts.append(PreferenceFact(
            fact_id, "delivery", "retail", "product", "系列X", "positive",
            0.5, "2026-01-01", source, evidence_types=[source],
            decision_eligible=False,
        ))
    card = memory.compile_task("帮我买双运动鞋")
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "StoreProduct(store_id=S1_S00001, product_id=S1_P00001, "
        "product_name=系列X联名鞋, quantity=5, tags=['系列X'])\n"
        "StoreProduct(store_id=S1_S00002, product_id=S1_P00002, "
        "product_name=系列Y联名鞋, quantity=5, tags=['系列Y'])",
    )
    memory.apply_candidate_grounding(card, list(ledger.candidates.values()))
    assert set(card.preference_source_types["系列X"]) == {"search", "high_freq_browse"}
    assert ledger.unique_evidence_leader(card).candidate_id == "S1_P00001"


def test_tiered_runtime_routes_entities_and_protects_non_entity_facts():
    memory = ADAPTMemory(
        language="chinese", entity_index_max_entries=500
    )
    for index in range(520):
        memory.facts.append(PreferenceFact(
            f"entity-{index}", "delivery", "retail", "product", f"开放实体{index}",
            "positive", 0.7, f"2026-01-{index % 28 + 1:02d}", "order",
            category="retail",
        ))
    protected = PreferenceFact(
        "safety", "delivery", "restaurant", "safety", "花生", "negative",
        0.95, "2026-02-01", "conversation", category="花生",
    )
    memory.facts.append(protected)
    assert len(memory.entity_index) == 500
    assert memory.preference_store.facts == [protected]
    assert len(memory.facts) == 501


def test_tiered_runtime_fairness_keeps_new_small_facet_bucket():
    memory = ADAPTMemory(
        language="chinese", entity_index_max_entries=4
    )
    for index in range(8):
        memory.facts.append(PreferenceFact(
            f"retail-{index}", "delivery", "retail", "product", f"零售实体{index}",
            "positive", 0.9, "2026-01-01", "order", category="retail",
        ))
    hotel = PreferenceFact(
        "hotel", "ota", "hotel", "product", "静谧双床空间", "positive",
        0.6, "2026-01-02", "order", category="hotel",
    )
    memory.facts.append(hotel)
    assert hotel.value in {fact.value for fact in memory.entity_index.facts}
    assert len(memory.entity_index) == 4


def test_tiered_runtime_feature_flag_restores_mixed_store():
    memory = ADAPTMemory(
        language="chinese", enable_tiered_compaction=False
    )
    entity = PreferenceFact(
        "entity", "delivery", "retail", "product", "传统存储实体", "positive",
        0.7, "2026-01-01", "order", category="retail",
    )
    memory.facts.append(entity)
    assert memory.preference_store.facts == [entity]
    assert len(memory.entity_index) == 0
    assert memory.storage_stats()["tiered_compaction"] is False


def test_tiered_runtime_exact_aggregation_preserves_multisource_strength():
    memory = ADAPTMemory(language="chinese")
    for fact_id, value, source in (
        ("search", "星河·经典款", "search"),
        ("browse", "星河经典款", "high_freq_browse"),
    ):
        memory.facts.append(PreferenceFact(
            fact_id, "delivery", "retail", "product", value, "positive",
            0.5, "2026-01-01", source, category="retail",
            evidence_types=[source], decision_eligible=False,
        ))
    assert len(memory.entity_index) == 1
    merged = memory.entity_index.facts[0]
    assert set(merged.evidence_types) == {"search", "high_freq_browse"}
    assert merged.decision_eligible


def test_current_instruction_is_an_open_world_alignment_source():
    card = build_decision_card(
        TaskSpec.compile("帮我买一个三模静音键盘"), []
    )
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "StoreProduct(store_id=S1_S00001, product_id=S1_P00001, "
        "product_name=星云键盘, attributes=连接：三模, quantity=5, "
        "tags=['三模', '静音'])\n"
        "StoreProduct(store_id=S1_S00002, product_id=S1_P00002, "
        "product_name=极光键盘, attributes=连接：有线, quantity=5, "
        "tags=['有线', '青轴'])",
    )
    candidates = [
        candidate for candidate in ledger.candidates.values()
        if candidate.entity_type == "product"
    ]
    counts = CandidateRanker.preference_match_counts(candidates, card)
    assert counts["S1_P00001"] == 2
    assert counts["S1_P00002"] == 0


def test_grounded_atoms_preserve_observable_fact_strength_without_duplicates():
    card = DecisionCard(
        preference_pool=["品牌甲经典", "品牌乙经典", "品牌甲"],
        preference_weights={"品牌甲经典": 0.95, "品牌乙经典": 0.55, "品牌甲": 0.7},
    )
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "StoreProduct(store_id=S1_S00001, product_id=S1_P00001, "
        "product_name=品牌甲经典款, quantity=5, tags=['品牌甲', '经典'])\n"
        "StoreProduct(store_id=S1_S00002, product_id=S1_P00002, "
        "product_name=品牌乙经典款, quantity=5, tags=['品牌乙', '经典'])",
    )
    candidates = [
        candidate for candidate in ledger.candidates.values()
        if candidate.entity_type == "product"
    ]
    alignment = CandidateRanker.preference_alignment(candidates, card)
    # 经典 is one attribute even though several source facts repeat it.
    assert [atom.value for atom in alignment.atoms].count("经典") == 1
    scores = CandidateRanker.preference_scores(candidates, card)
    assert scores["S1_P00001"] > scores["S1_P00002"]


def test_candidate_render_exposes_dynamic_preference_evidence():
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "StoreProduct(store_id=S1_S00001, product_id=S1_P00001, "
        "product_name=三模键盘, quantity=5, tags=['三模'])",
    )
    rendered = ledger.render(DecisionCard(prefer=["三模"]))
    assert "preference_evidence=['三模']" in rendered




def test_shortlist_hard_filters_wrong_product_category():
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "\n".join(
            [
                "StoreProduct(store_id=S1_S00001, product_id=S1_P00001, "
                "product_name=软欧包(原味), price=15, quantity=30)",
                "StoreProduct(store_id=S1_S00002, product_id=S1_P00002, "
                "product_name=四季奶茶(无小料), price=12, quantity=30)",
            ]
        ),
    )
    card = DecisionCard(
        must=["奶茶"],
        prefer=["原味"],
        constraints=[Constraint("类别", "奶茶")],
    )
    shortlist = ledger.shortlist(card)
    assert [candidate.candidate_id for candidate in shortlist] == ["S1_P00002"]


def test_unserialized_category_hypernym_does_not_erase_observed_candidates():
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "StoreProduct(store_id=S1_S00001, product_id=S1_P00001, "
        "product_name=秋季连帽卫衣, attributes=适合季节：秋季, "
        "quantity=10, tags=['卫衣', '秋装', '保暖'])\n"
        "StoreProduct(store_id=S1_S00001, product_id=S1_P00002, "
        "product_name=秋季摇粒绒外套, attributes=适合季节：秋季, "
        "quantity=8, tags=['外套', '秋装', '保暖'])",
    )
    card = build_decision_card(
        TaskSpec.compile("帮我买两件秋天穿的衣服送到家"), []
    )
    assert {candidate.candidate_id for candidate in ledger.shortlist(card)} == {
        "S1_P00001", "S1_P00002"
    }
    assert not ledger.validate_write(
        "create_delivery_order",
        {
            "store_id": "S1_S00001",
            "product_ids": ["S1_P00001", "S1_P00002"],
            # "送到家" now yields a real address contract (E-051), so a
            # delivery write must carry the resolved address argument.
            "address": "郑州市金水区沙门安置小区2栋302",
        },
        card,
        {"常住住址": "郑州市金水区沙门安置小区2栋302"},
    )


def test_shoe_category_uses_product_name_not_store_name():
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "\n".join(
            [
                "StoreProduct(store_name=特步运动鞋城, store_id=S1_S00001, "
                "product_name=运动毛巾, product_id=S1_P00001, quantity=30)",
                "StoreProduct(store_name=特步运动鞋城, store_id=S1_S00001, "
                "product_name=男子休闲鞋, product_id=S1_P00002, quantity=20)",
                "StoreProduct(store_name=特步运动鞋城, store_id=S1_S00001, "
                "product_name=记忆海绵鞋垫, product_id=S1_P00003, quantity=40, "
                "tags=['鞋垫', '配件'])",
            ]
        ),
    )
    spec = TaskSpec.compile("想入一双新鞋了，给我推荐哈")
    card = build_decision_card(spec, [])
    assert [candidate.candidate_id for candidate in ledger.shortlist(card)] == [
        "S1_P00002"
    ]


def test_coffee_category_uses_name_and_tags_not_caffeine_attribute():
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "\n".join(
            [
                "StoreProduct(store_name=幸运咖, store_id=S1_S00001, "
                "product_name=低咖啡因抹茶拿铁（低咖啡因）, product_id=S1_P00001, "
                "attributes=低咖啡因, 热, 中杯, quantity=30, "
                "tags=['饮品', '抹茶', '拿铁', '低咖啡因'])",
                "StoreProduct(store_name=幸运咖, store_id=S1_S00001, "
                "product_name=低因拿铁, product_id=S1_P00002, "
                "attributes=低咖啡因, 热, 中杯, quantity=20, "
                "tags=['饮品', '咖啡', '拿铁', '低咖啡因'])",
            ]
        ),
    )
    card = DecisionCard(
        must=["咖啡", "低咖啡因"],
        constraints=[
            Constraint("category", "咖啡"),
            Constraint("caffeine", "低咖啡因"),
        ],
    )
    assert [candidate.candidate_id for candidate in ledger.shortlist(card)] == [
        "S1_P00002"
    ]


def test_retail_signals_preserve_cart_item_and_operational_preferences():
    signals = SignalParser().parse(
        [
            {
                "date": "2026-05-10",
                "behavior": [
                    {
                        "behavior_type": "order",
                        "content": {
                            "scenario": "delivery",
                            "merchant_name": "优衣库",
                            "tags": ["低饱和色系", "0-30分钟配送"],
                            "items": [{"product_name": "基础黑薄外套"}],
                        },
                    },
                    {
                        "behavior_type": "add_to_cart",
                        "content": {
                            "item_name": "Nike男跑步鞋HJ9198-003",
                            "price": 449,
                            "quantity": 1,
                        },
                    },
                ],
            }
        ]
    )
    assert any(s.predicate == "intent_product" and s.object == "Nike男跑步鞋HJ9198-003" for s in signals)
    attributes = {s.object for s in signals if s.predicate == "attribute_preference"}
    assert {"低饱和色系", "配送30分钟内"} <= attributes


def test_observable_interest_fields_are_normalized_without_domain_rules():
    signals = SignalParser().parse(
        [
            {
                "date": "2026-05-11",
                "behavior": [
                    {
                        "behavior_type": "favorite",
                        "content": {
                            "target_name": "系列X联名运动鞋",
                            "target_type": "product",
                        },
                    },
                    {
                        "behavior_type": "high_freq_browse",
                        "content": {"keyword": "系列X联名", "duration_minutes": 18},
                    },
                ],
            }
        ]
    )
    assert any(
        signal.type == "favorite"
        and signal.predicate == "intent_product"
        and signal.object == "系列X联名运动鞋"
        and signal.confidence > 0.8
        for signal in signals
    )
    assert any(
        signal.type == "high_freq_browse"
        and signal.predicate == "observable_interest"
        and signal.object == "系列X联名"
        for signal in signals
    )


def test_single_weak_interest_can_rank_but_cannot_lock_write_choice():
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "StoreProduct(store_id=S1_S00001, product_id=S1_P00001, "
        "product_name=系列X联名鞋, quantity=5, tags=['运动鞋', '系列X'])\n"
        "StoreProduct(store_id=S1_S00002, product_id=S1_P00002, "
        "product_name=系列Y联名鞋, quantity=5, tags=['运动鞋', '系列Y'])",
    )
    weak = PreferenceFact(
        "weak-search", "delivery", "retail", "searches", "系列X", "positive",
        0.4, "2026-05-11", "search", evidence_types=["search"],
        decision_eligible=False,
    )
    card = build_decision_card(TaskSpec.compile("帮我买双运动鞋"), [weak])
    ranked = ledger.shortlist(card)
    assert ranked[0].candidate_id == "S1_P00001"
    assert ledger.unique_evidence_leader(card) is None


def test_favorite_or_multisource_interest_can_be_decisive():
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "StoreProduct(store_id=S1_S00001, product_id=S1_P00001, "
        "product_name=系列X联名鞋, quantity=5, tags=['运动鞋', '系列X'])\n"
        "StoreProduct(store_id=S1_S00002, product_id=S1_P00002, "
        "product_name=系列Y联名鞋, quantity=5, tags=['运动鞋', '系列Y'])",
    )
    favorite = PreferenceFact(
        "favorite", "delivery", "retail", "product", "系列X联名运动鞋",
        "positive", 0.82, "2026-05-11", "favorite",
        evidence_types=["favorite"], decision_eligible=True,
    )
    card = build_decision_card(TaskSpec.compile("帮我买双运动鞋"), [favorite])
    assert ledger.unique_evidence_leader(card).candidate_id == "S1_P00001"

    weak_sources = [
        PreferenceFact(
            "search", "delivery", "retail", "searches", "系列X", "positive",
            0.4, "2026-05-10", "search", evidence_types=["search"],
            decision_eligible=False,
        ),
        PreferenceFact(
            "browse", "delivery", "retail", "product", "系列X", "positive",
            0.6, "2026-05-11", "high_freq_browse",
            evidence_types=["high_freq_browse"], decision_eligible=False,
        ),
    ]
    combined = build_decision_card(
        TaskSpec.compile("帮我买双运动鞋"), weak_sources
    )
    assert ledger.unique_evidence_leader(combined).candidate_id == "S1_P00001"


def test_food_preferences_do_not_cross_facets_but_safety_constraints_do():
    facts = [
        PreferenceFact(
            "restaurant-taste",
            "delivery",
            "restaurant",
            "taste",
            "麻辣锅底",
            "positive",
            0.9,
            "2026-05-15",
            "order",
        ),
        PreferenceFact(
            "restaurant-safety",
            "delivery",
            "restaurant",
            "safety",
            "花生",
            "negative",
            0.9,
            "2026-05-16",
            "conversation",
        ),
    ]
    card = build_decision_card(TaskSpec.compile("帮我点一杯奶茶"), facts)
    assert "麻辣锅底" not in card.prefer
    assert "花生" in card.avoid


def test_beverage_category_prevents_coffee_specs_from_polluting_milk_tea():
    facts = [
        PreferenceFact(
            "coffee-temp",
            "delivery",
            "beverage",
            "temperature",
            "冰饮",
            "positive",
            0.9,
            "2026-05-01",
            "order",
            category="咖啡",
        ),
        PreferenceFact(
            "milk-tea-temp",
            "delivery",
            "beverage",
            "temperature",
            "热饮",
            "positive",
            0.9,
            "2026-05-02",
            "order",
            category="奶茶",
        ),
    ]
    card = build_decision_card(TaskSpec.compile("帮我点一杯奶茶"), facts)
    assert "热饮" in card.prefer
    assert "冰饮" not in card.prefer


def test_newer_topping_avoidance_suppresses_old_likes_but_not_new_exception():
    facts = [
        PreferenceFact(
            "old-pearl",
            "delivery",
            "beverage",
            "topping",
            "招牌芋圆奶茶（冰）",
            "positive",
            0.8,
            "2024-10-12",
            "order",
            category="奶茶",
        ),
        PreferenceFact(
            "avoid-topping",
            "delivery",
            "beverage",
            "avoid",
            "小料",
            "negative",
            0.9,
            "2025-12-27",
            "conversation",
            category="小料",
        ),
        PreferenceFact(
            "new-brulee",
            "delivery",
            "beverage",
            "topping",
            "布蕾",
            "positive",
            0.9,
            "2026-05-22",
            "order",
            category="奶茶",
        ),
    ]
    card = build_decision_card(TaskSpec.compile("帮我点杯奶茶"), facts)
    assert "招牌芋圆奶茶（冰）" not in card.prefer
    assert "布蕾" in card.prefer




def test_no_topping_order_note_suppresses_catalog_topping_signal():
    signals = SignalParser().parse(
        [
            {
                "type": "order",
                "timestamp": "2026-01-01 00:00:00",
                "content": {
                    "merchant_name": "饮品店",
                    "items": [{"product_name": "珍珠奶茶（热）不加小料"}],
                },
            }
        ]
    )
    tastes = {
        signal.object
        for signal in signals
        if signal.predicate == "taste_preference"
    }
    assert "珍珠" not in tastes
    assert "热饮" in tastes


def test_food_avoidance_keeps_its_source_category():
    fact = fact_from_signal(
        Signal(
            "avoids_food",
            "小料",
            0.9,
            "2026-01-01",
            "conversation",
            raw="帮我点杯珍珠奶茶，不加小料",
        )
    )
    assert fact.category == "奶茶"


def test_milk_tea_topping_avoidance_does_not_pollute_coffee_search():
    fact = PreferenceFact(
        fact_id="no-topping",
        scope="delivery",
        facet="beverage",
        dimension="avoid",
        value="小料",
        polarity="negative",
        confidence=0.9,
        observed_at="2026-01-01",
        source_type="conversation",
        category="奶茶",
    )
    coffee = build_decision_card(TaskSpec.compile("帮我点个咖啡提神"), [fact])
    broad_drink = build_decision_card(
        TaskSpec.compile("帮我点杯喝的送到公司"), [fact]
    )
    assert "小料" not in coffee.avoid
    assert "小料" in broad_drink.avoid


def test_broth_preference_matches_semantic_hotpot_variant_before_price():
    ledger = CandidateLedger()
    ledger.observe(
        "instore_product_search_recommend",
        "\n".join(
            [
                "ShopProduct(shop_id=S1_I00001, product_id=S1_P00001, "
                "name=菌汤火锅4人套餐（含茠蒿）, price=168, quantity=20)",
                "ShopProduct(shop_id=S1_I00002, product_id=S1_P00002, "
                "name=酸菜鱼汤锅套餐（含茠蒿）, price=118, quantity=20)",
            ]
        ),
    )
    card = DecisionCard(prefer=["菌汤锅底", "茠蒿"])
    assert ledger.shortlist(card, limit=1)[0].candidate_id == "S1_P00001"


def test_broad_topping_avoidance_allows_no_topping_and_preferred_exception():
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "\n".join(
            [
                "StoreProduct(store_id=S1_S00001, product_name=四季奶茶(热), "
                "product_id=S1_P00001, attributes=温度: 热, 小料: 无, "
                "quantity=20, tags=['奶茶', '热饮'])",
                "StoreProduct(store_id=S1_S00002, product_name=布蕾奶茶(热), "
                "product_id=S1_P00002, attributes=温度: 热, 小料: 布蕾, "
                "quantity=20, tags=['奶茶', '热饮', '布蕾'])",
                "StoreProduct(store_id=S1_S00003, product_name=珍珠奶茶(热), "
                "product_id=S1_P00003, attributes=温度: 热, 小料: 珍珠, "
                "quantity=20, tags=['奶茶', '热饮', '珍珠'])",
                "StoreProduct(store_id=S1_S00004, product_name=布蕾奶茶(冰), "
                "product_id=S1_P00004, attributes=温度: 冰, 小料: 布蕾, "
                "quantity=20, price=1, tags=['奶茶', '冷饮', '布蕾'])",
            ]
        ),
    )
    card = DecisionCard(
        avoid=["小料"],
        prefer=["黑糖布蕾奶茶（热/三分糖）"],
        constraints=[
            Constraint(
                "topping",
                "小料",
                operator=ConstraintOperator.EXCLUDES,
                source="memory",
            )
        ],
    )
    ranked = [candidate.candidate_id for candidate in ledger.shortlist(card)]
    assert ranked[:2] == ["S1_P00002", "S1_P00001"]
    assert "S1_P00003" not in ranked
    assert "S1_P00004" not in ranked
    assert not ledger.validate_write(
        "create_delivery_order",
        {
            "store_id": "S1_S00002",
            "product_ids": ["S1_P00002"],
            "product_cnts": [1],
        },
        card,
    )


def test_unrelated_preferences_cannot_bypass_topping_exclusion():
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "StoreProduct(store_id=S1_S00001, product_name=珍珠奶茶, "
        "product_id=S1_P00001, attributes=大杯, 热饮, 加珍珠小料, "
        "quantity=20, tags=['奶茶', '珍珠', '热饮'])",
    )
    card = DecisionCard(
        avoid=["小料"],
        prefer=["珍珠", "热饮", "蜜雪冰城", "书亦烧仙草（人民路店）"],
        constraints=[
            Constraint(
                "avoid",
                "小料",
                operator=ConstraintOperator.EXCLUDES,
                source="memory",
            )
        ],
    )
    assert not ledger.shortlist(card)
    errors = ledger.validate_write(
        "create_delivery_order",
        {"store_id": "S1_S00001", "product_ids": ["S1_P00001"]},
        card,
    )
    assert any("小料" in error for error in errors)




def test_retail_semantic_preferences_rank_brand_muted_fast_candidate():
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "\n".join(
            [
                "StoreProduct(store_id=S1_S00001, product_name=杂牌男鞋棕色, "
                "product_id=S1_P00001, attributes=配送时长: 20分钟, "
                "quantity=50, price=69, tags=['男鞋'])",
                "StoreProduct(store_id=S1_S00002, product_name=Nike男子跑鞋灰白色, "
                "product_id=S1_P00002, attributes=配送时长: 25分钟, "
                "quantity=10, price=449, tags=['Nike', '跑鞋', '男鞋'])",
                "StoreProduct(store_id=S1_S00003, product_name=Nike男子跑鞋荧光绿, "
                "product_id=S1_P00003, attributes=配送时长: 35分钟, "
                "quantity=10, price=399, tags=['Nike', '跑鞋', '男鞋'])",
                "StoreProduct(store_id=S1_S00004, product_name=Nike男子板鞋皇家蓝白红, "
                "product_id=S1_P00004, attributes=配送时长: 25分钟, "
                "颜色: 皇家蓝白红, quantity=10, price=299, tags=['Nike', '板鞋', '男鞋'])",
            ]
        ),
    )
    card = DecisionCard(
        must=["鞋"],
        prefer=["Nike男跑步鞋HJ9198-003", "低饱和色系", "配送30分钟内"],
        constraints=[Constraint("category", "鞋")],
    )
    assert ledger.shortlist(card, limit=1)[0].candidate_id == "S1_P00002"




def test_hierarchical_room_satisfies_hotel_category_filter():
    ledger = CandidateLedger()
    ledger.observe(
        "get_ota_hotel_info",
        "\n".join(
            [
                "Hotel(hotel_id=S1_H00001, hotel_name=汉庭酒店)",
                "HotelProduct(room_type=简约大床房, date=2026-02-19, "
                "quantity=3, room_id=S1_P00001)",
            ]
        ),
    )
    card = DecisionCard(
        must=["酒店"], constraints=[Constraint("category", "酒店")]
    )
    assert [candidate.candidate_id for candidate in ledger.shortlist(card)] == [
        "S1_P00001"
    ]


def test_task_spec_captures_address_and_route_constraints():
    delivery = TaskSpec.compile("就糯香青山吧，少糖多冰，送到公司前台")
    address = next(constraint for constraint in delivery.must if constraint.kind == "address")
    assert address.value == "company"
    assert address.target.value == "argument"
    route = TaskSpec.compile("帮我订从北京到上海的高铁票")
    must = [constraint.value for constraint in route.must]
    assert "北京" in must
    assert "上海" in must


def test_instore_reservation_is_validated_as_write():
    ledger = CandidateLedger()
    errors = ledger.validate_write(
        "instore_reservation",
        {"user_id": "U1", "shop_id": "S1_I99999", "time": "2026-08-23 18:00:00"},
        DecisionCard(),
    )
    assert errors


def test_restaurant_facts_remain_in_current_task_scope():
    memory = ADAPTMemory(language="chinese")
    memory.begin_subtask("预订一家餐厅")
    memory.update([
        {
            "type": "conversation",
            "timestamp": "2026-08-22 10:00:00",
            "content": "我喜欢清淡的餐厅",
        }
    ])
    assert all(fact.scope == "instore" for fact in memory.facts)


def test_third_identical_search_is_counted_for_rejection():
    ledger = CandidateLedger()
    args = {"keywords": ["奶茶"]}
    assert ledger.register_search("delivery_product_search_recommand", args) == 1
    assert ledger.register_search("delivery_product_search_recommand", args) == 2
    assert ledger.register_search("delivery_product_search_recommand", args) == 3














def test_unpaid_order_is_tracked_until_payment():
    ledger = CandidateLedger()
    ledger.observe("create_delivery_order", "Order(order_id:OT123abc, status:unpaid)")
    assert "OT123abc" in ledger.pending_payment_ids
    ledger.observe("pay_delivery_order", "Payment successful")
    assert not ledger.pending_payment_ids




def test_mid_clause_jiu_is_not_a_selection_marker():
    """A bare 就 is a selection marker only at a clause boundary (E-074).

    "一到下午猪瘾就犯了" produced the hard requirement ('犯了'); "晚饭吃完想喝
    点什么" produced ('饭'). Those spurious constraints reached every consumer of
    ``card.must``. The discriminator is clause position, not a verb list, so a
    legitimate selection is still captured.
    """
    spurious = TaskSpec.compile("一到下午猪瘾就犯了，给我送个巧克力到单位来~")
    assert not [c.value for c in spurious.must if c.kind == "entity"]

    legitimate = TaskSpec.compile("就糯糯青山吧，少糖多冰，送到公司")
    assert "糯糯青山" in [c.value for c in legitimate.must]

    after_verb = TaskSpec.compile("就要那家大床房吧")
    assert "那家大床房" in [c.value for c in after_verb.must]


def test_question_negotiation_is_not_an_entity():  # placeholder-name-guard
    spec = TaskSpec.compile("天气不错，帮我推荐家店吧")
    for constraint in spec.must:
        assert constraint.value not in {"不错", "推荐家店"}


def test_task_spec_does_not_define_hidden_evaluation_fields():

    spec = TaskSpec.compile("就糯糯青山吧，少糖多冰，送到公司")
    assert "糯糯青山" in [constraint.value for constraint in spec.must]
    assert "少糖" in [constraint.value for constraint in spec.must]
    assert not hasattr(spec, "rubric")
    assert not hasattr(spec, "reward")
    assert not hasattr(spec, "target_product_ids")








def test_natural_order_phrasing_compiles_as_commit():
    for instruction in (
        "26号开会，提前给我点个咖啡提神",
        "下午来杯冰咖啡",
        "帮我订一间明晚的大床房",
        "帮我再点一杯奶茶送到家",
    ):
        spec = TaskSpec.compile(instruction)
        assert spec.action == "commit"


def test_ambiguous_caffeine_intent_requires_time_clarification():
    spec = TaskSpec.compile("26号开会，提前给我点个咖啡提神")
    card = build_decision_card(spec, [])
    assert "caffeine" in spec.unknown_slots
    assert not any(value in card.prefer for value in ("高咖啡因", "低咖啡因"))






def test_future_default_dialogue_becomes_structured_preference():
    signals = SignalParser().parse(
        [
            {
                "date": "2026-01-01",
                "dialogue": [
                    {"role": "user", "content": "以后吃火锅都得加一份冰汤圆"}
                ],
            }
        ]
    )
    assert any(
        signal.predicate == "explicit_preference" and signal.object == "冰汤圆"
        for signal in signals
    )






def test_conditional_restaurant_preferences_resolve_for_current_scenario():
    signals = SignalParser().parse(
        [
            {
                "date": "2026-01-01",
                "dialogue": [
                    {
                        "role": "user",
                        "content": "四个人以上还是麻辣火锅更合适，人少就吃菌汤锅",
                    }
                ],
            }
        ]
    )
    facts = [fact_from_signal(signal) for signal in signals]
    group_card = build_decision_card(
        TaskSpec.compile("今晚聚餐想吃个汤锅，帮我下单个套餐"), facts
    )
    assert "麻辣火锅" in group_card.prefer
    assert not any("菌汤" in value for value in group_card.prefer)

    small_card = build_decision_card(
        TaskSpec.compile("我一个人想吃个汤锅套餐"), facts
    )
    assert "菌汤锅" in small_card.prefer
    assert not any("麻辣" in value for value in small_card.prefer)










def test_enrichment_read_signature_is_single_use():
    ledger = CandidateLedger()
    arguments = {"hotel_id": "S1_H00001"}
    assert ledger.register_enrichment_read("get_ota_hotel_info", arguments) == 1
    assert ledger.register_enrichment_read("get_ota_hotel_info", arguments) == 2










def test_room_id_rejects_hotel_id_even_when_both_are_observed():
    ledger = CandidateLedger()
    ledger.observe(
        "hotel_search_recommand",
        "Hotel(hotel_id=S1_H00001, hotel_name=汉庭酒店)",
    )
    errors = ledger.validate_write(
        "create_hotel_order",
        {"hotel_id": "S1_H00001", "room_id": "S1_H00001", "user_id": "U1"},
        DecisionCard(),
    )
    assert any("room ID" in error for error in errors)


def test_hotel_category_is_satisfied_by_typed_create_tool():
    ledger = CandidateLedger()
    ledger.observe(
        "hotel_search_recommand",
        "Hotel(hotel_id=S1_H00001, hotel_name=汉庭酒店)",
    )
    ledger.observe(
        "get_ota_hotel_info",
        "HotelProduct(room_type=简约大床房, date=2026-02-19, quantity=1, room_id=S1_P00001)",
    )
    card = DecisionCard(constraints=[Constraint("category", "酒店")])
    assert not ledger.validate_write(
        "create_hotel_order",
        {"hotel_id": "S1_H00001", "room_id": "S1_P00001", "user_id": "U1"},
        card,
    )


def test_hotel_date_constraint_is_satisfied_by_observed_room_candidate():
    ledger = CandidateLedger()
    ledger.observe(
        "get_ota_hotel_info",
        "Hotel(hotel_id=S1_H00001, hotel_name=汉庭酒店, "
        "products=HotelProduct(room_type=大床房, date=2027-11-13, "
        "quantity=2, room_id=S1_P00001))",
    )
    card = DecisionCard(
        constraints=[
            Constraint(
                "date",
                "11月13日",
                ConstraintTarget.ARGUMENT,
                ConstraintOperator.EQUALS,
                argument_name="date",
            )
        ]
    )
    assert not ledger.validate_write(
        "create_hotel_order",
        {"hotel_id": "S1_H00001", "room_id": "S1_P00001", "user_id": "U1"},
        card,
    )


def test_hotel_date_constraint_rejects_wrong_observed_room_date():
    ledger = CandidateLedger()
    ledger.observe(
        "get_ota_hotel_info",
        "Hotel(hotel_id=S1_H00001, hotel_name=汉庭酒店, "
        "products=HotelProduct(room_type=大床房, date=2027-11-14, "
        "quantity=2, room_id=S1_P00001))",
    )
    card = DecisionCard(
        constraints=[
            Constraint(
                "date",
                "11月13日",
                ConstraintTarget.ARGUMENT,
                ConstraintOperator.EQUALS,
                argument_name="date",
            )
        ]
    )
    errors = ledger.validate_write(
        "create_hotel_order",
        {"hotel_id": "S1_H00001", "room_id": "S1_P00001", "user_id": "U1"},
        card,
    )
    assert any("selected candidate does not satisfy required date" in error for error in errors)


def test_explicit_write_date_must_agree_with_selected_ticket_candidate():
    ledger = CandidateLedger()
    ledger.observe(
        "get_ota_flight_info",
        "Flight(flight_id=S1_F00001, products=FlightProduct("
        "seat_type=经济舱, date=2027-11-14, quantity=2, seat_id=S1_P00001))",
    )
    card = DecisionCard(
        constraints=[
            Constraint(
                "date",
                "11月13日",
                ConstraintTarget.ARGUMENT,
                ConstraintOperator.EQUALS,
                argument_name="date",
            )
        ]
    )
    errors = ledger.validate_write(
        "create_flight_order",
        {
            "flight_id": "S1_F00001",
            "seat_id": "S1_P00001",
            "user_id": "U1",
            "date": "2027-11-13",
            "quantity": 1,
        },
        card,
    )
    assert any("selected candidate conflicts with required date" in error for error in errors)


def test_multiline_room_candidates_keep_hotel_parent_and_reject_cross_pairing():
    ledger = CandidateLedger()
    ledger.observe(
        "get_ota_hotel_info",
        "\n".join(
            [
                "Hotel(hotel_id=S1_H00001, hotel_name=汉庭酒店, products=HotelProduct(room_type=商务大床房, date=2026-02-19, quantity=2, room_id=S1_P00001))",
                "HotelProduct(room_type=简约大床房, date=2026-02-19, quantity=3, room_id=S1_P00002)",
            ]
        ),
    )
    assert "S1_H00001" in ledger.candidates["S1_P00002"].parent_ids
    ledger.observe(
        "hotel_search_recommand",
        "Hotel(hotel_id=S1_H00002, hotel_name=另一家酒店)",
    )
    errors = ledger.validate_write(
        "create_hotel_order",
        {"hotel_id": "S1_H00002", "room_id": "S1_P00002", "user_id": "U1"},
        DecisionCard(),
    )
    assert any("was not observed under hotel_id" in error for error in errors)


def test_product_constraints_ignore_other_products_from_same_store():
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "\n".join(
            [
                "StoreProduct(store_id=S1_S00001, product_name=经典原味奶茶, product_id=S1_P00001, attributes=热饮, 无小料, quantity=3)",
                "StoreProduct(store_id=S1_S00001, product_name=芋圆奶茶, product_id=S1_P00002, attributes=热饮, 加芋圆小料, quantity=3)",
            ]
        ),
    )
    card = DecisionCard(avoid=["小料"])
    errors = ledger.validate_write(
        "create_delivery_order",
        {"store_id": "S1_S00001", "product_ids": ["S1_P00001"]},
        card,
    )
    assert not errors


def test_payment_does_not_reapply_create_address_constraints():
    ledger = CandidateLedger()
    ledger.observe(
        "create_delivery_order",
        "Order(order_id:O123456789, status:unpaid)",
    )
    card = DecisionCard(
        constraints=[
            Constraint(
                kind="address",
                value="company",
                target=ConstraintTarget.ARGUMENT,
            )
        ]
    )
    assert not ledger.validate_write(
        "pay_delivery_order",
        {"order_id": "O123456789", "user_id": "U1"},
        card,
        {"company": "测试公司地址"},
    )


def test_category_feature_ranks_compliant_milk_tea_over_generic_tea():
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "\n".join(
            [
                "StoreProduct(store_id=S1_S00001, product_name=红枣姜茶, product_id=S1_P00001, attributes=热饮, 无小料, quantity=3, price=10)",
                "StoreProduct(store_id=S1_S00002, product_name=经典原味奶茶, product_id=S1_P00002, attributes=热饮, 无小料, quantity=3, price=10)",
            ]
        ),
    )
    card = DecisionCard(prefer=["老红糖珍珠奶茶（热）不加小料"], avoid=["小料"])
    assert ledger.shortlist(card, limit=1)[0].candidate_id == "S1_P00002"


def test_specific_hotpot_preferences_are_not_collapsed_to_category():
    ledger = CandidateLedger()
    ledger.observe(
        "instore_product_search_recommend",
        "\n".join(
            [
                "ShopProduct(shop_id=S1_I00001, product_id=S1_P00001, name=菌汤火锅4人套餐（含茼蒿/冰汤圆）, quantity=3)",
                "ShopProduct(shop_id=S1_I00002, product_id=S1_P00002, name=麻辣火锅4人套餐（含茼蒿/冰汤圆）, quantity=3)",
            ]
        ),
    )
    spec = TaskSpec.compile("今晚聚餐想吃个汤锅，帮我下单个套餐")
    assert any(c.kind == "category" and c.value == "汤锅" for c in spec.must)
    card = DecisionCard(
        prefer=["麻辣火锅", "菌汤锅底", "茼蒿", "冰汤圆"],
        constraints=spec.must,
    )
    assert ledger.shortlist(card, limit=1)[0].candidate_id == "S1_P00002"




def test_hotel_order_tags_become_scoped_service_attribute_facts():
    signals = SignalParser().parse(
        [
            {
                "type": "order",
                "timestamp": "2026-01-28 00:00:00",
                "content": {
                    "scenario": "hotel",
                    "merchant_name": "汉庭酒店（成都店）",
                    "tags": ["汉庭", "近地铁", "简约风格", "经济型"],
                    "items": [{"product_name": "大床房", "quantity": 1}],
                },
            }
        ]
    )
    attributes = {
        signal.object: fact_from_signal(signal)
        for signal in signals
        if signal.predicate == "attribute_preference"
    }
    assert attributes["近地铁"].facet == "hotel"
    assert attributes["近地铁"].dimension == "location"
    assert attributes["简约风格"].dimension == "attribute"
    assert attributes["经济型"].dimension == "budget"


def test_hotel_room_ranking_uses_parent_location_and_room_style():
    ledger = CandidateLedger()
    ledger.observe(
        "get_ota_hotel_info",
        "Hotel(hotel_id=S1_H00003, hotel_name=汉庭酒店(观音桥店), tags=['经济型'], products=HotelProduct(room_type=简约大床房, date=2026-02-19, quantity=5, room_id=S1_P00006))",
    )
    ledger.observe(
        "get_ota_hotel_info",
        "Hotel(hotel_id=S1_H00038, hotel_name=汉庭酒店(北站地铁店), tags=['近地铁', '经济型'], products=HotelProduct(room_type=简约大床房, date=2026-02-19, quantity=5, room_id=S1_P00075))",
    )
    card = DecisionCard(
        prefer=["大床房", "近地铁", "简约风格", "汉庭酒店"]
    )
    assert ledger.shortlist(card, limit=1)[0].candidate_id == "S1_P00075"








def test_id_semantics_reject_product_id_in_store_slot():
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "StoreProduct(store_id=S1_S00001, product_name=鼠标, product_id=S1_P00001, quantity=3)",
    )
    errors = ledger.validate_write(
        "create_delivery_order",
        {"user_id": "U1", "store_id": "S1_P00001", "product_ids": ["S1_P00001"]},
        DecisionCard(),
    )
    assert any("expects store ID" in error for error in errors)




def test_memory_update_is_incremental_for_replayed_interactions():
    memory = ADAPTMemory(language="chinese")
    interaction = {
        "type": "order", "timestamp": "2026-01-01 10:00:00",
        "content": {"store_name": "川菜馆", "product_name": "水煮鱼"},
    }
    memory.update([interaction])
    event_count = len(memory.stream)
    fact_count = len(memory.facts)
    memory.update([interaction])
    assert len(memory.stream) == event_count
    assert len(memory.facts) == fact_count






def test_decision_card_reserves_space_for_must_and_prefer():
    """Hard sections win; soft material fills what is left (E-060).

    This test previously asserted that ``noise-2`` was absent, i.e. that the
    third AVOID entry was dropped to leave room for PREFER. AVOID entries are
    exclusions taken from the current instruction or from durable safety facts,
    so dropping the third one is not a budget trade -- it hides a graded
    condition from the model. The contract is now: MUST and AVOID render in
    full, ``max_facts`` bounds only the soft sections.
    """
    card = DecisionCard(
        must=["address=company", "authorization=create"],
        avoid=[f"noise-{index}" for index in range(8)],
        prefer=["奶茶", "无小料", "经典原味"],
    )
    rendered = card.render()
    assert "address=company" in rendered
    assert "authorization=create" in rendered
    for index in range(8):
        assert f"noise-{index}" in rendered
    # Ten hard facts already exceed the eight-fact budget, so no soft section
    # may be appended -- and none may be half-appended either.
    assert "PREFER:" not in rendered


def test_decision_card_renders_all_hard_constraints_and_bounds_soft():
    """The 3-taboo / 4-MUST case from the E-060 reproduction."""
    taboos = ["花生", "香菜", "内脏"]
    musts = ["category=咖啡", "size=大杯", "temperature=热饮", "sweetness=无糖"]
    card = DecisionCard(must=list(musts), avoid=list(taboos), ask=["您要冰的还是热的？"])
    rendered = card.render()
    for value in taboos:
        assert value in rendered
    for value in musts:
        assert value in rendered
    # Seven hard facts leave one soft slot, which ASK takes.
    assert "ASK:" in rendered
    assert "PREFER:" not in rendered


def test_decision_card_soft_sections_still_bounded():
    """The fix must not let soft material grow without bound."""
    card = DecisionCard(prefer=[f"pref-{index}" for index in range(40)])
    rendered = card.render(max_facts=8)
    assert "pref-0" in rendered
    assert "pref-7" in rendered
    assert "pref-8" not in rendered


def test_decision_card_soft_section_is_not_half_appended():
    """A soft section that does not fit is skipped whole, not cut mid-entry.

    Cutting the joined string at ``max_chars`` would leave a dangling
    ``PREFER: aaaa...`` fragment and, worse, could cut a hard section. Here the
    long PREFER section must be skipped entirely while the short ASK section
    still fits.
    """
    card = DecisionCard(
        must=["address=company"],
        prefer=["a" * 40],
        ask=["ask-me"],
    )
    rendered = card.render(max_chars=40)
    assert rendered == "MUST: address=company\nASK: ask-me"
    assert "aaa" not in rendered


def test_empty_decision_card_falls_back_to_instruction():
    assert DecisionCard().render() == "MUST: follow the current instruction"


def test_shortlist_does_not_spend_slots_on_parent_store_ids():
    ledger = CandidateLedger()
    for index in range(6):
        ledger.observe(
            "delivery_product_search_recommand",
            f"StoreProduct(store_id=S1_S0000{index}, product_name=奶茶{index}, "
            f"product_id=S1_P0000{index}, quantity=10, price={10 + index})",
        )
    shortlist = ledger.shortlist(DecisionCard(prefer=["奶茶"]))
    assert len(shortlist) == 5
    assert all(candidate.entity_type == "product" for candidate in shortlist)


def test_fact_store_rejects_non_consumption_negative_noise_and_merges_containment():
    store = FactStore()

    def negative(value: str, fact_id: str) -> PreferenceFact:
        return PreferenceFact(
            fact_id=fact_id,
            scope="delivery",
            facet="beverage",
            dimension="avoid",
            value=value,
            polarity="negative",
            confidence=0.9,
            observed_at="2026-01-01",
            source_type="conversation",
            category=value,
        )

    assert store.ingest(negative("某外卖配送员", "noise")) is None
    store.ingest(negative("任何小料", "avoid-1"))
    store.ingest(negative("小料", "avoid-2"))
    assert [fact.value for fact in store.active()] == ["小料"]


def test_general_game_facts_do_not_pollute_hotel_decision_card():
    facts = [
        PreferenceFact(
            fact_id="game",
            scope="general",
            facet="general",
            dimension="explicit",
            value="喜欢玩无畏契约",
            polarity="positive",
            confidence=0.99,
            observed_at="2026-02-01",
            source_type="conversation",
        ),
        PreferenceFact(
            fact_id="hotel",
            scope="ota",
            facet="hotel",
            dimension="brand",
            value="汉庭酒店(遂宁店)",
            polarity="positive",
            confidence=0.8,
            observed_at="2026-01-01",
            source_type="order",
        ),
    ]
    card = build_decision_card(TaskSpec.compile("帮我订明晚重庆的酒店"), facts)
    assert "喜欢玩无畏契约" not in card.prefer
    assert "汉庭酒店(遂宁店)" in card.prefer


def test_ranker_matches_brand_root_across_city_variants():
    ledger = CandidateLedger()
    ledger.observe(
        "hotel_search_recommand",
        "Hotel(hotel_id=S1_H00001, hotel_name=希岸酒店(重庆店), score=4.9)\n"
        "Hotel(hotel_id=S1_H00002, hotel_name=汉庭酒店(重庆大坪店), score=4.5)",
    )
    shortlist = ledger.shortlist(DecisionCard(prefer=["汉庭酒店(遂宁店)"]))
    assert shortlist[0].candidate_id == "S1_H00002"


def test_explicit_facet_preference_ranks_before_brand_history():
    facts = [
        PreferenceFact(
            fact_id="brand",
            scope="local_commerce",
            facet="beverage",
            dimension="brand",
            value="某奶茶店",
            polarity="positive",
            confidence=0.99,
            observed_at="2026-02-01",
            source_type="order",
        ),
        PreferenceFact(
            fact_id="explicit",
            scope="local_commerce",
            facet="beverage",
            dimension="explicit",
            value="奶茶不加小料/原味",
            polarity="positive",
            confidence=0.9,
            observed_at="2026-01-01",
            source_type="conversation",
        ),
    ]
    card = build_decision_card(TaskSpec.compile("帮我点杯喝的送到公司"), facts)
    assert card.prefer[0] == "奶茶不加小料/原味"


def test_negated_attribute_does_not_trigger_avoid_constraint():
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "StoreProduct(store_id=S1_S00001, product_name=经典原味奶茶, "
        "product_id=S1_P00001, attributes=大杯, 热饮, 无小料, quantity=30)",
    )
    card = DecisionCard(avoid=["小料"])
    assert ledger.shortlist(card)[0].candidate_id == "S1_P00001"
    assert not ledger.validate_write(
        "create_delivery_order",
        {"store_id": "S1_S00001", "product_ids": ["S1_P00001"]},
        card,
    )


def test_current_category_constraint_does_not_directly_name_all_ota_facts():
    facts = [
        PreferenceFact(
            fact_id="ticket",
            scope="ota",
            facet="attraction",
            dimension="product",
            value="古羌城门票",
            polarity="positive",
            confidence=0.99,
            observed_at="2026-02-01",
            source_type="order",
        ),
        PreferenceFact(
            fact_id="hotel-brand",
            scope="ota",
            facet="hotel",
            dimension="brand",
            value="汉庭酒店",
            polarity="positive",
            confidence=0.8,
            observed_at="2026-01-01",
            source_type="order",
        ),
    ]
    card = build_decision_card(TaskSpec.compile("帮我订明晚重庆的酒店"), facts)
    assert "古羌城门票" not in card.prefer
    assert "汉庭酒店" in card.prefer


def test_data_layer_import_closure_excludes_retired_controller_code():
    """Importing the data layer must not load the ledger or the rankers.

    The retired controller was once dragged back into the data layer through an
    eager package ``__init__`` re-export. The ledger was split out of
    ``decision.py`` for the same reason, so this guards the property directly
    rather than trusting import hygiene by convention. A subprocess is used
    because ``sys.modules`` is shared across the test session.
    """
    import pathlib
    import subprocess
    import sys

    program = (
        "import sys, agent.memory.adapt_memory\n"
        "bad = [n for n in sys.modules if n.startswith(('agent.candidate_ledger',"
        " 'agent.runtime.ranking', 'agent.runtime.location'))]\n"
        "print('LEAKED:' + ','.join(bad))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        cwd=str(pathlib.Path(__file__).resolve().parent.parent.parent),
    )
    assert result.returncode == 0, result.stderr
    leaked = [
        line for line in result.stdout.splitlines() if line.startswith("LEAKED:")
    ]
    assert leaked, result.stdout
    assert leaked[-1] == "LEAKED:", (
        "the data layer's import closure reaches retired controller code: "
        f"{leaked[-1]}"
    )


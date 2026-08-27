from agent.candidate_attribution_report import aggregate
from agent.decision import Candidate, DecisionCard
from agent.runtime import CandidateAttributionEngine, RankingPolicy
from agent.runtime.ranking import CandidateRanker


def _shadow_fixture():
    candidates = [
        Candidate(
            "glyph-a",
            "glyph",
            "azure glyph",
            "Glyph(name=azure glyph, mode=echo)",
            "scan_glyphs",
            attributes={"mode": "echo", "kind": "glyph"},
            price=10,
            inventory=2,
        ),
        Candidate(
            "glyph-b",
            "glyph",
            "amber glyph",
            "Glyph(name=amber glyph, mode=echo)",
            "scan_glyphs",
            attributes={"mode": "echo", "kind": "glyph"},
            price=9,
            inventory=2,
        ),
    ]
    card = DecisionCard(
        task_intent=["activate echo glyph"],
        grounded_edge_count=2,
        candidate_preference_scores={"glyph-a": 2.0, "glyph-b": 1.0},
        candidate_grounding_sources={"glyph-a": ("field",), "glyph-b": ("field",)},
        candidate_grounding_confidence={"glyph-a": 0.5, "glyph-b": 1.0},
        candidate_grounding_edge_counts={"glyph-a": 1, "glyph-b": 1},
    )
    return candidates, card


def test_three_shadow_policies_have_explicit_layered_keys():
    candidates, card = _shadow_fixture()
    ranker = CandidateRanker()

    current = ranker.rank(candidates, card, policy=RankingPolicy.CURRENT)
    task_only = ranker.rank(
        candidates, card, policy=RankingPolicy.CURRENT_TASK_ONLY
    )
    task_first = ranker.rank(
        candidates,
        card,
        policy=RankingPolicy.TASK_FIRST_PREFERENCE_TIEBREAK,
    )

    assert current[0].candidate_id == "glyph-a"
    assert task_only[0].candidate_id == "glyph-b"
    assert task_first[0].candidate_id == "glyph-b"


def test_candidate_attribution_record_exposes_task_preference_and_grounding():
    candidates, card = _shadow_fixture()
    comparison = CandidateAttributionEngine.compare(candidates, card)
    record = comparison["current"].records[0]

    assert record.candidate_id == "glyph-a"
    assert record.task_relevance_band > 0
    assert record.historical_preference_score == 2.0
    assert record.grounding_sources == ("field",)
    assert record.intrinsic_grounding_count == 1
    assert record.request_only_grounding_count == 0


def test_task_first_shadows_rank_task_match_without_deleting_open_world_fallbacks():
    candidates = [
        Candidate(
            "glyph",
            "artifact",
            "echo glyph",
            "Artifact(name=echo glyph)",
            "scan",
            attributes={"kind": "echo glyph"},
            inventory=1,
        ),
        Candidate(
            "relic",
            "artifact",
            "amber relic",
            "Artifact(name=amber relic)",
            "scan",
            attributes={"kind": "amber relic"},
            inventory=1,
        ),
    ]
    card = DecisionCard(task_intent=["activate echo glyph"])
    comparison = CandidateAttributionEngine.compare(candidates, card)

    assert comparison["current"].summary.top3_ids == ("glyph", "relic")
    assert comparison["current_task_only"].summary.top3_ids == ("glyph", "relic")
    assert comparison["task_first_preference_tiebreak"].summary.top3_ids == (
        "glyph",
        "relic",
    )


def test_request_only_grounding_is_reported_as_confusion_not_fulfillment():
    candidates, card = _shadow_fixture()
    card.candidate_grounding_sources["glyph-a"] = ("note",)
    batch = CandidateAttributionEngine.build(candidates, card)

    assert batch.summary.request_only_grounding_confusions == 1
    flagged = next(
        record for record in batch.records if record.candidate_id == "glyph-a"
    )
    assert flagged.intrinsic_grounding_count == 0
    assert flagged.request_only_grounding_count == 1


def test_request_object_outranks_incidental_context_noun():
    candidates = [
        Candidate(
            "rice",
            "product",
            "braised rice",
            "Product(name=braised rice)",
            "scan",
            attributes={"name": "卤肉饭"},
            inventory=10,
        ),
        Candidate(
            "noodle",
            "product",
            "spicy noodles",
            "Product(name=spicy noodles)",
            "scan",
            attributes={"name": "酸辣粉"},
            inventory=10,
        ),
    ]
    # Both 饭 and 粉 occur in the full utterance, but only 粉 is the object
    # of the current desire.  This is a syntactic capability test, not an
    # entity-specific rule.
    card = DecisionCard(task_intent=["晚饭在家里对付一下，想吃点粉类的"])

    ranked = CandidateRanker().rank(candidates, card, limit=2)

    assert [candidate.candidate_id for candidate in ranked] == ["noodle", "rice"]


def test_request_object_grounding_generalizes_to_unseen_schema_values():
    candidates = [
        Candidate(
            "moon-vessel",
            "artifact",
            "moon vessel",
            "Artifact(name=moon vessel)",
            "scan",
            attributes={"glyph": "月影容器"},
            inventory=2,
        ),
        Candidate(
            "star-scroll",
            "artifact",
            "star scroll",
            "Artifact(name=star scroll)",
            "scan",
            attributes={"glyph": "星辉卷轴"},
            inventory=2,
        ),
    ]
    card = DecisionCard(task_intent=["今晚看星星，帮我买个月影容器送到家"])

    ranked = CandidateRanker().rank(candidates, card, limit=2)

    assert [candidate.candidate_id for candidate in ranked] == [
        "moon-vessel",
        "star-scroll",
    ]


def test_postposed_request_qualifier_outranks_head_noun_only_match():
    candidates = [
        Candidate(
            "translated",
            "artifact",
            "translated story",
            "Artifact(name=translated story)",
            "scan",
            attributes={"类别": "小说", "语言": "中文"},
            inventory=3,
        ),
        Candidate(
            "original",
            "artifact",
            "original story",
            "Artifact(name=original story)",
            "scan",
            attributes={"类别": "小说", "语言": "日文", "版本": "原著"},
            inventory=3,
        ),
    ]
    card = DecisionCard(task_intent=["帮我下单一本小说，日文原著的"])

    ranked = CandidateRanker().rank(candidates, card, limit=2)

    assert [candidate.candidate_id for candidate in ranked] == [
        "original",
        "translated",
    ]


def test_legacy_compound_attributes_are_intrinsic_but_parent_name_is_not():
    candidates = [
        Candidate(
            "essay",
            "product",
            "foreign essay",
            "StoreProduct(store_name=Moon Novel Shop, store_id=S1, "
            "product_name=foreign essay, product_id=P1, attributes=language:moon, "
            "category:essay, quantity=4)",
            "scan",
            attributes={"store_name": "Moon Novel Shop", "attributes": "language:moon"},
            inventory=4,
        ),
        Candidate(
            "novel",
            "product",
            "foreign work",
            "StoreProduct(store_name=Plain Shop, store_id=S2, "
            "product_name=foreign work, product_id=P2, attributes=language:moon, "
            "category:echo novel, quantity=4)",
            "scan",
            attributes={"store_name": "Plain Shop", "attributes": "language:moon"},
            inventory=4,
        ),
    ]
    card = DecisionCard(task_intent=["buy an echo novel, moon-language edition"])

    ranked = CandidateRanker().rank(candidates, card, limit=2)

    assert [candidate.candidate_id for candidate in ranked] == ["novel", "essay"]


def test_candidate_report_aggregates_five_requested_metrics():
    candidates, card = _shadow_fixture()
    events = []
    for policy, batch in CandidateAttributionEngine.compare(candidates, card).items():
        events.append(
            {
                "event": "candidate_attribution",
                "policy": policy,
                "instruction_epoch": 1,
                "candidate_version": 1,
                "summary": batch.summary.as_dict(),
                "top3": [record.as_dict() for record in batch.records[:3]],
            }
        )
    events.append({"event": "create_consistency", "consistent": True})

    report = aggregate(events)

    assert set(report["policies"]) == {
        "current",
        "current_task_only",
        "task_first_preference_tiebreak",
    }
    assert "top3_non_task_category_rate" in report["policies"]["current"]
    assert "historical_outranks" in report["policies"]["current"]
    assert "no_executable" in report["policies"]["current"]
    assert "request_only_confusions" in report["policies"]["current"]
    assert report["create_consistency"]["rate"] == 1.0

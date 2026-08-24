"""Counterfactual compaction policies stay generic and observable-only."""

from types import SimpleNamespace

from agent.compaction_audit import (
    aggregate_entity_facts,
    audit_compaction,
    fair_entity_selection,
)
from agent.memory.facts import PreferenceFact


def _fact(
    fact_id: str,
    value: str,
    *,
    facet: str = "retail",
    dimension: str = "product",
    source: str = "order",
    evidence_types=None,
):
    return PreferenceFact(
        fact_id, "delivery", facet, dimension, value, "positive", 0.7,
        "2026-01-01", source, category=facet,
        evidence_types=list(evidence_types or [source]),
        decision_eligible=source not in {"search", "browse", "high_freq_browse"},
    )


def test_entity_aggregation_is_exact_normalized_not_fuzzy():
    merged = aggregate_entity_facts([
        _fact("a", "星河·经典款", source="search"),
        _fact("b", "星河经典款", source="high_freq_browse"),
        _fact("c", "星河经典大杯款", source="order"),
    ])
    assert len(merged) == 2
    combined = next(fact for fact in merged if fact.value != "星河经典大杯款")
    assert set(combined.evidence_types) == {"search", "high_freq_browse"}
    assert combined.decision_eligible


def test_fair_selection_preserves_small_observed_facet_bucket():
    dominant = [_fact(f"retail-{index}", f"零售实体{index}") for index in range(20)]
    hotel = _fact("hotel", "静谧双床空间", facet="hotel")
    hotel.scope = "ota"
    selected = fair_entity_selection([*dominant, hotel], limit=4)
    assert hotel in selected
    assert len(selected) == 4


class VisibleSubtask:
    subtask_id = "synthetic-subtask"
    domain = "ota"
    instruction = "帮我订一家酒店"
    interactions = [{
        "date": "2026-01-01",
        "behavior": [{
            "behavior_type": "order",
            "content": {
                "scenario": "hotel",
                "merchant_name": "云庭旅居",
                "items": [{"product_name": "静谧双床空间"}],
            },
        }],
        "dialogue": [{"role": "user", "content": "我不吃花生，以后多人聚餐都要麻辣火锅"}],
    }]

    def __getattr__(self, name):
        if name in {
            "evaluation_criteria", "user_intention", "skill_tested", "reward",
            "target_product_ids", "environment", "user_scenario",
        }:
            raise AssertionError(f"audit accessed hidden field: {name}")
        raise AttributeError(name)


def test_counterfactual_audit_is_aggregate_and_hidden_field_free():
    task = SimpleNamespace(id="synthetic-user", subtasks=[VisibleSubtask()])
    report = audit_compaction([task], [task.id])
    data = report.to_dict()
    assert report.users == 1
    assert data["data_access"]["model_calls"] == 0
    assert data["data_access"]["per_user_findings_emitted"] is False
    assert "synthetic-user" not in str(data)

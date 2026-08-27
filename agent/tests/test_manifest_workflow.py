"""Fictional-schema gates for lossless grounding and workflow compilation."""

import json

from agent.decision import CandidateLedger, Constraint, TaskSpec
from agent.intent import CompletionContract, DesiredOutcome
from agent.runtime import (
    CandidateManifestParser,
    ExecutionWorkflowGraph,
    SearchPlan,
)
from agent.runtime.contracts import IdVariable, ToolContract


def _schema():
    return {
        "type": "object",
        "properties": {
            "payload": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "vault_id": {"type": "string"},
                        "relic_id": {"type": "string"},
                        "vault_name": {"type": "string"},
                        "relic_name": {"type": "string"},
                        "stock": {"type": "integer"},
                        "cost": {"type": "number"},
                        "unknown_traits": {
                            "type": "object",
                            "properties": {"aura": {"type": "string"}},
                        },
                    },
                },
            }
        },
    }


def test_nested_unknown_schema_builds_lossless_parent_child_manifests():
    payload = {
        "payload": [
            {
                "vault_id": "parent::violet",
                "relic_id": "leaf::17",
                "vault_name": "Violet Vault",
                "relic_name": "Glass Glyph",
                "stock": 4,
                "cost": 19.5,
                "unknown_traits": {"aura": "azure"},
            }
        ]
    }
    manifests = CandidateManifestParser.parse("discover_artifacts", payload, _schema())

    assert [item.candidate_id for item in manifests] == [
        "parent::violet",
        "leaf::17",
    ]
    leaf = manifests[1]
    assert leaf.entity_type == "relic"
    assert leaf.parent_ids == ("parent::violet",)
    assert leaf.inventory == 4
    assert leaf.price == 19.5
    assert leaf.attributes()["payload.0.unknown_traits.aura"] == "azure"

    ledger = CandidateLedger()
    ledger.observe("discover_artifacts", json.dumps(payload), result_json_schema=_schema())
    assert ledger.candidates["leaf::17"].parent_ids == ["parent::violet"]
    assert "payload.0.unknown_traits.aura" in ledger.candidates["leaf::17"].attributes


def test_workflow_graph_is_compiled_from_entities_not_tool_vocabulary():
    discover = ToolContract(
        "phase_alpha",
        "search",
        (),
        (),
        observation_entities=("vault", "relic"),
    )
    create = ToolContract(
        "phase_beta",
        "create",
        ("vault_ref", "relic_ref"),
        (
            IdVariable("vault_ref", "vault", True),
            IdVariable("relic_ref", "relic", True),
        ),
        observation_entities=("receipt",),
    )
    settle = ToolContract(
        "phase_gamma",
        "pay",
        ("receipt_ref",),
        (IdVariable("receipt_ref", "receipt", True),),
    )

    graph = ExecutionWorkflowGraph.compile((discover, create, settle))
    edges = {(edge.producer, edge.consumer, edge.entity_type) for edge in graph.edges}
    assert ("phase_alpha", "phase_beta", "vault") in edges
    assert ("phase_alpha", "phase_beta", "relic") in edges
    assert ("phase_beta", "phase_gamma", "receipt") in edges


def test_search_plan_preserves_current_constraints_without_category_words():
    spec = TaskSpec(
        instruction="bind an azure glyph but avoid brittle ones",
        domain="",
        facet="",
        action="commit",
        completion=CompletionContract(DesiredOutcome.TRANSACT),
        must=[Constraint("material", "azure")],
        avoid=[Constraint("fragility", "brittle")],
    )
    search = ToolContract("phase_alpha", "search", (), ())
    plan = SearchPlan.compile(spec, (search,))

    assert plan.required_qualifiers == ("azure",)
    assert plan.forbidden_qualifiers == ("brittle",)
    assert plan.allowed_tool_families == ("search:->none",)


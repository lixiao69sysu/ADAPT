"""Metamorphic tests for schema/topology-driven execution contracts."""

from __future__ import annotations

from agent.decision import Candidate, CandidateLedger
from agent.runtime import CandidateBindingGraph, CallLineageLedger, ToolRegistry


def _schema_type(payload):
    class Schema:
        @staticmethod
        def model_json_schema():
            return payload

    return Schema


def _tool(name, params, returns=None, role=""):
    return type(
        "FictionalTool",
        (),
        {
            "name": name,
            "params": _schema_type(params)(),
            "returns": _schema_type(returns)() if returns else None,
            "info": {"adapt_role": role} if role else {},
        },
    )()


def _candidate_schema(entity, id_field, name_field, parent_field=""):
    properties = {
        id_field: {"type": "string", "x-adapt-role": "id"},
        name_field: {"type": "string", "x-adapt-role": "name"},
    }
    if parent_field:
        properties[parent_field] = {
            "type": "string",
            "x-adapt-role": "parent_id",
        }
    return {
        "type": "array",
        "items": {
            "type": "object",
            "x-adapt-entity": entity,
            "properties": properties,
        },
    }


def _compile_shape(prefix, parent_type, leaf_type):
    search = _tool(
        f"scan_{prefix}",
        {"type": "object", "properties": {}},
        _candidate_schema(parent_type, f"{prefix}_root", f"{prefix}_title"),
        role="search",
    )
    enrich = _tool(
        f"expand_{prefix}",
        {
            "type": "object",
            "required": [f"{prefix}_root"],
            "properties": {
                f"{prefix}_root": {
                    "type": "string",
                    "x-adapt-entity": parent_type,
                }
            },
        },
        _candidate_schema(
            leaf_type,
            f"{prefix}_leaf",
            f"{prefix}_label",
            f"{prefix}_root",
        ),
        role="enrich",
    )
    create = _tool(
        f"forge_{prefix}",
        {
            "type": "object",
            "required": [f"{prefix}_root", f"{prefix}_leaf"],
            "properties": {
                f"{prefix}_root": {
                    "type": "string",
                    "x-adapt-entity": parent_type,
                },
                f"{prefix}_leaf": {
                    "type": "string",
                    "x-adapt-entity": leaf_type,
                },
            },
        },
        role="create",
    )
    registry = ToolRegistry()
    registry.rebuild([search, enrich, create])
    return registry, create.name


def _ledger(parent_id, leaf_id, parent_type, leaf_type, *, reverse=False):
    ledger = CandidateLedger()
    candidates = [
        Candidate(parent_id, parent_type, "root", "root", "scan"),
        Candidate(
            leaf_id,
            leaf_type,
            "leaf",
            "leaf",
            "expand",
            parent_ids=[parent_id],
            inventory=1,
        ),
    ]
    if reverse:
        candidates.reverse()
    ledger.candidates = {item.candidate_id: item for item in candidates}
    return ledger


def test_renamed_fictional_schemas_compile_the_same_binding_topology():
    first_registry, first_create = _compile_shape("nebula", "vault", "glyph")
    second_registry, second_create = _compile_shape("aurora", "nest", "sigil")
    first = first_registry.contract(first_create)
    second = second_registry.contract(second_create)

    assert first is not None and second is not None
    assert first.role == second.role == "create"
    assert len(first.required_id_variables) == len(second.required_id_variables) == 2

    first_graph = CandidateBindingGraph.from_ledger(
        _ledger("V-1", "G-1", "vault", "glyph"), epoch=7
    )
    second_graph = CandidateBindingGraph.from_ledger(
        _ledger("N-1", "S-1", "nest", "sigil"), epoch=7
    )
    first_binding = first_graph.enumerate_bindings(first)
    second_binding = second_graph.enumerate_bindings(second)
    assert len(first_binding) == len(second_binding) == 1
    assert len(first_binding[0].leaf_ids) == len(second_binding[0].leaf_ids) == 1
    assert len(first_binding[0].parent_ids) == len(second_binding[0].parent_ids) == 1


def test_candidate_reordering_cannot_change_structural_bindings():
    registry, create_name = _compile_shape("prism", "cluster", "ray")
    contract = registry.contract(create_name)
    graph_a = CandidateBindingGraph.from_ledger(
        _ledger("C-4", "R-9", "cluster", "ray", reverse=False), epoch=3
    )
    graph_b = CandidateBindingGraph.from_ledger(
        _ledger("C-4", "R-9", "cluster", "ray", reverse=True), epoch=3
    )
    assert graph_a.enumerate_bindings(contract) == graph_b.enumerate_bindings(contract)


def test_same_type_parent_child_keeps_only_the_observed_leaf():
    contract_registry, create_name = _compile_shape("node", "node", "node")
    graph = CandidateBindingGraph.from_ledger(
        _ledger("N-parent", "N-child", "node", "node"), epoch=11
    )
    assert graph.structural_leaf_ids() == ("N-child",)
    bindings = graph.enumerate_bindings(contract_registry.contract(create_name))
    assert bindings
    assert all("N-child" in binding.leaf_ids for binding in bindings)


def test_wrong_parent_pair_is_rejected_without_entity_vocabulary():
    registry, create_name = _compile_shape("orbit", "hub", "spoke")
    contract = registry.contract(create_name)
    ledger = _ledger("H-1", "S-1", "hub", "spoke")
    ledger.candidates["H-2"] = Candidate("H-2", "hub", "other", "", "scan")
    graph = CandidateBindingGraph.from_ledger(ledger, epoch=2)
    errors = graph.validate_arguments(
        contract, {"orbit_root": "H-2", "orbit_leaf": "S-1"}
    )
    assert any("observed edge" in error for error in errors)


def test_lineage_rejects_unknown_and_stale_ids_across_tool_roles():
    lineage = CallLineageLedger()
    lineage.register(
        call_id="search-1",
        instruction_epoch=4,
        tool_epoch=8,
        operation_epoch=2,
        tool_name="scan",
        tool_role="search",
    )
    _, failures = lineage.observe_result(
        call_id="search-1",
        tool_name="scan",
        instruction_epoch=4,
        tool_epoch=8,
        succeeded=True,
        output_candidate_ids=("C-visible",),
    )
    assert failures == ()
    assert lineage.validate_inputs(
        tool_role="enrich",
        input_ids=("C-visible",),
        instruction_epoch=4,
        operation_epoch=2,
    ) == ()
    assert lineage.validate_inputs(
        tool_role="create",
        input_ids=("C-unknown",),
        instruction_epoch=4,
        operation_epoch=2,
    )

    lineage.register(
        call_id="create-1",
        instruction_epoch=4,
        tool_epoch=8,
        operation_epoch=2,
        tool_name="forge",
        tool_role="create",
        input_ids=("C-visible",),
    )
    _, failures = lineage.observe_result(
        call_id="create-1",
        tool_name="forge",
        instruction_epoch=3,
        tool_epoch=8,
        succeeded=True,
        output_state_ids=("W-1",),
    )
    assert "stale instruction epoch" in failures[0]
    assert lineage.validate_inputs(
        tool_role="pay",
        input_ids=("W-1",),
        instruction_epoch=4,
        operation_epoch=2,
    )

    lineage.observe_result(
        call_id="create-1",
        tool_name="forge",
        instruction_epoch=4,
        tool_epoch=8,
        succeeded=True,
        output_state_ids=("W-1",),
    )
    assert lineage.validate_inputs(
        tool_role="pay",
        input_ids=("W-1",),
        instruction_epoch=4,
        operation_epoch=2,
    ) == ()
    assert lineage.validate_inputs(
        tool_role="pay",
        input_ids=("W-1",),
        instruction_epoch=4,
        operation_epoch=3,
    )

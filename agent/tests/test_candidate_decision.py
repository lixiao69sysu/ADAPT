"""Differential and counterfactual tests for CandidateDecision."""

from __future__ import annotations

from agent.decision import Candidate, CandidateLedger, DecisionCard, TaskSpec
from agent.runtime import RuntimePhase, TaskRuntime, ToolRegistry


def _schema_type(payload):
    class Schema:
        @staticmethod
        def model_json_schema():
            return payload

    return Schema


def _create_registry(entity="glyph", argument="glyph_ref"):
    params = {
        "type": "object",
        "required": [argument],
        "properties": {
            argument: {"type": "string", "x-adapt-entity": entity}
        },
    }
    tool = type(
        "CreateTool",
        (),
        {
            "name": "forge_fictional_selection",
            "params": _schema_type(params)(),
            "returns": None,
            "info": {"adapt_role": "create"},
        },
    )()
    registry = ToolRegistry()
    registry.rebuild([tool])
    return registry


def _ledger(*, reverse=False):
    candidates = [
        Candidate(
            "G-blue",
            "glyph",
            "Blue Glyph",
            "name=Blue Glyph, tone=blue",
            "scan",
            inventory=2,
            observed_turn=1,
        ),
        Candidate(
            "G-red",
            "glyph",
            "Red Glyph",
            "name=Red Glyph, tone=red",
            "scan",
            inventory=2,
            observed_turn=2,
        ),
    ]
    if reverse:
        candidates.reverse()
    ledger = CandidateLedger()
    ledger.candidates = {candidate.candidate_id: candidate for candidate in candidates}
    return ledger


def _commit_runtime():
    runtime = TaskRuntime.begin(TaskSpec.compile("请帮我购买一个虚构对象"))
    runtime.authorization.create_authorized = True
    runtime.authorization.candidate_choice_authorized = True
    return runtime


def test_direct_commit_with_admissible_binding_transitions_to_create():
    registry = _create_registry()
    runtime = _commit_runtime()
    decision = registry.candidate_decision(
        _ledger(), DecisionCard(), runtime=runtime, instruction_epoch=9
    )
    assert decision.admissible
    assert decision.selected is not None
    assert decision.next_phase == RuntimePhase.READY_TO_CREATE
    runtime.apply_candidate_decision(decision)
    assert runtime.phase == RuntimePhase.READY_TO_CREATE


def test_soft_preference_changes_order_but_never_admissibility():
    registry = _create_registry()
    ledger = _ledger()
    blue = registry.candidate_decision(
        ledger, DecisionCard(prefer=["blue"]), runtime=_commit_runtime()
    )
    red = registry.candidate_decision(
        ledger, DecisionCard(prefer=["red"]), runtime=_commit_runtime()
    )
    assert {item.leaf_ids for item in blue.admissible} == {
        item.leaf_ids for item in red.admissible
    }
    assert blue.selected.leaf_ids == ("G-blue",)
    assert red.selected.leaf_ids == ("G-red",)


def test_explicit_selection_overrides_soft_order_without_recomputing_family():
    registry = _create_registry()
    runtime = _commit_runtime()
    runtime.selected_candidate_id = "G-red"
    decision = registry.candidate_decision(
        _ledger(), DecisionCard(prefer=["blue"]), runtime=runtime
    )
    assert decision.selected is not None
    assert decision.selected.leaf_ids == ("G-red",)
    assert decision.selection_basis == "explicit_display_selection"


def test_candidate_input_order_does_not_change_decision():
    registry = _create_registry()
    card = DecisionCard(prefer=["blue"])
    first = registry.candidate_decision(
        _ledger(reverse=False), card, runtime=_commit_runtime()
    )
    second = registry.candidate_decision(
        _ledger(reverse=True), card, runtime=_commit_runtime()
    )
    assert first.ordered == second.ordered
    assert first.selected == second.selected


def test_zero_inventory_is_hard_filtered_before_preference_ranking():
    registry = _create_registry()
    ledger = _ledger()
    ledger.candidates["G-blue"].inventory = 0
    decision = registry.candidate_decision(
        ledger, DecisionCard(prefer=["blue"]), runtime=_commit_runtime()
    )
    assert decision.selected is not None
    assert decision.selected.leaf_ids == ("G-red",)
    assert all(item.leaf_ids != ("G-blue",) for item in decision.admissible)

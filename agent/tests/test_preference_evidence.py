"""Regression gates for evidence-aware, schema-driven preferences."""

from __future__ import annotations

from agent.adapt_agent import ADAPTAgent
from agent.decision import (
    Candidate,
    CandidateLedger,
    Constraint,
    ConstraintOperator,
    ConstraintTarget,
    DecisionCard,
)
from agent.memory.adapt_memory import ADAPTMemory
from agent.memory.evidence import PreferenceEvidenceStore
from agent.memory.fact_store import FactStore
from agent.memory.facts import PreferenceFact
from agent.runtime.contracts import ToolContractCompiler
from agent.runtime.tools import ToolMeta, ToolRole
from vita.data_model.message import ToolCall


def _fact(
    value: str,
    *,
    dimension: str = "attribute",
    polarity: str = "positive",
    condition: str = "",
) -> PreferenceFact:
    return PreferenceFact(
        fact_id=f"fact-{value}-{condition}",
        scope="delivery",
        facet="beverage",
        dimension=dimension,
        value=value,
        polarity=polarity,
        confidence=0.75,
        observed_at="2026-08-01",
        source_type="order",
        category="drink",
        condition_signature=condition,
    )


def test_live_task_statement_is_not_durable_without_future_scope():
    memory = ADAPTMemory(language="chinese")

    assert memory.record_runtime_preference("这次不要加香菜", persistent=False) == 0
    assert not list(memory.facts)

    added = memory.record_runtime_preference("以后都不要加香菜", persistent=True)
    assert added >= 1
    fact = next(fact for fact in memory.facts if fact.polarity == "negative")
    assert fact.persistence == "persistent"
    assert fact.source_kind == "direct_user"
    assert fact.independent_evidence_count == 1


def test_evidence_projection_keeps_repeated_memory_reads_pure():
    memory = ADAPTMemory(language="chinese")
    memory._ingest_fact(_fact("深烘", dimension="roast"))
    before = [
        (fact.fact_id, fact.confidence, fact.belief_confidence, fact.status)
        for fact in memory.facts
    ]
    outputs = [memory.read("推荐一杯饮品") for _ in range(3)]
    after = [
        (fact.fact_id, fact.confidence, fact.belief_confidence, fact.status)
        for fact in memory.facts
    ]

    assert outputs[0] == outputs[1] == outputs[2]
    assert before == after


def test_conditional_values_do_not_supersede_across_conditions():
    store = FactStore()
    winter = _fact("热饮", dimension="temperature", condition="冬天")
    summer = _fact("冰饮", dimension="temperature", condition="夏天")

    store.ingest(winter, confirmed_drift=True)
    store.ingest(summer, confirmed_drift=True)

    assert {fact.value for fact in store.active()} == {"热饮", "冰饮"}


def test_confidence_uses_independent_observable_evidence_not_event_ids():
    evidence = PreferenceEvidenceStore()
    order = _fact("深烘", dimension="roast")
    first = evidence.observe_fact(order, evidence_id="event-a")
    replay = evidence.observe_fact(order, evidence_id="event-b")
    review = _fact("深烘", dimension="roast")
    review.source_type = "review"
    corroborated = evidence.observe_fact(review, evidence_id="event-c")

    assert replay.evidence_count == 1
    assert corroborated.evidence_count == 2
    assert corroborated.source_diversity == 2
    assert corroborated.confidence > first.confidence


def test_candidate_edges_are_candidate_specific_and_bounded():
    memory = ADAPTMemory(language="chinese")
    memory._ingest_fact(_fact("冷萃"))
    memory._ingest_fact(_fact("燕麦", dimension="milk"))
    candidates = [
        Candidate(
            "n-1", "glyph", "Alpha", "tone=冷萃, base=燕麦", "scan",
            attributes={"tone": "冷萃", "base": "燕麦"},
        ),
        Candidate(
            "n-2", "glyph", "Beta", "tone=热饮, base=全脂", "scan",
            attributes={"tone": "热饮", "base": "全脂"},
        ),
    ]
    card = memory.compile_task("帮我找一杯饮品")
    stats = memory.apply_candidate_grounding(card, candidates)

    assert stats["grounded_edges"] >= 2
    assert len(card.preference_pool) <= 8
    assert card.candidate_preference_scores["n-1"] > 0
    assert card.candidate_preference_scores.get("n-2", 0.0) == 0.0


def test_schema_request_capability_transmits_intent_but_not_fulfillment():
    meta = ToolMeta(
        "forge_commit",
        ToolRole.CREATE,
        {"relic_id"},
        {"relic_id": "relic"},
        argument_schemas={
            "relic_id": {"type": "string"},
            "special_instructions": {
                "type": "string",
                "description": "free-form customer note for the provider",
            },
        },
    )
    contract = ToolContractCompiler.compile([meta])["forge_commit"]
    assert contract.action_capability.can_transmit_request
    assert contract.action_capability.mode == "requested"

    card = DecisionCard(
        constraints=[
            Constraint(
                "avoid", "oxide", ConstraintTarget.CANDIDATE,
                ConstraintOperator.EXCLUDES, hard=True,
            )
        ]
    )
    agent = object.__new__(ADAPTAgent)
    agent.decision_card = card
    request = agent._bind_transmittable_preference_request(
        ToolCall(id="call-1", name="forge_commit", arguments={"relic_id": "r-1"}),
        contract,
    )
    assert "oxide" in request.arguments["special_instructions"]

    # A request field does not make an intrinsically conflicting candidate
    # admissible; it only carries the user's wording to a capable provider.
    ledger = CandidateLedger()
    ledger.candidates["r-1"] = Candidate(
        "r-1", "relic", "Oxide Relic", "material=oxide", "scan"
    )
    errors = ledger.validate_write(
        "forge_commit", request.arguments, card, tool_meta=meta
    )
    assert any("forbidden value" in error for error in errors)

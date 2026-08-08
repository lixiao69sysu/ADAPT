"""Whitelisted strategy mutations proposed from deterministic diagnoses."""

from __future__ import annotations

from copy import deepcopy

from agent.evolution.models import BadCase, CandidateStrategy


_CATALOG = {
    "memory_retrieval": [
        CandidateStrategy(
            strategy_id="retrieval.product_type_expansion.v2",
            diagnosis_code="memory_retrieval",
            mutation_kind="config",
            component="memory.retrieval",
            changes={"product_type_expansions": {"拖鞋": ["人字拖"]}},
            rationale="apply the successful product subtype bridge without global token filtering",
            risk="the subtype can be stale when a newer preference has drifted",
        ),
        CandidateStrategy(
            strategy_id="retrieval.query_normalization.v1",
            diagnosis_code="memory_retrieval",
            mutation_kind="config",
            component="memory.retrieval",
            changes={
                "product_type_expansions": {"拖鞋": ["人字拖"]},
                "ignore_generic_bigrams": True,
            },
            rationale="bridge broad product terms to stable historical subtypes",
            risk="an expansion can over-constrain a user whose preference has drifted",
        ),
        CandidateStrategy(
            strategy_id="retrieval.structured_signal_priority.v1",
            diagnosis_code="memory_retrieval",
            mutation_kind="config",
            component="memory.retrieval",
            changes={
                "structured_signal_boost": {
                    "order": 0.20,
                    "cart": 0.12,
                    "search": 0.08,
                }
            },
            rationale="rank explicit consumption evidence ahead of generic conversations",
            risk="old purchases can dominate a newer conversational preference",
        ),
        CandidateStrategy(
            strategy_id="retrieval.simulation_time_recency.v1",
            diagnosis_code="memory_retrieval",
            mutation_kind="policy",
            component="memory.retrieval",
            changes={"recency_reference": "latest_observed_event_time"},
            rationale="use the task timeline rather than host wall-clock time",
            risk="malformed future timestamps can flatten recency",
        ),
        CandidateStrategy(
            strategy_id="retrieval.query_centered_raw_page.v1",
            diagnosis_code="memory_retrieval",
            mutation_kind="policy",
            component="memory.context_paging",
            changes={"raw_excerpt": "query_centered", "max_chars": 220},
            rationale="keep the matched preference visible instead of truncating the prefix",
            risk="longer excerpts increase prompt size",
        ),
    ],
    "repeated_tool_call": [
        CandidateStrategy(
            strategy_id="harness.search_stage_progression.v1",
            diagnosis_code="repeated_tool_call",
            mutation_kind="policy",
            component="harness.progress_guard",
            changes={
                "repeat_limit": 2,
                "on_stall": "disable_search_family_and_require_next_stage",
            },
            rationale="advance from lookup to candidate selection instead of only rejecting",
            risk="a weak initial result page can leave no acceptable candidate",
        )
    ],
    "tool_execution": [
        CandidateStrategy(
            strategy_id="harness.failed_write_recovery.v1",
            diagnosis_code="tool_execution",
            mutation_kind="policy",
            component="harness.progress_guard",
            changes={"failed_write": "refresh_public_ids_then_retry_once"},
            rationale="recover from stale public identifiers without guessing new IDs",
            risk="one retry adds latency and must remain idempotent",
        )
    ],
    "proactive_decision": [
        CandidateStrategy(
            strategy_id="harness.proactive_question_delivery.v1",
            diagnosis_code="proactive_decision",
            mutation_kind="policy",
            component="harness.agent",
            changes={"suggested_question": "emit_to_user_or_use_memory"},
            rationale="prevent a generated clarification from being silently ignored",
            risk="unnecessary questions can reduce task completion",
        )
    ],
    "cross_entity_mismatch": [
        CandidateStrategy(
            strategy_id="harness.cross_entity_soft_warning.v1",
            diagnosis_code="cross_entity_mismatch",
            mutation_kind="policy",
            component="harness.tool_guard",
            changes={"cross_entity_mismatch": "warning"},
            rationale="retain provenance diagnostics without blocking valid combinations",
            risk="a truly incompatible combination reaches the public tool for validation",
        )
    ],
}


def propose_candidates(cases: list[BadCase]) -> list[CandidateStrategy]:
    """Create deduplicated candidates from a fixed, auditable mutation catalog."""
    candidates: dict[str, CandidateStrategy] = {}
    for case in cases:
        for diagnosis in case.diagnoses:
            for template in _CATALOG.get(diagnosis.code, []):
                candidate = candidates.setdefault(
                    template.strategy_id, deepcopy(template)
                )
                if case.case_id not in candidate.supporting_case_ids:
                    candidate.supporting_case_ids.append(case.case_id)
    return sorted(candidates.values(), key=lambda item: item.strategy_id)

"""Candidate-level attribution and shadow ranking comparison.

All fields are derived from the current instruction, observable candidates,
candidate grounding edges, and deterministic execution contracts.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from agent.runtime.grounding import TaskRelevanceMatrix
from agent.runtime.ranking import (
    CandidateRanker,
    RankingPolicy,
    layered_ranking_key,
)


_REQUEST_ONLY_SOURCES = {"request", "note", "transmitted_request"}


@dataclass(frozen=True)
class CandidateAttributionRecord:
    policy: str
    candidate_id: str
    entity_type: str
    name: str
    structural_family: str
    rank: int
    top3: bool
    admissible: bool
    selected: bool
    task_relevance_band: int
    task_relevance_score: float
    task_matched_features: tuple[str, ...]
    session_correction_score: float
    historical_preference_score: float
    historical_parent_preference_score: float
    grounding_reliability: float
    grounding_sources: tuple[str, ...]
    intrinsic_grounding_count: int
    request_only_grounding_count: int
    availability: int
    ranking_key: tuple[float | int, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CandidateAttributionSummary:
    policy: str
    candidate_count: int
    admissible_count: int
    no_executable_candidate: bool
    top3_non_task_category_count: int
    top3_count: int
    historical_preference_outrank_count: int
    request_only_grounding_confusions: int
    top3_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CandidateAttributionBatch:
    records: tuple[CandidateAttributionRecord, ...]
    summary: CandidateAttributionSummary


class CandidateAttributionEngine:
    """Build comparable records without changing the production decision."""

    @staticmethod
    def build(
        candidates: list[Any],
        card: Any,
        *,
        decision: Any | None = None,
        policy: RankingPolicy | str = RankingPolicy.CURRENT,
    ) -> CandidateAttributionBatch:
        policy = RankingPolicy(policy)
        ranker = CandidateRanker()
        scores = ranker.score_candidates(candidates, card)
        relevance = TaskRelevanceMatrix(
            " ".join(getattr(card, "task_intent", ()) or ()), candidates
        )
        ranked = ranker.rank(candidates, card, max(1, len(candidates)), policy=policy)
        admissible_ids = {
            candidate_id
            for binding in getattr(decision, "admissible", ())
            for candidate_id in binding.leaf_ids
        }
        selected_ids = set(getattr(getattr(decision, "selected", None), "leaf_ids", ()))
        sources_by_id = getattr(card, "candidate_grounding_sources", {})
        edge_counts = getattr(card, "candidate_grounding_edge_counts", {})
        records: list[CandidateAttributionRecord] = []
        for rank, candidate in enumerate(ranked, 1):
            candidate_id = str(candidate.candidate_id)
            score = scores[candidate_id]
            task = relevance.row(candidate)
            sources = tuple(sources_by_id.get(candidate_id, ()))
            request_only = sum(source in _REQUEST_ONLY_SOURCES for source in sources)
            intrinsic_count = max(0, int(edge_counts.get(candidate_id, 0)) - request_only)
            records.append(
                CandidateAttributionRecord(
                    policy=policy.value,
                    candidate_id=candidate_id,
                    entity_type=str(getattr(candidate, "entity_type", "unknown")),
                    name=str(getattr(candidate, "name", "")),
                    structural_family=task.family,
                    rank=rank,
                    top3=rank <= 3,
                    admissible=(candidate_id in admissible_ids if decision is not None else True),
                    selected=candidate_id in selected_ids,
                    task_relevance_band=task.band,
                    task_relevance_score=task.score,
                    task_matched_features=task.matched_features,
                    session_correction_score=score.session_correction,
                    historical_preference_score=score.historical_preference,
                    historical_parent_preference_score=score.historical_parent_preference,
                    grounding_reliability=score.grounding_reliability,
                    grounding_sources=sources,
                    intrinsic_grounding_count=intrinsic_count,
                    request_only_grounding_count=request_only,
                    availability=score.availability,
                    ranking_key=layered_ranking_key(score, candidate, policy).values,
                )
            )
        outranks = 0
        for index, higher in enumerate(records):
            for lower in records[index + 1 :]:
                higher_task = (
                    higher.task_relevance_band,
                    higher.task_relevance_score,
                )
                lower_task = (lower.task_relevance_band, lower.task_relevance_score)
                higher_preference = (
                    higher.historical_preference_score
                    + higher.historical_parent_preference_score
                )
                lower_preference = (
                    lower.historical_preference_score
                    + lower.historical_parent_preference_score
                )
                if higher_task < lower_task and higher_preference > lower_preference:
                    outranks += 1
        top3 = records[:3]
        summary = CandidateAttributionSummary(
            policy=policy.value,
            candidate_count=len(records),
            admissible_count=(len(admissible_ids) if decision is not None else len(records)),
            no_executable_candidate=(
                not bool(getattr(decision, "ordered", ())) if decision is not None else not records
            ),
            top3_non_task_category_count=sum(
                record.task_relevance_band == 0 for record in top3
            ),
            top3_count=len(top3),
            historical_preference_outrank_count=outranks,
            request_only_grounding_confusions=sum(
                record.request_only_grounding_count > 0
                and record.intrinsic_grounding_count == 0
                for record in records
            ),
            top3_ids=tuple(record.candidate_id for record in top3),
        )
        return CandidateAttributionBatch(tuple(records), summary)

    @classmethod
    def compare(
        cls, candidates: list[Any], card: Any, *, decision: Any | None = None
    ) -> dict[str, CandidateAttributionBatch]:
        return {
            policy.value: cls.build(
                candidates, card, decision=decision, policy=policy
            )
            for policy in RankingPolicy
        }

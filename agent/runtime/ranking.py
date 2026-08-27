"""Deterministic ranking for the bounded candidate shortlist."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from agent.runtime.alignment import EvidenceAlignment
from agent.runtime.grounding import TaskRelevanceMatrix


@dataclass(frozen=True)
class CandidateScore:
    task_relevance_band: int
    task_relevance: float
    session_correction: float
    historical_preference: float
    historical_parent_preference: float
    grounding_reliability: float
    availability: int
    stable_key: int


class RankingPolicy(str, Enum):
    CURRENT = "current"
    CURRENT_TASK_ONLY = "current_task_only"
    TASK_FIRST_PREFERENCE_TIEBREAK = "task_first_preference_tiebreak"


PRODUCTION_RANKING_POLICY = RankingPolicy.TASK_FIRST_PREFERENCE_TIEBREAK


@dataclass(frozen=True)
class CandidateRankingKey:
    """Auditable lexicographic key for one shadow or production policy."""

    policy: RankingPolicy
    values: tuple[float | int, ...]


def layered_ranking_key(
    score: CandidateScore,
    candidate: Any,
    policy: RankingPolicy | str = RankingPolicy.CURRENT,
) -> CandidateRankingKey:
    policy = RankingPolicy(policy)
    price = -(getattr(candidate, "price", None) or 0)
    if policy == RankingPolicy.CURRENT_TASK_ONLY:
        values = (
            score.task_relevance_band,
            score.task_relevance,
            score.availability,
            price,
            score.stable_key,
        )
    elif policy == RankingPolicy.TASK_FIRST_PREFERENCE_TIEBREAK:
        values = (
            score.task_relevance_band,
            score.task_relevance,
            score.availability,
            score.grounding_reliability,
            score.session_correction,
            score.historical_preference,
            score.historical_parent_preference,
            price,
            score.stable_key,
        )
    else:
        # Bit-for-bit ordering compatibility with the previous production key.
        values = (
            score.task_relevance_band,
            score.task_relevance,
            score.session_correction,
            score.historical_preference,
            score.historical_parent_preference,
            score.availability,
            price,
            score.stable_key,
        )
    return CandidateRankingKey(policy, values)


class CandidateRanker:
    """Hard-filter exclusions, then score evidence rather than model prose."""

    @staticmethod
    def preference_alignment(candidates: list[Any], card: Any) -> EvidenceAlignment:
        sources = (
            card.alignment_source_records()
            if hasattr(card, "alignment_source_records")
            else getattr(card, "prefer", [])
        )
        return EvidenceAlignment(candidates, sources, _preference_matches)

    @classmethod
    def has_reliable_task_grounding(
        cls, candidates: list[Any], card: Any
    ) -> bool:
        """Whether live candidates ground an entity requested in this task."""
        if not candidates:
            return False
        instruction = " ".join(getattr(card, "task_intent", ()) or ())
        matrix = TaskRelevanceMatrix(instruction, candidates)
        return matrix.reliable_grounding or matrix.single_structural_family

    @classmethod
    def score_candidates(
        cls, candidates: list[Any], card: Any
    ) -> dict[str, CandidateScore]:
        instruction = " ".join(getattr(card, "task_intent", ()) or ())
        relevance = TaskRelevanceMatrix(instruction, candidates)
        sources = (
            card.alignment_source_records()
            if hasattr(card, "alignment_source_records")
            else getattr(card, "prefer", [])
        )
        session_sources = [
            source
            for source in sources
            if isinstance(source, (tuple, list))
            and len(source) > 4
            and any(
                marker in str(source[4])
                for marker in ("current_user_answer", "user_correction")
            )
        ]
        session_alignment = EvidenceAlignment(
            candidates, session_sources, _preference_matches
        )
        historical_sources = [
            source
            for source in sources
            if not (
                isinstance(source, (tuple, list))
                and len(source) > 4
                and any(
                    marker in str(source[4])
                    for marker in (
                        "current_instruction",
                        "current_user_answer",
                        "user_correction",
                    )
                )
            )
        ]
        historical_alignment = EvidenceAlignment(
            candidates, historical_sources, _preference_matches
        )
        parent_identity_keys = {"store_name", "shop_name", "merchant_name"}
        allow_historical = (
            relevance.reliable_grounding or relevance.single_structural_family
        )
        scores: dict[str, CandidateScore] = {}
        for index, candidate in enumerate(candidates):
            row = relevance.row(candidate)
            candidate_id = str(candidate.candidate_id)
            if getattr(card, "grounded_edge_count", 0):
                # Explicit edges replace global text re-matching after SEARCH.
                # The existing task-grounding gate still prevents historical
                # preferences from choosing an ungrounded candidate family.
                historical = (
                    float(getattr(card, "candidate_preference_scores", {}).get(candidate_id, 0.0))
                    if allow_historical or row.band == 2
                    else 0.0
                )
                historical_parent = (
                    float(getattr(card, "candidate_parent_preference_scores", {}).get(candidate_id, 0.0))
                    if allow_historical or row.band == 2
                    else 0.0
                )
            else:
                historical_matches = (
                    historical_alignment.matches(candidate)
                    if allow_historical or row.band == 2
                    else ()
                )
                historical = sum(
                    atom.weight
                    for atom in historical_matches
                    if atom.attribute_key not in parent_identity_keys
                )
                historical_parent = sum(
                    atom.weight
                    for atom in historical_matches
                    if atom.attribute_key in parent_identity_keys
                )
            scores[candidate_id] = CandidateScore(
                task_relevance_band=row.band,
                task_relevance=row.score,
                session_correction=session_alignment.score(candidate),
                historical_preference=historical,
                historical_parent_preference=historical_parent,
                grounding_reliability=float(
                    getattr(card, "candidate_grounding_confidence", {}).get(
                        candidate_id, 0.0
                    )
                ),
                availability=(1 if candidate.inventory is None or candidate.inventory > 0 else 0),
                # Preserve the environment's observed order as the final
                # value-agnostic tie break. Concrete ID spelling must not
                # decide between otherwise equivalent candidates.
                stable_key=len(candidates) - index,
            )
        return scores

    @classmethod
    def preference_match_count(
        cls, candidate: Any, card: Any, candidates: list[Any] | None = None
    ) -> int:
        alignment = cls.preference_alignment(candidates or [candidate], card)
        return alignment.coverage(candidate)

    @classmethod
    def preference_match_counts(
        cls, candidates: list[Any], card: Any
    ) -> dict[str, int]:
        alignment = cls.preference_alignment(candidates, card)
        return {
            str(candidate.candidate_id): alignment.coverage(candidate)
            for candidate in candidates
        }

    @classmethod
    def preference_scores(
        cls, candidates: list[Any], card: Any
    ) -> dict[str, float]:
        alignment = cls.preference_alignment(candidates, card)
        return {
            str(candidate.candidate_id): alignment.score(candidate)
            for candidate in candidates
        }

    @classmethod
    def decisive_preference_scores(
        cls, candidates: list[Any], card: Any
    ) -> dict[str, float]:
        alignment = cls.preference_alignment(candidates, card)
        return {
            str(candidate.candidate_id): alignment.decisive_score(candidate)
            for candidate in candidates
        }

    def rank(
        self,
        candidates: list[Any],
        card: Any,
        limit: int = 5,
        *,
        policy: RankingPolicy | str = PRODUCTION_RANKING_POLICY,
    ) -> list[Any]:
        policy = RankingPolicy(policy)
        score_by_id = self.score_candidates(candidates, card)
        constraints = [
            constraint
            for constraint in getattr(card, "constraints", [])
            if getattr(getattr(constraint, "target", None), "value", "candidate")
            == "candidate"
        ]
        scored: list[tuple[Any, ...]] = []
        for candidate in candidates:
            raw = candidate.raw or ""
            if _strong_preference_conflict(raw, getattr(card, "prefer", [])):
                continue
            required = [
                constraint
                for constraint in constraints
                if getattr(getattr(constraint, "operator", None), "value", "")
                != "excludes"
                and getattr(constraint, "hard", True)
                and constraint.value
            ]
            if any(
                not _hard_constraint_matches(constraint.value, raw, candidate)
                for constraint in required
            ):
                continue
            excluded = any(
                getattr(getattr(constraint, "operator", None), "value", "")
                == "excludes"
                and constraint.value
                and violates_exclusion(
                    raw, constraint.value, getattr(card, "prefer", [])
                )
                for constraint in constraints
            )
            if excluded or candidate.inventory == 0:
                continue
            score = score_by_id[str(candidate.candidate_id)]
            key = layered_ranking_key(score, candidate, policy)
            scored.append((*key.values, candidate))
        scored.sort(key=lambda item: item[:-1], reverse=True)
        return [item[-1] for item in scored[:limit]]


def _preference_matches(value: str, raw: str) -> bool:
    """Normalize preference phrases for ranking without resolving any ID."""
    preference = (value or "").strip()
    if not preference:
        return False
    if preference in raw:
        return True
    normalized = preference
    for prefix in ("喜欢", "偏好", "常选", "常点", "经常选择"):
        normalized = normalized.removeprefix(prefix)
    roots = {normalized}
    for separator in ("（", "(", "酒店", "宾馆", "店"):
        if separator in normalized:
            roots.add(normalized.split(separator, 1)[0])
    if any(len(root.strip()) >= 2 and root.strip() in raw for root in roots):
        return True
    brand = re.match(r"([A-Za-z][A-Za-z0-9&.'-]{1,20})", normalized)
    if brand and brand.group(1).lower() in raw.lower():
        return True
    return False


def _hard_constraint_matches(value: str, raw: str, candidate: Any = None) -> bool:
    return value in raw


def _contains_forbidden(raw: str, value: str) -> bool:
    if value not in raw:
        return False
    if value == "小料":
        field = re.search(r"小料\s*[:：=]\s*([^,，)\]]+)", raw)
        if field and field.group(1).strip() in {"无", "不加", "不要", "无小料", "不加小料"}:
            return False
    remainder = raw
    for phrase in (f"无{value}", f"不加{value}", f"不含{value}", f"去{value}"):
        remainder = remainder.replace(phrase, "")
    return value in remainder


def violates_exclusion(raw: str, value: str, preferences: list[str]) -> bool:
    """Canonical exclusion check shared by ranking and WRITE validation."""
    return _contains_forbidden(raw, value)


def _strong_preference_conflict(raw: str, preferences: list[str]) -> bool:
    """Soft preferences never become an implicit hard exclusion."""
    return False

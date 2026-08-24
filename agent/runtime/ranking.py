"""Deterministic ranking for the bounded candidate shortlist."""

from __future__ import annotations

import re
from typing import Any

from agent.runtime.alignment import EvidenceAlignment


class CandidateRanker:
    """Hard-filter exclusions, then score evidence rather than model prose."""

    @staticmethod
    def preference_alignment(candidates: list[Any], card: Any) -> EvidenceAlignment:
        sources = (
            card.alignment_source_records()
            if hasattr(card, "alignment_source_records")
            else getattr(card, "prefer", [])
        )
        return EvidenceAlignment(
            candidates, sources, _preference_matches
        )

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

    def rank(self, candidates: list[Any], card: Any, limit: int = 5) -> list[Any]:
        alignment = self.preference_alignment(candidates, card)
        constraints = [
            constraint
            for constraint in getattr(card, "constraints", [])
            if getattr(getattr(constraint, "target", None), "value", "candidate")
            == "candidate"
        ]
        scored: list[tuple[float, int, float, Any]] = []
        has_groundable_identity = any(
            getattr(constraint, "kind", "") == "entity"
            and constraint.value
            and any(
                _hard_constraint_matches(constraint.value, candidate.raw, candidate)
                for candidate in candidates
            )
            for constraint in constraints
        )
        task_identity = (
            None if has_groundable_identity else alignment.task_identity_atom()
        )
        for candidate in candidates:
            raw = candidate.raw or ""
            if not alignment.satisfies_task_identity(candidate, task_identity):
                continue
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
            hard_matches = sum(
                1
                for constraint in constraints
                if getattr(getattr(constraint, "operator", None), "value", "")
                != "excludes"
                and constraint.value
                and _hard_constraint_matches(constraint.value, raw, candidate)
            )
            preference_matches = alignment.score(candidate)
            exact_preference_matches = sum(
                1
                for value in getattr(card, "prefer", [])
                if value and value in raw
            )
            # Exact evidence breaks a tie while candidate-induced semantic
            # matches still dominate presentation fields such as price.
            score = (
                hard_matches * 5.0
                + preference_matches
                + exact_preference_matches * 0.25
            )
            if candidate.inventory is not None and candidate.inventory > 0:
                score += 0.5
            scored.append(
                (score, candidate.observed_turn, -(candidate.price or 0), candidate)
            )
        scored.sort(key=lambda item: item[:3], reverse=True)
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

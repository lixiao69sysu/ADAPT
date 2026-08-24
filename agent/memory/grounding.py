"""Candidate-induced second-stage retrieval over the complete fact store."""

from __future__ import annotations

from collections.abc import Iterable

from agent.memory.facts import PreferenceFact
from agent.runtime.alignment import CandidateAttributeMap, _normalize, _usable


def ground_facts_to_candidates(
    facts: Iterable[PreferenceFact], candidates: Iterable[object]
) -> list[PreferenceFact]:
    """Return active facts that ground to fields observed on current candidates.

    The candidate schema supplies the vocabulary. No domain lexicon, evaluator
    label, target marker, user ID, or candidate ID is used for retrieval.
    """
    attributes: dict[tuple[str, str], str] = {}
    for candidate in candidates:
        for attribute in CandidateAttributeMap.from_candidate(candidate).attributes:
            normalized = _normalize(attribute.value)
            if _usable(attribute.value):
                attributes.setdefault((attribute.key, normalized), attribute.value)
    if not attributes:
        return []

    grounded: list[tuple[int, int, float, str, PreferenceFact]] = []
    for fact in facts:
        if fact.status != "active" or not (fact.value or "").strip():
            continue
        preference = _normalize(fact.value)
        if not _usable(fact.value):
            continue
        matched_keys: set[str] = set()
        longest = 0
        for (key, observed), _raw_value in attributes.items():
            if (
                observed == preference
                or observed in preference
                or (len(preference) >= 2 and observed.startswith(preference))
            ):
                matched_keys.add(key)
                longest = max(longest, min(len(observed), len(preference)))
        if matched_keys:
            grounded.append(
                (
                    len(matched_keys),
                    longest,
                    fact.confidence,
                    fact.observed_at,
                    fact,
                )
            )
    grounded.sort(key=lambda item: item[:-1], reverse=True)
    return [item[-1] for item in grounded]


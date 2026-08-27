"""Candidate-induced preference grounding over observable result fields.

Facts are never globally mapped to an entity class.  Instead each retrieval
round creates explicit fact-to-candidate edges from the current tool output.
That makes grounding auditable and prevents a historic entity token from
choosing the current task's category before live evidence exists.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from hashlib import sha1

from agent.memory.facts import PreferenceFact
from agent.runtime.alignment import CandidateAttributeMap, _normalize, _usable


_SOURCE_PRECISION = {
    "dynamic_field": 1.0,
    "field": 0.96,
    "tag": 0.82,
    "name": 0.68,
    "open": 0.48,
}


@dataclass(frozen=True)
class CandidateEvidenceEdge:
    """One fact grounded to one current candidate attribute.

    ``grounding_confidence`` measures only the observable mapping precision;
    it is separate from the belief confidence of the underlying fact.  Neither
    field can name an evaluator target or an out-of-ledger entity.
    """

    fact: PreferenceFact
    candidate_id: str
    entity_type: str
    attribute_key: str
    attribute_value: str
    attribute_source: str
    match_kind: str
    grounding_confidence: float

    @property
    def weight(self) -> float:
        return round(self.fact.confidence * self.grounding_confidence, 4)

    def projected_fact(self) -> PreferenceFact:
        """Project an actionable negative only onto observed live values."""
        if self.fact.polarity != "negative":
            return self.fact
        projection_id = sha1(
            f"{self.fact.fact_id}|{self.candidate_id}|{self.attribute_key}|{self.attribute_value}".encode("utf-8")
        ).hexdigest()[:16]
        return replace(
            self.fact,
            fact_id=projection_id,
            value=self.attribute_value,
            category=self.attribute_value,
            evidence_ids=list(self.fact.evidence_ids),
            evidence_types=list(self.fact.evidence_types),
        )


def candidate_evidence_edges(
    facts: Iterable[PreferenceFact],
    candidates: Iterable[object],
    *,
    task_text: str = "",
) -> list[CandidateEvidenceEdge]:
    """Return explicit preference-to-current-candidate evidence edges.

    Exact field matches are strong.  Prefix/substring matches are allowed only
    for a value at least two characters long and are deliberately downgraded.
    The same task-identity suppression used by the previous projection is
    preserved for ordinary negative preferences.
    """
    candidate_list = list(candidates)
    maps = {
        str(getattr(candidate, "candidate_id", "")): CandidateAttributeMap.from_candidate(candidate)
        for candidate in candidate_list
    }
    entity_counts: dict[str, int] = {}
    attribute_coverage: dict[tuple[str, str, str], set[str]] = {}
    for candidate in candidate_list:
        candidate_id = str(getattr(candidate, "candidate_id", ""))
        entity_type = str(getattr(candidate, "entity_type", "unknown"))
        entity_counts[entity_type] = entity_counts.get(entity_type, 0) + 1
        for attribute in maps[candidate_id].attributes:
            normalized = _normalize(attribute.value)
            if _usable(attribute.value):
                attribute_coverage.setdefault(
                    (entity_type, attribute.key, normalized), set()
                ).add(candidate_id)
    normalized_task = _normalize(task_text)
    edges: dict[tuple[str, str, str, str], CandidateEvidenceEdge] = {}
    for fact in facts:
        if fact.status != "active" or not _usable(fact.value):
            continue
        preference = _normalize(fact.value)
        if not preference:
            continue
        for candidate in candidate_list:
            candidate_id = str(getattr(candidate, "candidate_id", ""))
            entity_type = str(getattr(candidate, "entity_type", "unknown"))
            for attribute in maps[candidate_id].attributes:
                observed = _normalize(attribute.value)
                if not observed:
                    continue
                if observed == preference:
                    kind = "exact"
                    match_factor = 1.0
                elif len(preference) >= 2 and (
                    observed.startswith(preference)
                    or preference in observed
                    or (len(observed) >= 2 and observed in preference)
                ):
                    kind = "partial"
                    match_factor = 0.55
                else:
                    continue
                if fact.polarity == "negative" and fact.dimension != "safety":
                    # Do not turn a broad current task identity into a
                    # rejection of every candidate in that family.
                    covered = attribute_coverage.get(
                        (entity_type, attribute.key, observed), set()
                    )
                    if (
                        observed in normalized_task
                        and entity_counts.get(entity_type, 0) >= 2
                        and len(covered) / entity_counts[entity_type] >= 0.8
                    ):
                        continue
                confidence = _SOURCE_PRECISION.get(attribute.source, 0.45) * match_factor
                edge = CandidateEvidenceEdge(
                    fact=fact,
                    candidate_id=candidate_id,
                    entity_type=entity_type,
                    attribute_key=attribute.key,
                    attribute_value=attribute.value,
                    attribute_source=attribute.source,
                    match_kind=kind,
                    grounding_confidence=round(confidence, 4),
                )
                key = (fact.fact_id, candidate_id, attribute.key, observed)
                previous = edges.get(key)
                if previous is None or edge.weight > previous.weight:
                    edges[key] = edge
    return sorted(
        edges.values(),
        key=lambda edge: (
            edge.fact.polarity != "negative",
            edge.weight,
            edge.fact.observed_at,
            edge.candidate_id,
            edge.attribute_key,
        ),
        reverse=True,
    )


def ground_facts_to_candidates(
    facts: Iterable[PreferenceFact],
    candidates: Iterable[object],
    *,
    task_text: str = "",
) -> list[PreferenceFact]:
    """Backward-compatible fact projection derived from explicit edges."""
    selected: dict[tuple[str, str, str], tuple[float, PreferenceFact]] = {}
    for edge in candidate_evidence_edges(facts, candidates, task_text=task_text):
        fact = edge.projected_fact()
        key = (fact.fact_id, fact.value, fact.polarity)
        previous = selected.get(key)
        if previous is None or edge.weight > previous[0]:
            selected[key] = (edge.weight, fact)
    return [
        fact
        for _weight, fact in sorted(
            selected.values(),
            key=lambda item: (item[0], item[1].observed_at, item[1].value),
            reverse=True,
        )
    ]

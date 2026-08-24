"""Bounded, evidence-preserving index for product/brand/entity history."""

from __future__ import annotations

import re
from collections import defaultdict, deque
from collections.abc import Iterator, Sequence
from dataclasses import replace

from agent.memory.fact_store import FactStore
from agent.memory.facts import PreferenceFact


ENTITY_DIMENSIONS = {"product", "brand", "searches", "like"}
SOURCE_STRENGTH = {
    "favorite": 7,
    "conversation": 6,
    "order": 5,
    "opinion": 4,
    "add_to_cart": 3,
    "high_freq_browse": 2,
    "browse": 1,
    "search": 1,
}
_NORMALIZE_RE = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff]+")


def normalize_entity(value: str) -> str:
    return _NORMALIZE_RE.sub("", (value or "").casefold())


def entity_fact_key(
    fact: PreferenceFact,
) -> tuple[str, str, str, str, str, str]:
    return (
        fact.scope,
        fact.facet,
        fact.dimension,
        fact.category,
        fact.polarity,
        normalize_entity(fact.value),
    )


def entity_strength(fact: PreferenceFact) -> tuple[int, int, int, float, str]:
    evidence_types = set(fact.evidence_types or [fact.source_type])
    return (
        int(fact.decision_eligible),
        len(evidence_types),
        max((SOURCE_STRENGTH.get(source, 0) for source in evidence_types), default=0),
        fact.confidence,
        fact.observed_at,
    )


def aggregate_entity_facts(
    facts: Sequence[PreferenceFact] | Iterator[PreferenceFact],
) -> list[PreferenceFact]:
    """Merge exact-normalized evidence only inside the same typed entity slot."""
    merged: dict[tuple[str, str, str, str, str, str], PreferenceFact] = {}
    for fact in facts:
        if fact.status != "active" or fact.dimension not in ENTITY_DIMENSIONS:
            continue
        key = entity_fact_key(fact)
        if not key[-1]:
            continue
        current = merged.get(key)
        if current is None:
            merged[key] = replace(
                fact,
                evidence_ids=list(fact.evidence_ids),
                evidence_types=list(fact.evidence_types or [fact.source_type]),
            )
            continue
        current.confidence = max(current.confidence, fact.confidence)
        current.observed_at = max(current.observed_at, fact.observed_at)
        current.evidence_ids = list(
            dict.fromkeys([*current.evidence_ids, *fact.evidence_ids])
        )
        current.evidence_types = list(
            dict.fromkeys(
                [
                    *current.evidence_types,
                    *(fact.evidence_types or [fact.source_type]),
                ]
            )
        )
        current.decision_eligible = (
            current.decision_eligible
            or fact.decision_eligible
            or len(set(current.evidence_types)) >= 2
        )
    return list(merged.values())


def fair_entity_selection(
    facts: Sequence[PreferenceFact] | Iterator[PreferenceFact], limit: int = 500
) -> list[PreferenceFact]:
    """Water-fill observed semantic buckets; rank evidence only within buckets."""
    grouped: dict[tuple[str, str, str, str], list[PreferenceFact]] = defaultdict(list)
    for fact in facts:
        grouped[(fact.scope, fact.facet, fact.dimension, fact.category)].append(fact)
    buckets = {
        key: deque(sorted(values, key=entity_strength, reverse=True))
        for key, values in grouped.items()
    }
    selected: list[PreferenceFact] = []
    active_keys = sorted(buckets)
    while active_keys and len(selected) < limit:
        next_keys = []
        for key in active_keys:
            if len(selected) >= limit:
                break
            bucket = buckets[key]
            if bucket:
                selected.append(bucket.popleft())
            if bucket:
                next_keys.append(key)
        active_keys = next_keys
    return selected


class EntityEvidenceIndex:
    """Exact aggregation plus fair, bounded retention for entity evidence."""

    def __init__(self, max_entries: int = 500) -> None:
        self.max_entries = max_entries
        self._entries: dict[
            tuple[str, str, str, str, str, str], PreferenceFact
        ] = {}

    @property
    def facts(self) -> list[PreferenceFact]:
        return list(self._entries.values())

    def __len__(self) -> int:
        return len(self._entries)

    def ingest(self, fact: PreferenceFact) -> PreferenceFact | None:
        if fact.status != "active" or fact.dimension not in ENTITY_DIMENSIONS:
            return None
        key = entity_fact_key(fact)
        if not key[-1]:
            return None
        existing = self._entries.get(key)
        if existing is not None:
            merged = aggregate_entity_facts([existing, fact])[0]
            self._entries[key] = merged
            return merged
        self._entries[key] = replace(
            fact,
            evidence_ids=list(fact.evidence_ids),
            evidence_types=list(fact.evidence_types or [fact.source_type]),
        )
        self.prune()
        return self._entries.get(key)

    def prune(self) -> None:
        if len(self._entries) <= self.max_entries:
            return
        selected = fair_entity_selection(self._entries.values(), self.max_entries)
        self._entries = {entity_fact_key(fact): fact for fact in selected}

    def reset(self) -> None:
        self._entries.clear()


class CombinedFactView(Sequence[PreferenceFact]):
    """Backward-compatible read view; append routes to the appropriate tier."""

    def __init__(
        self,
        preference_store: FactStore,
        entity_index: EntityEvidenceIndex,
        tiered: bool,
    ) -> None:
        self.preference_store = preference_store
        self.entity_index = entity_index
        self.tiered = tiered

    def __iter__(self) -> Iterator[PreferenceFact]:
        yield from self.preference_store.facts
        if self.tiered:
            yield from self.entity_index.facts

    def __len__(self) -> int:
        return len(self.preference_store.facts) + (
            len(self.entity_index) if self.tiered else 0
        )

    def __getitem__(self, index):
        return list(self)[index]

    def append(self, fact: PreferenceFact) -> None:
        if self.tiered and fact.dimension in ENTITY_DIMENSIONS:
            self.entity_index.ingest(fact)
        else:
            self.preference_store.ingest(fact)

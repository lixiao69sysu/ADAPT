"""Observable evidence and confidence aggregation for preference beliefs.

The long-term ``FactStore`` is intentionally a compact task-facing view.  This
module keeps the information which must *not* be collapsed into a single
confidence number: provenance, persistence intent, conditional context and
independent corroboration.  It has no evaluator, benchmark-ID or product-ID
inputs and is safe to use both in VitaBench and synthetic-schema tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha1
from math import prod
import re
from typing import Iterable

from agent.memory.facts import PreferenceFact


# A direct utterance is durable only when the user scopes it beyond the active
# request.  This deliberately excludes ordinary imperative task language such
# as "不要小料"; that remains a current-task constraint, not a global profile.
_PERSISTENCE_PATTERNS = (
    "以后", "今后", "之后都", "下次也", "默认", "一直", "长期", "总是", "永远",
    "每次", "固定", "习惯", "平时都", "通常都", "不再", "再也不",
)

_SOURCE_RELIABILITY = {
    "direct_user": 0.98,
    "complaint": 0.96,
    "conversation": 0.90,
    "order": 0.88,
    "review": 0.80,
    "comment": 0.78,
    "favorite": 0.74,
    "add_to_cart": 0.66,
    "high_freq_browse": 0.55,
    "search": 0.38,
    "browse": 0.25,
}


def is_persistent_statement(text: str) -> bool:
    """Whether a live user turn explicitly asks to update future preference."""
    normalized = re.sub(r"\s+", "", text or "")
    return bool(normalized) and any(marker in normalized for marker in _PERSISTENCE_PATTERNS)


def condition_signature(value: str, explicit: str = "") -> str:
    """Keep conditional facts in a separate belief slot.

    The parser represents a conditional preference as ``condition=>choice``.
    This intentionally uses only the user-visible value; it never infers a
    condition from candidate labels or benchmark metadata.
    """
    if explicit:
        return explicit.strip()[:160]
    if "=>" in (value or ""):
        return value.split("=>", 1)[0].strip()[:160]
    return ""


@dataclass(frozen=True)
class PreferenceEvidence:
    evidence_id: str
    slot: tuple[str, str, str, str, str, str]
    value: str
    polarity: str
    source_kind: str
    extraction_confidence: float
    observed_at: str
    persistence: str
    raw_fingerprint: str


@dataclass(frozen=True)
class PreferenceHypothesis:
    """Aggregated, explainable confidence for one scoped preference value."""

    slot: tuple[str, str, str, str, str, str]
    value: str
    polarity: str
    confidence: float
    evidence_count: int
    source_diversity: int
    latest_observed_at: str


class PreferenceEvidenceStore:
    """Append-only observable evidence ledger with deterministic aggregation."""

    def __init__(self) -> None:
        self._evidence: dict[str, PreferenceEvidence] = {}
        self._by_hypothesis: dict[tuple[tuple[str, str, str, str, str, str], str, str], set[str]] = {}

    @staticmethod
    def _slot(fact: PreferenceFact) -> tuple[str, str, str, str, str, str]:
        # Category prevents beverage/restaurant brand collisions while the
        # condition keeps a conditional preference from replacing its default.
        return (
            fact.scope,
            fact.facet,
            fact.dimension,
            fact.category,
            condition_signature(fact.value, getattr(fact, "condition_signature", "")),
            getattr(fact, "persistence", "persistent"),
        )

    def observe_fact(
        self,
        fact: PreferenceFact,
        *,
        evidence_id: str = "",
        source_kind: str = "",
        extraction_confidence: float | None = None,
        persistence: str | None = None,
    ) -> PreferenceHypothesis:
        source = source_kind or getattr(fact, "source_kind", "") or fact.source_type or "unknown"
        durable = persistence or getattr(fact, "persistence", "persistent") or "persistent"
        confidence = extraction_confidence
        if confidence is None:
            confidence = getattr(fact, "extraction_confidence", None)
        if confidence is None:
            confidence = fact.confidence
        # Event IDs are transport identifiers, not independent evidence.  A
        # replay of the same observable value/source/timestamp must therefore
        # retain the same fingerprint even when its caller assigns a new ID.
        raw = "|".join(
            (
                fact.scope,
                fact.facet,
                fact.dimension,
                fact.category,
                fact.value,
                fact.polarity,
                fact.observed_at,
                source,
                condition_signature(fact.value, getattr(fact, "condition_signature", "")),
            )
        )
        fingerprint = sha1(raw.encode("utf-8")).hexdigest()[:20]
        identity = evidence_id or (fact.evidence_ids[-1] if fact.evidence_ids else fingerprint)
        evidence = PreferenceEvidence(
            evidence_id=str(identity),
            slot=self._slot(fact),
            value=fact.value,
            polarity=fact.polarity,
            source_kind=source,
            extraction_confidence=max(0.0, min(1.0, float(confidence))),
            observed_at=fact.observed_at,
            persistence=durable,
            raw_fingerprint=fingerprint,
        )
        existing = self._evidence.get(evidence.evidence_id)
        if existing is None:
            self._evidence[evidence.evidence_id] = evidence
            key = (evidence.slot, evidence.value, evidence.polarity)
            self._by_hypothesis.setdefault(key, set()).add(evidence.evidence_id)
        return self.hypothesis_for(fact) or PreferenceHypothesis(
            evidence.slot, fact.value, fact.polarity, evidence.extraction_confidence, 1, 1, fact.observed_at
        )

    def hypothesis_for(self, fact: PreferenceFact) -> PreferenceHypothesis | None:
        slot = self._slot(fact)
        key = (slot, fact.value, fact.polarity)
        records = [self._evidence[item] for item in self._by_hypothesis.get(key, set())]
        if not records:
            return None
        # Duplicate ingestion of the same raw observation cannot raise belief.
        distinct: dict[str, PreferenceEvidence] = {}
        for record in records:
            previous = distinct.get(record.raw_fingerprint)
            if previous is None or record.extraction_confidence > previous.extraction_confidence:
                distinct[record.raw_fingerprint] = record
        independent = list(distinct.values())
        supports = []
        for record in independent:
            reliability = _SOURCE_RELIABILITY.get(record.source_kind, 0.50)
            # Preserve a conservative floor for direct observable evidence,
            # but weak browsing/search cannot become decisive on its own.
            supports.append(min(0.98, 0.18 + 0.82 * reliability * record.extraction_confidence))
        confidence = 1.0 - prod(1.0 - support for support in supports)
        source_diversity = len({record.source_kind for record in independent})
        latest = max((record.observed_at for record in independent), default="")
        return PreferenceHypothesis(
            slot=slot,
            value=fact.value,
            polarity=fact.polarity,
            confidence=round(min(0.99, confidence), 4),
            evidence_count=len(independent),
            source_diversity=source_diversity,
            latest_observed_at=latest,
        )

    def apply_to_facts(self, facts: Iterable[PreferenceFact]) -> None:
        """Refresh projected facts without making the old FactStore an authority."""
        for fact in facts:
            hypothesis = self.hypothesis_for(fact)
            if hypothesis is None:
                continue
            fact.confidence = hypothesis.confidence
            fact.belief_confidence = hypothesis.confidence
            fact.independent_evidence_count = hypothesis.evidence_count
            fact.source_diversity = hypothesis.source_diversity

    def stats(self) -> dict[str, int]:
        return {
            "evidence_entries": len(self._evidence),
            "hypotheses": len(self._by_hypothesis),
        }

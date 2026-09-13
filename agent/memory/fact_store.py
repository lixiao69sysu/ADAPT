"""Incremental, evidence-preserving store for structured preferences."""

from __future__ import annotations

from dataclasses import dataclass, field

from agent.memory.facts import SCALAR_DIMENSIONS, PreferenceFact

# The single-valued boundary is declared once, in ``facts``; see the comment on
# ``SCALAR_DIMENSIONS`` there.
_SCALAR_DIMENSIONS = SCALAR_DIMENSIONS
_REJECT_VALUES = {"随便", "都行", "你看着办", "我不太清楚", "不清楚"}
_NEGATIVE_NOISE_MARKERS = (
    "配送员",
    "电梯",
    "抽烟",
    "别人催",
    "慢慢来",
    "快递员",
    "服务员态度",
)


@dataclass
class FactStore:
    """Deduplicate evidence and supersede only scoped scalar slots."""

    max_facts: int = 500
    facts: list[PreferenceFact] = field(default_factory=list)

    def ingest(
        self, fact: PreferenceFact, *, confirmed_drift: bool = False
    ) -> PreferenceFact | None:
        """Deduplicate against the same slot, then resolve a scalar conflict.

        ``confirmed_drift`` records that the drift detector vouched for this
        fact.  Supersession no longer depends on that verdict (see
        :meth:`_supersede_slot`), but the detector's own bookkeeping — decayed
        confidence and retrieval suppression — still runs, so the keyword is
        kept for callers that report it.
        """
        value = (fact.value or "").strip()
        if not value or value in _REJECT_VALUES or len(value) > 80:
            return None
        if fact.polarity == "negative":
            if any(mark in value for mark in _NEGATIVE_NOISE_MARKERS):
                return None
            if value[:1] in "，、。；,;" or any(mark in value for mark in "，。；;!?！？"):
                return None
        for existing in self.facts:
            if (
                fact.polarity == "negative"
                and existing.status == "active"
                and existing.scope == fact.scope
                and existing.facet == fact.facet
                and existing.dimension == fact.dimension
                and (existing.value in fact.value or fact.value in existing.value)
            ):
                if len(fact.value) < len(existing.value):
                    existing.value = fact.value
                    existing.category = fact.value
                existing.confidence = max(existing.confidence, fact.confidence)
                existing.evidence_ids = list(
                    dict.fromkeys(existing.evidence_ids + fact.evidence_ids)
                )
                existing.evidence_types = list(
                    dict.fromkeys(existing.evidence_types + fact.evidence_types)
                )
                existing.decision_eligible = (
                    existing.decision_eligible
                    or fact.decision_eligible
                    or len(set(existing.evidence_types)) >= 2
                )
                return existing
            if (
                existing.scope == fact.scope
                and existing.facet == fact.facet
                and existing.dimension == fact.dimension
                and existing.category == fact.category
                and existing.polarity == fact.polarity
                and existing.value == fact.value
            ):
                existing.confidence = max(existing.confidence, fact.confidence)
                existing.observed_at = max(existing.observed_at, fact.observed_at)
                existing.evidence_ids = list(
                    dict.fromkeys(existing.evidence_ids + fact.evidence_ids)
                )
                existing.evidence_types = list(
                    dict.fromkeys(existing.evidence_types + fact.evidence_types)
                )
                existing.decision_eligible = (
                    existing.decision_eligible
                    or fact.decision_eligible
                    or len(set(existing.evidence_types)) >= 2
                )
                existing.status = "active"
                # Re-observing a value is a new observation of that slot, not
                # merely a confidence bump. When the value had been superseded
                # earlier — the user changed their mind and has now changed it
                # back — reactivating it must also retire whichever value was
                # active in the meantime, or the slot keeps both.
                self._supersede_slot(existing)
                return existing
        self._supersede_slot(fact)
        self.facts.append(fact)
        self.prune()
        return fact

    def _supersede_slot(self, fact: PreferenceFact) -> None:
        """Retire the active value this fact contradicts, if the slot is scalar.

        A new value landing on an already-occupied single-valued slot *is* the
        change; it does not need a separate three-observation drift verdict to
        be believed.  Gating this on ``confirmed_drift`` was why supersession
        never fired while forgetting fired thousands of times: the drift
        detector's own slot key ignored ``category`` and admitted only one
        predicate, so the store's supersession branch was waiting for a signal
        the detector could not produce.

        The boundary that must not move is the one above: only dimensions in
        ``_SCALAR_DIMENSIONS`` are superseded, and only positive-against-
        positive.  An added aversion, allergy, brand or product therefore keeps
        accumulating — it never evicts a sibling value.

        Superseding an already-superseded fact is idempotent, so the drift path
        and the new-value path may both fire on one fact.
        """
        if fact.dimension not in _SCALAR_DIMENSIONS or fact.polarity == "negative":
            return
        for existing in self.facts:
            if (
                existing.status == "active"
                and existing.scope == fact.scope
                and existing.facet == fact.facet
                and existing.dimension == fact.dimension
                and existing.category == fact.category
                and existing.polarity == fact.polarity
                and existing.value != fact.value
            ):
                existing.status = "superseded"

    def active(
        self,
        *,
        scope: str | None = None,
        facet: str | None = None,
        category: str | None = None,
    ) -> list[PreferenceFact]:
        return [
            fact
            for fact in self.facts
            if fact.status == "active"
            and (scope is None or fact.scope == scope)
            and (facet is None or fact.facet == facet)
            and (category is None or fact.category == category)
        ]

    def prune(self) -> None:
        if len(self.facts) <= self.max_facts:
            return
        self.facts[:] = sorted(
            self.facts,
            key=lambda fact: (
                fact.status == "active",
                fact.confidence,
                fact.observed_at,
            ),
            reverse=True,
        )[: self.max_facts]

    def reset(self) -> None:
        self.facts.clear()

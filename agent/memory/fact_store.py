"""Incremental, evidence-preserving store for structured preferences."""

from __future__ import annotations

from dataclasses import dataclass, field

from agent.memory.facts import PreferenceFact

_SCALAR_DIMENSIONS = {
    "temperature",
    "sweetness",
    "taste",
    "topping",
    "room_type",
    "transport",
    "budget",
    "time",
}
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
                and getattr(existing, "condition_signature", "")
                == getattr(fact, "condition_signature", "")
                and getattr(existing, "persistence", "persistent")
                == getattr(fact, "persistence", "persistent")
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
                and getattr(existing, "condition_signature", "")
                == getattr(fact, "condition_signature", "")
                and getattr(existing, "persistence", "persistent")
                == getattr(fact, "persistence", "persistent")
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
                return existing
        if (
            confirmed_drift
            and fact.dimension in _SCALAR_DIMENSIONS
            and fact.polarity != "negative"
        ):
            for existing in self.facts:
                if (
                    existing.status == "active"
                    and existing.scope == fact.scope
                    and existing.facet == fact.facet
                    and existing.dimension == fact.dimension
                    and existing.category == fact.category
                    and existing.polarity == fact.polarity
                    and existing.value != fact.value
                    and getattr(existing, "condition_signature", "")
                    == getattr(fact, "condition_signature", "")
                    and getattr(existing, "persistence", "persistent")
                    == getattr(fact, "persistence", "persistent")
                ):
                    existing.status = "superseded"
        self.facts.append(fact)
        self.prune()
        return fact

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

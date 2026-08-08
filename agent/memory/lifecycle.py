"""Fact lifecycle and selective forgetting.

Different preference facts have different natural lifespans:
- durable   : health constraints (vegetarian, allergy, PCOS) - almost never forgotten
- normal    : taste / brand preferences - forgotten after ~6 months of disuse
- ephemeral : fleeting interests - forgotten after ~1 month

Confidence decays exponentially with a per-type half-life. When confidence
falls below a floor, the fact is evicted from the stream. This prevents
outdated preferences from diluting retrieval precision.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional

from agent.memory.stream import MemoryEvent, MemoryStream, parse_timestamp
from agent.memory.signals import Signal, TYPE_IMPORTANCE

# Half-lives (in days) per lifetime type.
HALF_LIFE_DAYS = {
    "durable": 3650.0,     # ~10 years: health constraints, addresses
    "normal": 180.0,       # ~6 months: taste, brand
    "ephemeral": 30.0,     # ~1 month: fleeting interests
}

# Predicates that encode durable constraints (health, identity).
DURABLE_PREDICATES = {
    "avoids_food",         # allergies / strong dislikes
    "delivery_address",    # addresses don't change often
    "brand_loyalty",       # brand loyalty persists
    "explicit_preference", # explicitly stated preferences
}

# Signals whose type suggests a durable trait.
DURABLE_TYPES = {
    "complaint",           # a strong negative signal usually persists
    "order",               # actual purchases indicate real preferences
}

# Predicates that are ephemeral (short-lived).
EPHEMERAL_PREDICATES = {
    "searches",            # search history is fleeting
    "intent_product",      # purchase intent changes
    "taste_preference",    # taste can change
    "urgency",             # urgency is temporary
    "budget_conscious",    # budget can change
    "quality_prefer",      # quality preference can change
}

# Confidence floor: below this, evict.
CONFIDENCE_FLOOR = 0.2


@dataclass
class Fact:
    """A stored preference fact with lifecycle metadata."""

    predicate: str
    value: str
    confidence: float
    lifetime_type: str        # durable | normal | ephemeral
    last_seen: str            # timestamp
    event_id: Optional[int] = None

    def decay(self, age_days: float) -> float:
        hl = HALF_LIFE_DAYS.get(self.lifetime_type, 180.0)
        return self.confidence * math.exp(-math.log(2) * age_days / hl)

    def is_alive(self, age_days: float) -> bool:
        return self.decay(age_days) >= CONFIDENCE_FLOOR


class LifecycleManager:
    """Assigns lifetime types to facts and applies decay/forgetting."""

    def __init__(self) -> None:
        self.facts: List[Fact] = []

    def classify(self, predicate: str, sig_type: str) -> str:
        if predicate in DURABLE_PREDICATES or sig_type in DURABLE_TYPES:
            return "durable"
        if predicate in EPHEMERAL_PREDICATES or sig_type in ("search", "browse", "high_freq_browse"):
            return "ephemeral"
        return "normal"

    def record(self, signal: Signal) -> None:
        """Add or reinforce a fact from a signal."""
        # Skip pure observations (too noisy to keep as durable facts).
        if signal.predicate == "raw_observation":
            return
        lt = self.classify(signal.predicate, signal.type)
        # Find an existing fact with same predicate+value.
        for f in self.facts:
            if f.predicate == signal.predicate and self._same(f.value, signal.object):
                # Reinforce confidence.
                f.confidence = min(1.0, f.confidence + 0.15 * signal.confidence)
                f.last_seen = signal.timestamp
                return
        self.facts.append(Fact(
            predicate=signal.predicate,
            value=signal.object,
            confidence=signal.confidence,
            lifetime_type=lt,
            last_seen=signal.timestamp,
        ))

    def apply_forgetting(self, stream: MemoryStream, now_str: str) -> int:
        """Decay all facts, evict dead ones from the stream, return evicted count.

        Also evicts the corresponding stream events so retrieval stops surfacing
        forgotten preferences.
        """
        ref = parse_timestamp(now_str)
        evicted = 0
        for f in list(self.facts):
            last = parse_timestamp(f.last_seen)
            if last is None or ref is None:
                continue
            age_days = max(0.0, (ref - last).total_seconds() / 86400.0)
            if not f.is_alive(age_days):
                self.facts.remove(f)
                evicted += 1
        # Also drop stream events whose signals point to forgotten facts.
        forgotten = {
            (f.predicate, f.value) for f in self.facts
        }  # current alive facts
        alive_events = []
        for ev in stream.all():
            sig = ev.signal
            if sig is None:
                alive_events.append(ev)
                continue
            # Keep if the fact is alive OR raw observation.
            if sig.predicate == "raw_observation":
                alive_events.append(ev)
                continue
            key = (sig.predicate, sig.object)
            if any(self._same(fv, sig.object) for (fp, fv) in [(f.predicate, f.value) for f in self.facts] if fp == sig.predicate):
                alive_events.append(ev)
            else:
                # This event's fact is forgotten -> drop from stream.
                pass
        stream.events = alive_events
        return evicted

    @staticmethod
    def _same(a: str, b: str) -> bool:
        return a == b or a in b or b in a

    def summary(self) -> List[dict]:
        return [{"predicate": f.predicate, "value": f.value,
                 "confidence": round(f.confidence, 2),
                 "type": f.lifetime_type} for f in self.facts]

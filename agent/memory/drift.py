"""Preference drift detection: detect when user preferences shift over time.

The core idea: monitor specific preference *dimensions* (taste, budget level,
hotel room type, etc.) for shifts. A drift is declared when a NEW value on a
dimension repeatedly conflicts with the established value.

Important: NOT every new product is a drift. Buying different dishes is normal
consumption variety. Drift applies only to genuinely single-valued dimensions
where one preference governs the direction (e.g. 热饮 -> 冰饮). Multi-valued sets
(avoids, allergies, brands, products, likes, searches) never drift and never
evict one another.

The slot a value lives in is the same slot the fact store supersedes in:
``(scope, facet, dimension, category)``, taken from ``PreferenceFact.slot_key``.
The previous version of this module compared a *different* slot — it dropped
``category`` and admitted a single predicate — so the two mechanisms could not
agree on what "the same preference" meant and supersession never fired (E-090).

Reference structure (from VitaBench user_scenario): each preference has a
change history with entries {content, type: unchanged|changed, source}. We
build the same "current preference" notion from observed signals.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from agent.memory.facts import SCALAR_DIMENSIONS, fact_from_signal
from agent.memory.signals import Signal


@dataclass
class PreferenceSlot:
    """Current state of a single preference dimension."""

    value: str
    confidence: float
    last_seen_ts: str
    conflict_count: int = 0
    drifted: bool = False

    def to_dict(self) -> dict:
        return {
            "value": self.value,
            "confidence": round(self.confidence, 4),
            "conflict_count": self.conflict_count,
            "drifted": self.drifted,
        }


class DriftDetector:
    """Tracks preference dimensions and detects drift from conflicting signals."""

    def __init__(self, drift_threshold: int = 2, decay_factor: float = 0.3) -> None:
        """Args:
            drift_threshold: number of conflicts before declaring drift.
            decay_factor: old value confidence multiplier after drift.
        """
        self.drift_threshold = drift_threshold
        self.decay_factor = decay_factor
        self.slots: Dict[tuple[str, str, str, str], PreferenceSlot] = {}

    @staticmethod
    def _slot_key(signal: Signal) -> Optional[tuple[str, str, str, str]]:
        """The drift slot, or ``None`` when this signal may not drift at all.

        Participation is decided by the *resulting fact's dimension*, using the
        single-valued set the fact store also supersedes on — not by a
        hand-maintained predicate whitelist. Every predicate that can produce a
        genuinely single-valued dimension therefore participates, and every
        predicate producing a multi-valued set (avoids, brands, products,
        likes, searches) is excluded by construction.

        Negative facts are excluded too. Aversion is additive by design: a new
        "don't eat X" is not a change of mind about "don't eat Y".
        """
        fact = fact_from_signal(signal)
        if fact.polarity == "negative" or fact.dimension not in SCALAR_DIMENSIONS:
            return None
        # Exactly the key ``FactStore`` supersedes in; ``slot_key`` is where a
        # negative substitutes its value for the category, which the polarity
        # guard above already makes unreachable.
        return fact.slot_key

    def observe(self, signal: Signal) -> Optional[str]:
        """Process a new signal, updating dimension slots.

        Returns the predicate if a drift was detected, else None.
        """
        key = self._slot_key(signal)
        if key is None:
            return None

        value = signal.object
        slot = self.slots.get(key)

        if slot is None:
            self.slots[key] = PreferenceSlot(value, signal.confidence, signal.timestamp)
            return None

        # Same value -> reinforce.
        if self._same_value(slot.value, value):
            slot.confidence = min(1.0, slot.confidence + 0.1)
            slot.last_seen_ts = signal.timestamp
            slot.conflict_count = 0
            return None

        # Conflicting value -> increment conflict counter.
        slot.conflict_count += 1
        if slot.conflict_count >= self.drift_threshold:
            # Drift detected: old value decays, new value becomes dominant.
            slot.confidence *= self.decay_factor
            slot.value = value
            slot.drifted = True
            slot.conflict_count = 0
            return signal.predicate

        return None

    @staticmethod
    def _same_value(a: str, b: str) -> bool:
        """Values match if one contains the other or equal."""
        return a == b or a in b or b in a

    def drift_summary(self) -> List[dict]:
        """List of drifted preferences (for diagnostics)."""
        return [
            {
                "scope": key[0],
                "facet": key[1],
                "dimension": key[2],
                "category": key[3],
                **value.to_dict(),
            }
            for key, value in self.slots.items() if value.drifted
        ]

    def suppress_drifted(self, event) -> bool:
        """Whether an event belongs to a drifted-away preference value and should
        be de-prioritized during retrieval."""
        sig = event.signal
        if sig is None:
            return False
        key = self._slot_key(sig)
        if key is None or key not in self.slots:
            return False
        slot = self.slots[key]
        if not slot.drifted:
            return False
        return not self._same_value(slot.value, sig.object)

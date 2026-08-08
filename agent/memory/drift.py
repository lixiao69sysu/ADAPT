"""Preference drift detection: detect when user preferences shift over time.

The core idea: monitor specific preference *dimensions* (taste, budget level,
hotel brand, etc.) for shifts. A drift is declared when a NEW value on a
dimension repeatedly conflicts with the established value.

Important: NOT every new product is a drift. Buying different dishes is normal
consumption variety. Drift applies only to "dimension" predicates where a
single preference governs the direction (e.g. taste spicy -> mild). Product
preferences (prefers_product) are excluded from drift and handled purely by
lifecycle decay.

Reference structure (from VitaBench user_scenario): each preference has a
change history with entries {content, type: unchanged|changed, source}. We
build the same "current preference" notion from observed signals.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from agent.memory.signals import Signal

# Predicates that represent a single preference *dimension* where a shift is
# meaningful drift. Product-level predicates (prefers_product, intent_product)
# are excluded — buying different dishes is normal variety, not drift.
DIMENSION_PREDICATES = {
    "likes_food",           # taste preference: spicy -> mild
    "avoids_food",          # dislikes are durable, rarely drift
    "brand_loyalty",        # store loyalty can shift to a new store
}


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
        self.slots: Dict[str, PreferenceSlot] = {}

    def observe(self, signal: Signal) -> Optional[str]:
        """Process a new signal, updating dimension slots.

        Returns the predicate if a drift was detected, else None.
        """
        pred = signal.predicate
        # Only tracked dimension predicates participate in drift.
        if pred not in DIMENSION_PREDICATES:
            return None

        value = signal.object
        slot = self.slots.get(pred)

        if slot is None:
            self.slots[pred] = PreferenceSlot(value, signal.confidence, signal.timestamp)
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
            return pred

        return None

    @staticmethod
    def _same_value(a: str, b: str) -> bool:
        """Values match if one contains the other or equal."""
        return a == b or a in b or b in a

    def drift_summary(self) -> List[dict]:
        """List of drifted preferences (for diagnostics)."""
        return [
            {"predicate": k, **v.to_dict()}
            for k, v in self.slots.items() if v.drifted
        ]

    def suppress_drifted(self, event) -> bool:
        """Whether an event belongs to a drifted-away preference value and should
        be de-prioritized during retrieval."""
        sig = event.signal
        if sig is None or sig.predicate not in self.slots:
            return False
        slot = self.slots[sig.predicate]
        if not slot.drifted:
            return False
        return not self._same_value(slot.value, sig.object)

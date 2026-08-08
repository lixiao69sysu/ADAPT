"""Memory Stream: ordered event storage with timestamps and importance.

Inspired by Generative Agents (Park et al. 2023): every interaction is an
event with a timestamp and importance score. The stream is the raw substrate
that Reflection later synthesizes into structured preferences.

Unlike a plain list, the stream supports:
- importance-weighted queries
- recency-aware decay (for later retrieval)
- salience tracking (which events were actually used)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from agent.memory.signals import Signal, TYPE_IMPORTANCE


@dataclass
class MemoryEvent:
    """A single memory event in the stream."""

    id: int
    timestamp: str                  # YYYY-MM-DD HH:MM:SS
    type: str                       # order / complaint / ...
    signal: Optional[Signal]        # structured signal (may be None for raw)
    raw_text: str = ""
    importance: float = 5.0         # static, from type prior + content
    salience: float = 0.0           # dynamic, increments when retrieved

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "type": self.type,
            "importance": self.importance,
            "salience": self.salience,
            "raw_text": self.raw_text[:200],
        }


# Timestamp helpers ------------------------------------------------------------
_TIMESTAMP_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?:\s+(\d{2}):(\d{2}):(\d{2}))?")


def parse_timestamp(ts: str) -> Optional[datetime]:
    """Parse a VitaBench timestamp. Returns None if unparseable."""
    m = _TIMESTAMP_RE.match(ts.strip())
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    hh = int(m.group(4) or 0)
    mm = int(m.group(5) or 0)
    ss = int(m.group(6) or 0)
    try:
        return datetime(y, mo, d, hh, mm, ss)
    except ValueError:
        return None


class MemoryStream:
    """Ordered event store. Append-only with optional decay-aware reads."""

    def __init__(self) -> None:
        self.events: List[MemoryEvent] = []
        self._next_id = 0

    def add(self, signal: Signal) -> MemoryEvent:
        ev = MemoryEvent(
            id=self._next_id,
            timestamp=signal.timestamp,
            type=signal.type,
            signal=signal,
            raw_text=signal.raw,
            importance=signal.importance,
        )
        self._next_id += 1
        self.events.append(ev)
        return ev

    def __len__(self) -> int:
        return len(self.events)

    def all(self) -> List[MemoryEvent]:
        return list(self.events)

    def mark_retrieved(self, event: MemoryEvent) -> None:
        event.salience += 1.0

    def reset(self) -> None:
        self.events = []
        self._next_id = 0

    # -- queries ---------------------------------------------------------------

    def recent(self, n: int = 20) -> List[MemoryEvent]:
        """Most recent n events (by insertion order)."""
        return self.events[-n:]

    def most_important(self, n: int = 20) -> List[MemoryEvent]:
        """Top-n events by importance (with salience bonus)."""
        scored = sorted(
            self.events,
            key=lambda e: e.importance + e.salience,
            reverse=True,
        )
        return scored[:n]

    def by_type(self, types: set[str]) -> List[MemoryEvent]:
        return [e for e in self.events if e.type in types]

    def to_string(self, limit: int = 50) -> str:
        """Render recent events as a readable block (for debugging/system prompt)."""
        lines = []
        for ev in self.events[-limit:]:
            sig = ev.signal
            if sig and sig.predicate != "raw_observation":
                lines.append(f"[{ev.timestamp}] {sig.predicate}: {sig.object} (conf={sig.confidence:.2f})")
            elif ev.raw_text:
                lines.append(f"[{ev.timestamp}] {ev.type}: {ev.raw_text[:80]}")
            else:
                lines.append(f"[{ev.timestamp}] {ev.type}")
        return "\n".join(lines)

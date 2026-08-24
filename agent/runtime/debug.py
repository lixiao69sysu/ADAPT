"""Agent-owned structured trace sidecar with no evaluator dependencies."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class DebugEventStore:
    context: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    sequence: int = 0

    def emit(self, event: str, **payload: Any) -> None:
        self.sequence += 1
        blocked = {"rubric", "reward", "target_product_ids", "target", "distraction"}
        safe = {key: value for key, value in payload.items() if key not in blocked}
        self.events.append(
            {"seq": self.sequence, "event": event, **self.context, **safe}
        )

    def dump_jsonl(self, path: Path, *, append: bool = True) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a" if append else "w", encoding="utf-8") as handle:
            for event in self.events:
                handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

    def clear(self) -> None:
        self.events.clear()

"""Load promoted or explicitly selected evolution strategies at Agent runtime."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _merge(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge(target[key], value)
        else:
            target[key] = value


def load_strategy_changes(
    registry_path: str | Path | None = None,
    candidate_strategy_id: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Return changes grouped by component; pending strategies require opt-in."""
    raw_path = registry_path or os.environ.get("ADAPT_STRATEGY_REGISTRY")
    if not raw_path:
        return {}
    path = Path(raw_path)
    if not path.exists():
        raise FileNotFoundError(f"ADAPT strategy registry not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    selected = set(payload.get("active_strategy_ids") or [])
    explicit = candidate_strategy_id or os.environ.get("ADAPT_CANDIDATE_STRATEGY_ID")
    if explicit:
        selected.add(explicit)
    grouped: dict[str, dict[str, Any]] = {}
    candidates = payload.get("candidates") or {}
    for strategy_id in sorted(selected):
        candidate = candidates.get(strategy_id)
        if candidate is None:
            raise KeyError(f"strategy {strategy_id} not found in {path}")
        component = str(candidate.get("component") or "")
        if not component:
            raise ValueError(f"strategy {strategy_id} has no component")
        _merge(grouped.setdefault(component, {}), candidate.get("changes") or {})
    return grouped

"""Versioned local registry for proposed and promoted Agent strategies."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.evolution.models import CandidateStrategy


class StrategyRegistry:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "schema_version": 1,
                "active_strategy_ids": [],
                "candidates": {},
                "history": [],
            }
        with self.path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if payload.get("schema_version") != 1:
            raise ValueError("unsupported strategy registry schema")
        return payload

    def _save(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def upsert(self, candidates: list[CandidateStrategy]) -> None:
        payload = self.load()
        for candidate in candidates:
            existing = payload["candidates"].get(candidate.strategy_id)
            # Refresh proposal fields while retaining audit evidence written by
            # probes and gates.  Re-analysis must never erase the evidence that
            # justified an earlier terminal decision.
            value = {**(existing or {}), **candidate.to_dict()}
            if existing and existing.get("status") in {"promoted", "rejected"}:
                value["status"] = existing["status"]
                value["decision_reasons"] = existing.get("decision_reasons", [])
            payload["candidates"][candidate.strategy_id] = value
        self._save(payload)

    def record_decision(
        self,
        strategy_id: str,
        *,
        promoted: bool,
        reasons: list[str],
    ) -> None:
        payload = self.load()
        if strategy_id not in payload["candidates"]:
            raise KeyError(f"unknown strategy {strategy_id}")
        status = "promoted" if promoted else "rejected"
        payload["candidates"][strategy_id]["status"] = status
        payload["candidates"][strategy_id]["decision_reasons"] = reasons
        active = set(payload["active_strategy_ids"])
        if promoted:
            active.add(strategy_id)
        else:
            active.discard(strategy_id)
        payload["active_strategy_ids"] = sorted(active)
        payload["history"].append(
            {
                "strategy_id": strategy_id,
                "status": status,
                "reasons": reasons,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        self._save(payload)

    def record_probe(self, strategy_id: str, probe: dict[str, Any]) -> None:
        payload = self.load()
        if strategy_id not in payload["candidates"]:
            raise KeyError(f"unknown strategy {strategy_id}")
        payload["candidates"][strategy_id].setdefault("probes", []).append(probe)
        self._save(payload)

"""Tool-grounded candidate observations without category inference."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

InventoryState = Literal["available", "unavailable", "unknown"]
_ID_KEY = re.compile(r"(?:^|_)(?:id|ids)$", re.IGNORECASE)
_ID_PAIR = re.compile(r"([A-Za-z][A-Za-z0-9_]*_id)\s*[=:]\s*['\"]?([^,'\"\s\)\]}]+)")
_INVENTORY_PAIR = re.compile(
    r"(?:quantity|inventory|stock|available_quantity)\s*[=:]\s*['\"]?(-?\d+)",
    re.IGNORECASE,
)


class CandidateObservation(BaseModel):
    snapshot_version: int
    turn_id: int
    tool_name: str
    candidate_id: str
    id_field: str = "id"
    parent_id: str | None = None
    parent_ids: list[str] = Field(default_factory=list)
    observed_fields: dict[str, Any] = Field(default_factory=dict)
    inventory_state: InventoryState = "unknown"
    raw_evidence_ref: str


def _inventory(fields: dict[str, Any], raw: str = "") -> InventoryState:
    for key, value in fields.items():
        lowered = str(key).lower()
        if lowered in {"quantity", "inventory", "stock", "available_quantity"}:
            try:
                return "unavailable" if float(value) <= 0 else "available"
            except (TypeError, ValueError):
                continue
    match = _INVENTORY_PAIR.search(raw)
    if match:
        return "unavailable" if int(match.group(1)) <= 0 else "available"
    return "unknown"


class ObservationStore:
    """Versioned snapshots of exact IDs and fields returned by tools."""

    def __init__(self) -> None:
        self.snapshot_version = 0
        self.observations: list[CandidateObservation] = []
        self._latest: dict[str, CandidateObservation] = {}

    def reset(self) -> None:
        self.snapshot_version = 0
        self.observations.clear()
        self._latest.clear()

    def observe(self, tool_name: str, content: Any, turn_id: int) -> list[CandidateObservation]:
        self.snapshot_version += 1
        raw = str(content or "")
        evidence_ref = "tool-" + hashlib.sha256(
            f"{tool_name}:{raw}".encode()
        ).hexdigest()[:16]
        records: list[tuple[dict[str, Any], str]] = []
        parsed: Any = None
        if isinstance(content, (dict, list)):
            parsed = content
        elif isinstance(content, str):
            try:
                parsed = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                parsed = None
        if parsed is not None:
            self._collect_dicts(parsed, records)
        else:
            for chunk in raw.splitlines() or [raw]:
                ids = {key: value for key, value in _ID_PAIR.findall(chunk)}
                if ids:
                    records.append((ids, chunk))

        created: list[CandidateObservation] = []
        for fields, record_raw in records:
            ids = self._ids(fields)
            for id_field, candidate_id in ids:
                parents = [value for key, value in ids if key != id_field and value != candidate_id]
                observation = CandidateObservation(
                    snapshot_version=self.snapshot_version,
                    turn_id=turn_id,
                    tool_name=tool_name,
                    candidate_id=candidate_id,
                    id_field=id_field,
                    parent_id=parents[0] if parents else None,
                    parent_ids=parents,
                    observed_fields=fields,
                    inventory_state=_inventory(fields, record_raw),
                    raw_evidence_ref=evidence_ref,
                )
                self.observations.append(observation)
                self._latest[candidate_id] = observation
                created.append(observation)
        return created

    def _collect_dicts(
        self, value: Any, records: list[tuple[dict[str, Any], str]]
    ) -> None:
        if isinstance(value, dict):
            if self._ids(value):
                records.append((value, json.dumps(value, ensure_ascii=False, default=str)))
            for nested in value.values():
                self._collect_dicts(nested, records)
        elif isinstance(value, list):
            for item in value:
                self._collect_dicts(item, records)

    @staticmethod
    def _ids(fields: dict[str, Any]) -> list[tuple[str, str]]:
        result: list[tuple[str, str]] = []
        for key, value in fields.items():
            if not _ID_KEY.search(str(key)):
                continue
            values = value if isinstance(value, list) else [value]
            for item in values:
                if isinstance(item, (str, int)) and str(item).strip():
                    result.append((str(key), str(item)))
        return result

    def contains(self, candidate_id: str) -> bool:
        return str(candidate_id) in self._latest

    def latest(self, candidate_id: str) -> CandidateObservation | None:
        return self._latest.get(str(candidate_id))

    def co_observed(self, first: str, second: str) -> bool:
        first, second = str(first), str(second)
        for observation in self.observations:
            ids = {observation.candidate_id, *observation.parent_ids}
            if first in ids and second in ids:
                return True
        return False

    def relation_observed_between_fields(self, first_field: str, second_field: str) -> bool:
        def singular(value: str) -> str:
            return value[:-1] if value.lower().endswith("_ids") else value

        first_field, second_field = singular(first_field), singular(second_field)
        for observation in self.observations:
            fields = {singular(str(key)) for key in observation.observed_fields}
            if first_field in fields and second_field in fields:
                return True
        return False

    def candidate_fields(self, limit: int = 30) -> list[str]:
        fields: list[str] = []
        for observation in list(self._latest.values())[-limit:]:
            fields.append(json.dumps(observation.observed_fields, ensure_ascii=False, default=str))
        return fields

    def snapshot(self) -> list[dict[str, Any]]:
        return [item.model_dump(mode="json") for item in self.observations]

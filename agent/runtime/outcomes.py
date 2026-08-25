"""Normalize visible tool results into a typed state-machine outcome."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable


class ToolEffect(str, Enum):
    OBSERVED = "observed"
    CREATED = "created"
    CREATED_PENDING_PAYMENT = "created_pending_payment"
    PAID = "paid"
    CANCELLED = "cancelled"
    MODIFIED = "modified"
    NO_CHANGE = "no_change"


@dataclass(frozen=True)
class CorrectionEvidence:
    argument: str
    failed_value: str
    corrected_value: str


@dataclass(frozen=True)
class ToolOutcome:
    ok: bool
    effect: ToolEffect
    workflow_ids: tuple[str, ...] = ()
    correction: CorrectionEvidence | None = None
    source: str = "role"


class ToolOutcomeNormalizer:
    """Prefer structured result fields, with isolated text compatibility."""

    _PENDING = {"unpaid", "pending_payment", "payment_pending", "awaiting_payment"}
    _SUCCESS = {"ok", "success", "successful", "succeeded", "completed", "paid"}

    @classmethod
    def normalize(
        cls,
        *,
        tool_name: str,
        tool_role: str,
        content: Any,
        error: bool = False,
    ) -> ToolOutcome:
        if error:
            return ToolOutcome(False, ToolEffect.NO_CHANGE, source="tool_error")
        payload = cls._structured(content)
        statuses = cls._status_values(payload)
        workflow_ids = tuple(dict.fromkeys(cls._ids(payload)))
        if payload is not None:
            if tool_role == "create":
                effect = (
                    ToolEffect.CREATED_PENDING_PAYMENT
                    if statuses & cls._PENDING
                    else ToolEffect.CREATED
                )
            elif tool_role == "pay":
                effect = ToolEffect.PAID
            elif tool_role == "cancel":
                effect = ToolEffect.CANCELLED
            elif tool_role == "modify":
                effect = ToolEffect.MODIFIED
            else:
                effect = ToolEffect.OBSERVED
            return ToolOutcome(True, effect, workflow_ids, source="structured")

        text = str(content or "")
        lowered = text.casefold()
        workflow_ids = tuple(
            dict.fromkeys(
                match.group(2)
                for match in re.finditer(
                    r"\b([A-Za-z][A-Za-z0-9_]*_id)\s*[:=]\s*['\"]?([A-Za-z0-9_.:-]+)",
                    text,
                )
            )
        )
        if tool_role == "create":
            pending = "unpaid" in lowered or "待支付" in text
            return ToolOutcome(
                True,
                ToolEffect.CREATED_PENDING_PAYMENT if pending else ToolEffect.CREATED,
                workflow_ids,
                source="legacy_text",
            )
        if tool_role == "pay":
            ok = any(marker in lowered for marker in ("successful", "success", "paid")) or "成功" in text
            return ToolOutcome(
                ok,
                ToolEffect.PAID if ok else ToolEffect.NO_CHANGE,
                workflow_ids,
                source="legacy_text",
            )
        if tool_role == "cancel":
            return ToolOutcome(True, ToolEffect.CANCELLED, workflow_ids, source="legacy_text")
        if tool_role == "modify":
            return ToolOutcome(True, ToolEffect.MODIFIED, workflow_ids, source="legacy_text")
        return ToolOutcome(True, ToolEffect.OBSERVED, workflow_ids, source="legacy_text")

    @staticmethod
    def _structured(content: Any) -> Any | None:
        if isinstance(content, (dict, list)):
            return content
        if isinstance(content, str) and content.lstrip().startswith(("{", "[")):
            try:
                return json.loads(content)
            except (TypeError, ValueError, json.JSONDecodeError):
                return None
        return None

    @classmethod
    def _status_values(cls, payload: Any) -> set[str]:
        values: set[str] = set()
        for key, value in cls._walk(payload):
            if key.casefold() in {"status", "payment_status", "state", "result"}:
                values.add(str(value).strip().casefold())
        return values

    @classmethod
    def _ids(cls, payload: Any) -> Iterable[str]:
        for key, value in cls._walk(payload):
            if key.casefold().endswith(("_id", "_ids")):
                values = value if isinstance(value, list) else [value]
                for item in values:
                    if isinstance(item, (str, int)) and str(item):
                        yield str(item)

    @classmethod
    def _walk(cls, payload: Any) -> Iterable[tuple[str, Any]]:
        if isinstance(payload, dict):
            for key, value in payload.items():
                yield str(key), value
                yield from cls._walk(value)
        elif isinstance(payload, list):
            for value in payload:
                yield from cls._walk(value)

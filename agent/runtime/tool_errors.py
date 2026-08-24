"""Current-subtask tool failure ledger and bounded parameter recovery."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from agent.runtime.tools import ToolRole


def _normalized_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Normalize only for equality; never use this to resolve entity IDs."""
    normalized = {}
    for key, value in sorted((arguments or {}).items()):
        if key in {"note", "remark", "comment"}:
            continue
        if isinstance(value, str):
            normalized[key] = " ".join(value.split())
        elif isinstance(value, list):
            normalized[key] = list(value)
        else:
            normalized[key] = value
    return normalized


def _signature(tool_name: str, arguments: dict[str, Any]) -> str:
    payload = json.dumps(
        _normalized_arguments(arguments), ensure_ascii=False, sort_keys=True, default=str
    )
    return f"{tool_name}:{payload}"


def _error_class(content: str) -> str:
    text = (content or "").lower()
    if "longitude and latitude not found for address" in text:
        return "address_not_resolvable"
    if "not found" in text:
        return "not_found"
    if "missing" in text or "required" in text:
        return "missing_argument"
    if "invalid" in text or "错误" in text:
        return "invalid_argument"
    return "tool_error"


@dataclass(frozen=True)
class ToolAttempt:
    tool_name: str
    arguments: dict[str, Any]
    signature: str
    role: ToolRole


@dataclass(frozen=True)
class ParameterRecovery:
    argument: str
    failed_value: str
    recovered_value: str
    evidence_tool: str


@dataclass(frozen=True)
class ParameterProbe:
    tool_name: str
    argument: str
    failed_value: str
    probe_value: str


@dataclass(frozen=True)
class RecoveredToolAttempt:
    tool_name: str
    arguments: dict[str, Any]
    recovery: ParameterRecovery


class ToolErrorLedger:
    """Ephemeral evidence; reset at every subtask and never shared across users."""

    def __init__(self, max_identical_failures: int = 2) -> None:
        self.max_identical_failures = max_identical_failures
        self._pending: dict[str, ToolAttempt] = {}
        self._pending_by_name: dict[str, list[ToolAttempt]] = {}
        self._signature_failures: dict[str, int] = {}
        self._argument_failures: dict[tuple[str, str, str], int] = {}
        self._successful_arguments: dict[tuple[str, str], list[str]] = {}
        self._attempted_signatures: set[str] = set()
        self._failed_attempts: list[ToolAttempt] = []

    def reset(self) -> None:
        self._pending.clear()
        self._pending_by_name.clear()
        self._signature_failures.clear()
        self._argument_failures.clear()
        self._successful_arguments.clear()
        self._attempted_signatures.clear()
        self._failed_attempts.clear()

    def register_proposal(
        self,
        call_id: str | None,
        tool_name: str,
        arguments: dict[str, Any],
        role: ToolRole,
    ) -> None:
        attempt = ToolAttempt(
            tool_name,
            deepcopy(arguments or {}),
            _signature(tool_name, arguments or {}),
            role,
        )
        self._attempted_signatures.add(attempt.signature)
        if call_id:
            self._pending[str(call_id)] = attempt
        self._pending_by_name.setdefault(tool_name, []).append(attempt)

    def observe_result(
        self,
        call_id: str | None,
        tool_name: str,
        content: str,
        error: bool,
    ) -> ToolAttempt | None:
        attempt = self._pending.pop(str(call_id), None) if call_id else None
        queue = self._pending_by_name.get(tool_name, [])
        if attempt is None and queue:
            attempt = queue[0]
        if attempt in queue:
            queue.remove(attempt)
        if not attempt:
            return None
        if error:
            self._failed_attempts.append(attempt)
            error_class = _error_class(content)
            self._signature_failures[attempt.signature] = (
                self._signature_failures.get(attempt.signature, 0) + 1
            )
            for argument, value in attempt.arguments.items():
                if isinstance(value, (str, int, float)):
                    key = (argument, str(value), error_class)
                    self._argument_failures[key] = (
                        self._argument_failures.get(key, 0) + 1
                    )
        else:
            for argument, value in attempt.arguments.items():
                if isinstance(value, str) and value.strip():
                    key = (attempt.tool_name, argument)
                    values = self._successful_arguments.setdefault(key, [])
                    if value not in values:
                        values.append(value)
        return attempt

    def next_address_probe(self) -> ParameterProbe | None:
        """Suggest one safer resolver probe without changing a WRITE address.

        Only explicit building/unit/room suffixes are removed. The returned
        prefix must still be proven by the environment before ``recover`` can
        use it, and each probe is attempted at most once per subtask.
        """
        failed_addresses = [
            value
            for (argument, value, error_class), count in self._argument_failures.items()
            if argument == "address"
            and error_class == "address_not_resolvable"
            and count > 0
        ]
        for original in reversed(failed_addresses):
            probe = re.sub(
                r"(?:[0-9一二三四五六七八九十百]+)"
                r"(?:号楼|栋|幢|座|单元|室).*$",
                "",
                original,
            ).strip(" ,，;；")
            if (
                len(probe) < 4
                or probe == original
                or not original.startswith(probe)
            ):
                continue
            tool_name = "address_to_longitude_latitude"
            arguments = {"address": probe}
            if _signature(tool_name, arguments) in self._attempted_signatures:
                continue
            return ParameterProbe(tool_name, "address", original, probe)
        return None

    def rejection_reason(
        self, tool_name: str, arguments: dict[str, Any], role: ToolRole
    ) -> str:
        signature = _signature(tool_name, arguments or {})
        failures = self._signature_failures.get(signature, 0)
        if failures >= self.max_identical_failures:
            return (
                f"identical tool signature already failed {failures} times; "
                "change the proven-bad argument or stop"
            )
        if role == ToolRole.CREATE:
            address = str((arguments or {}).get("address", ""))
            address_failures = self._argument_failures.get(
                ("address", address, "address_not_resolvable"), 0
            )
            if address and address_failures >= self.max_identical_failures:
                return (
                    f"the same address argument already failed {address_failures} "
                    "times; use a successfully resolved equivalent or stop"
                )
        return ""

    def recover(
        self, tool_name: str, arguments: dict[str, Any], role: ToolRole
    ) -> ParameterRecovery | None:
        """Recover only a proven-bad address from a proven-good contained prefix."""
        if role != ToolRole.CREATE or not isinstance(arguments, dict):
            return None
        original = str(arguments.get("address", "")).strip()
        if not original or not any(
            argument == "address"
            and value == original
            and error_class == "address_not_resolvable"
            for argument, value, error_class in self._argument_failures
        ):
            return None
        candidates = self._successful_arguments.get(
            ("address_to_longitude_latitude", "address"), []
        )
        safe = [
            candidate.strip()
            for candidate in candidates
            if candidate.strip()
            and candidate.strip() != original
            and len(candidate.strip()) >= 4
            and candidate.strip() in original
        ]
        if not safe:
            return None
        recovered = max(safe, key=len)
        arguments["address"] = recovered
        return ParameterRecovery(
            "address", original, recovered, "address_to_longitude_latitude"
        )

    def recovered_create_attempt(self) -> RecoveredToolAttempt | None:
        """Replay the latest failed CREATE with only proven-bad input changed."""
        for attempt in reversed(self._failed_attempts):
            if attempt.role != ToolRole.CREATE:
                continue
            arguments = deepcopy(attempt.arguments)
            recovery = self.recover(attempt.tool_name, arguments, attempt.role)
            if recovery is None:
                continue
            if _signature(attempt.tool_name, arguments) in self._attempted_signatures:
                continue
            return RecoveredToolAttempt(attempt.tool_name, arguments, recovery)
        return None

    def failure_count(self, tool_name: str, arguments: dict[str, Any]) -> int:
        return self._signature_failures.get(_signature(tool_name, arguments), 0)

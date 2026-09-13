"""Bounded parameter recovery for tool failures, independent of any agent loop.

Extracted from the frozen ADAPT controller's ``runtime/tool_errors.py`` because it
is the one piece there that is a **guard-rail rather than a policy**: it never
decides anything for the model. It only refuses to repeat an argument the
environment has already proven bad, and offers a proven-good alternative.

Two failure shapes it exists for, both recorded in the engineering log:

- E-043 / E-051: the environment cannot geocode a long address carrying a
  building/unit/room suffix, so the identical call fails forever;
- the stock skeleton has no memory of its own failed calls, so it retries them.

Everything here is derived from environment responses. Nothing reads rubrics,
rewards, target ids or target/distraction annotations, and no value is invented:
a recovery is only offered when a *successfully resolved* argument contains the
failed one.

Independence
------------
The original module imported ``runtime.tools.ToolRole``, which belongs to the
controller being retired. This version identifies a write by tool name using the
same convention as the rest of the codebase (``create_*`` / ``instore_book`` /
``instore_reservation``) and takes an explicit ``is_write`` flag, so it imports
nothing from the controller.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

WRITE_EXACT = frozenset({"instore_book", "instore_reservation"})

# Suffixes that make an address un-geocodable in this environment, and that a
# resolved prefix can safely replace.
_BUILDING_SUFFIX_RE = re.compile(
    r"(?:[0-9一二三四五六七八九十百]+)(?:号楼|栋|幢|座|单元|室).*$"
)


def is_write_tool(tool_name: str) -> bool:
    """True for tools that create an order/booking (not a payment)."""
    return (tool_name or "").startswith("create_") or tool_name in WRITE_EXACT


def normalized_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
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


def attempt_signature(tool_name: str, arguments: dict[str, Any]) -> str:
    """Public wrapper so callers can match a proposal against past attempts."""
    payload = json.dumps(
        normalized_arguments(arguments or {}),
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return f"{tool_name}:{payload}"


def classify_error(content: str) -> str:
    """Coarse error class; drives which recoveries are considered provable."""
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
    is_write: bool


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


class ToolFailureLedger:
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
        is_write: bool,
    ) -> None:
        attempt = ToolAttempt(
            tool_name,
            deepcopy(arguments or {}),
            attempt_signature(tool_name, arguments or {}),
            is_write,
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
            error_class = classify_error(content)
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

        Only explicit building/unit/room suffixes are removed. The returned prefix
        must still be proven by the environment before ``recover`` can use it, and
        each probe is attempted at most once per subtask.
        """
        failed_addresses = [
            value
            for (argument, value, error_class), count in self._argument_failures.items()
            if argument == "address"
            and error_class == "address_not_resolvable"
            and count > 0
        ]
        for original in reversed(failed_addresses):
            probe = _BUILDING_SUFFIX_RE.sub("", original).strip(" ,，;；")
            if len(probe) < 4 or probe == original or not original.startswith(probe):
                continue
            tool_name = "address_to_longitude_latitude"
            arguments = {"address": probe}
            if attempt_signature(tool_name, arguments) in self._attempted_signatures:
                continue
            return ParameterProbe(tool_name, "address", original, probe)
        return None

    def rejection_reason(
        self, tool_name: str, arguments: dict[str, Any], is_write: bool
    ) -> str:
        signature = attempt_signature(tool_name, arguments or {})
        failures = self._signature_failures.get(signature, 0)
        if failures >= self.max_identical_failures:
            return (
                f"identical tool signature already failed {failures} times; "
                "change the proven-bad argument or stop"
            )
        if is_write:
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
        self, tool_name: str, arguments: dict[str, Any], is_write: bool
    ) -> ParameterRecovery | None:
        """Recover only a proven-bad address from a proven-good contained prefix."""
        if not is_write or not isinstance(arguments, dict):
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
            if not attempt.is_write:
                continue
            arguments = deepcopy(attempt.arguments)
            recovery = self.recover(attempt.tool_name, arguments, attempt.is_write)
            if recovery is None:
                continue
            if attempt_signature(attempt.tool_name, arguments) in self._attempted_signatures:
                continue
            return RecoveredToolAttempt(attempt.tool_name, arguments, recovery)
        return None

    def failure_count(self, tool_name: str, arguments: dict[str, Any]) -> int:
        return self._signature_failures.get(attempt_signature(tool_name, arguments), 0)

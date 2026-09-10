"""Independent mechanical transaction safety for ADAPT V2."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field
from vita.data_model.message import ToolCall, ToolMessage
from vita.environment.toolkit import ToolType

from agent.v2.observation import ObservationStore

OperationStatus = Literal[
    "proposed", "validated", "executing", "succeeded", "failed", "compensated"
]
_DATE = re.compile(r"(?<!\d)(?:20\d{2})[-/]\d{1,2}[-/]\d{1,2}(?!\d)")
_RESULT_ID = re.compile(
    r"['\"]?([A-Za-z][A-Za-z0-9_]*_id)['\"]?\s*[=:]\s*['\"]?([^,'\"\s\)\]}]+)"
)


class OperationRecord(BaseModel):
    operation_id: str
    turn_id: int
    tool_call_id: str = ""
    tool_name: str
    argument_hash: str
    candidate_snapshot_version: int
    authorization_evidence_turn: int | None = None
    status: OperationStatus = "proposed"
    result_entity_ids: list[str] = Field(default_factory=list)
    retry_of: str | None = None
    compensation_for: str | None = None
    error: str | None = None
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class ValidationResult(BaseModel):
    valid: bool
    problems: list[str] = Field(default_factory=list)
    operation: OperationRecord | None = None


class OperationJournal:
    def __init__(self) -> None:
        self.records: list[OperationRecord] = []
        self._by_call_id: dict[str, OperationRecord] = {}

    def recent(self, limit: int = 8) -> list[dict[str, Any]]:
        return [item.model_dump(mode="json") for item in self.records[-limit:]]

    def find_signature(self, tool_name: str, argument_hash: str) -> list[OperationRecord]:
        return [
            item
            for item in self.records
            if item.tool_name == tool_name and item.argument_hash == argument_hash
        ]

    def append(self, record: OperationRecord) -> OperationRecord:
        self.records.append(record)
        if record.tool_call_id:
            self._by_call_id[record.tool_call_id] = record
        return record

    def observe_result(self, message: ToolMessage, turn_id: int) -> OperationRecord | None:
        record = self._by_call_id.get(message.id)
        if record is None:
            return None
        record.status = "failed" if message.error else "succeeded"
        if message.error:
            record.error = str(message.content or "tool execution failed")
        record.result_entity_ids = [value for _, value in _RESULT_ID.findall(str(message.content or ""))]
        return record


class TransactionKernel:
    """Validate only observable invariants; never rank or choose candidates."""

    def __init__(self, observations: ObservationStore, journal: OperationJournal) -> None:
        self.observations = observations
        self.journal = journal
        self.write_tools: set[str] = set()
        self.payment_tools: set[str] = set()
        self.current_order_ids: dict[str, int] = {}
        self.explicit_dates: set[str] = set()
        self.explicit_addresses: set[str] = set()

    def configure_tools(self, tools: list[Any]) -> None:
        self.write_tools.clear()
        self.payment_tools.clear()
        for tool in tools or []:
            name = str(getattr(tool, "name", ""))
            function = getattr(tool, "_func", None)
            tool_type = getattr(function, "__tool_type__", None)
            structural_write = name.startswith(("create_", "pay_", "cancel_", "modify_"))
            structural_write = structural_write or any(
                marker in name for marker in ("_book", "_reservation")
            )
            if tool_type == ToolType.WRITE or structural_write:
                self.write_tools.add(name)
            if name.startswith("pay_") or "payment" in name.lower():
                self.payment_tools.add(name)

    def begin_instruction(self, instruction: str, user_profile: dict[str, Any]) -> None:
        self.explicit_dates = set(_DATE.findall(instruction or ""))
        self.explicit_addresses.clear()
        self._collect_explicit_addresses(user_profile, instruction or "")

    def observe_user_turn(self, content: str, user_profile: dict[str, Any]) -> None:
        """Let explicit current-turn corrections supersede the initial values."""
        dates = set(_DATE.findall(content or ""))
        if dates:
            self.explicit_dates = dates
        previous_addresses = set(self.explicit_addresses)
        self.explicit_addresses.clear()
        self._collect_explicit_addresses(user_profile, content or "")
        if not self.explicit_addresses:
            self.explicit_addresses = previous_addresses

    def _collect_explicit_addresses(self, value: Any, instruction: str) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                if "address" in str(key).lower() and isinstance(nested, str):
                    if nested and nested in instruction:
                        self.explicit_addresses.add(nested)
                else:
                    self._collect_explicit_addresses(nested, instruction)
        elif isinstance(value, list):
            for nested in value:
                self._collect_explicit_addresses(nested, instruction)

    def is_write(self, tool_name: str) -> bool:
        return tool_name in self.write_tools

    def validate(self, call: ToolCall, turn_id: int, *, commit: bool) -> ValidationResult:
        if not self.is_write(call.name):
            return ValidationResult(valid=True)
        arguments = call.arguments or {}
        argument_hash = hashlib.sha256(
            json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        prior = self.journal.find_signature(call.name, argument_hash)
        problems: list[str] = []
        if any(item.status in {"validated", "executing", "succeeded"} for item in prior):
            problems.append("duplicate operation: identical write is already validated or completed")

        candidate_ids = self._candidate_argument_ids(arguments)
        for argument, candidate_id in candidate_ids:
            observation = self.observations.latest(candidate_id)
            if observation is None:
                problems.append(f"{argument}={candidate_id} is not in the current candidate snapshot")
            elif observation.inventory_state == "unavailable":
                problems.append(f"{argument}={candidate_id} has observed zero inventory")
        for index, (first_field, first) in enumerate(candidate_ids):
            for second_field, second in candidate_ids[index + 1 :]:
                if first_field == second_field:
                    # Multiple children in the same list do not need to be
                    # parent/child of one another.
                    continue
                relation_is_observed = self.observations.relation_observed_between_fields(
                    first_field, second_field
                )
                if (
                    relation_is_observed
                    and first != second
                    and not self.observations.co_observed(first, second)
                ):
                    problems.append(f"candidate IDs {first} and {second} were not observed as a parent-child pair")

        for key, value in arguments.items():
            lowered = str(key).lower()
            rendered = str(value)
            if self.explicit_dates and ("date" in lowered or "time" in lowered):
                normalized = rendered.replace("/", "-")
                expected = {item.replace("/", "-") for item in self.explicit_dates}
                if not any(item in normalized for item in expected):
                    problems.append(f"{key} changes an explicit user date")
            if (
                self.explicit_addresses
                and "address" in lowered
                and rendered not in self.explicit_addresses
            ):
                problems.append(f"{key} changes an explicit user address")

        authorization_turn: int | None = None
        if call.name in self.payment_tools:
            order_ids = self._argument_values(arguments, "order_id")
            if not order_ids:
                problems.append("payment must identify the current order")
            for order_id in order_ids:
                created_turn = self.current_order_ids.get(order_id)
                if created_turn is None:
                    problems.append(f"payment order {order_id} is not the current observed order")
                elif turn_id <= created_turn:
                    problems.append("payment authorization must come from a later user turn")
                else:
                    authorization_turn = turn_id

        retry_of = next((item.operation_id for item in reversed(prior) if item.status == "failed"), None)
        record = OperationRecord(
            operation_id="op-" + uuid.uuid4().hex,
            turn_id=turn_id,
            tool_call_id=call.id,
            tool_name=call.name,
            argument_hash=argument_hash,
            candidate_snapshot_version=self.observations.snapshot_version,
            authorization_evidence_turn=authorization_turn,
            retry_of=retry_of,
        )
        if problems:
            return ValidationResult(valid=False, problems=sorted(set(problems)), operation=record)
        if commit:
            record.status = "executing"
            self.journal.append(record)
        return ValidationResult(valid=True, operation=record)

    def observe_result(self, message: ToolMessage, turn_id: int) -> None:
        record = self.journal.observe_result(message, turn_id)
        if record and not message.error:
            for key, value in _RESULT_ID.findall(str(message.content or "")):
                if key.lower() == "order_id":
                    self.current_order_ids[value] = turn_id

    def mark_compensated(
        self, operation_id: str, *, result_entity_ids: list[str] | None = None
    ) -> OperationRecord:
        """Record externally confirmed rollback/compensation of a write."""
        record = next(
            (item for item in self.journal.records if item.operation_id == operation_id),
            None,
        )
        if record is None:
            raise ValueError(f"Unknown operation id: {operation_id}")
        record.status = "compensated"
        if result_entity_ids is not None:
            record.result_entity_ids = list(result_entity_ids)
        return record

    @staticmethod
    def _argument_values(arguments: dict[str, Any], key_name: str) -> list[str]:
        result: list[str] = []
        for key, value in arguments.items():
            if str(key).lower() != key_name:
                continue
            values = value if isinstance(value, list) else [value]
            result.extend(str(item) for item in values if item is not None)
        return result

    @staticmethod
    def _candidate_argument_ids(arguments: dict[str, Any]) -> list[tuple[str, str]]:
        excluded = {"user_id", "order_id", "address_id", "payment_id"}
        result: list[tuple[str, str]] = []
        for key, value in arguments.items():
            lowered = str(key).lower()
            if lowered in excluded or not lowered.endswith(("_id", "_ids")):
                continue
            values = value if isinstance(value, list) else [value]
            result.extend((str(key), str(item)) for item in values if item is not None)
        return result

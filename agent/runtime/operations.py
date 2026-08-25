"""Per-subtask idempotency journal for irreversible operations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


def _signature(tool_name: str, arguments: dict[str, Any]) -> str:
    return f"{tool_name}:{json.dumps(arguments or {}, ensure_ascii=False, sort_keys=True, default=str)}"


@dataclass(frozen=True)
class OperationRecord:
    epoch: int
    role: str
    tool_name: str
    signature: str
    succeeded: bool = False
    failed: bool = False


class OperationJournal:
    """Guarantee at-most-once effects in one user-intent epoch.

    Failed proposals remain retryable with corrected arguments.  A new epoch
    must be opened explicitly by a later user transaction/revision; ordinary
    acknowledgements, status reads and model replans never reset idempotency.
    """

    def __init__(self) -> None:
        self.epoch = 1
        self._records: list[OperationRecord] = []
        self._pending: dict[str, OperationRecord] = {}

    def reset(self) -> None:
        self.epoch = 1
        self._records.clear()
        self._pending.clear()

    def begin_new_epoch(self) -> int:
        self.epoch += 1
        self._pending.clear()
        return self.epoch

    def successful(self, role: str) -> bool:
        return any(
            record.epoch == self.epoch
            and record.role == role
            and record.succeeded
            for record in self._records
        )

    def validate(
        self,
        role: str,
        tool_name: str = "",
        arguments: dict[str, Any] | None = None,
    ) -> list[str]:
        irreversible = {"create", "pay", "cancel", "modify"}
        if role in irreversible and self.successful(role):
            return [
                f"{role.upper()} already succeeded for the current user-intent epoch; "
                "do not repeat the irreversible effect"
            ]
        if role in irreversible and tool_name and arguments is not None:
            signature = _signature(tool_name, arguments)
            if any(
                record.epoch == self.epoch
                and record.role == role
                and record.signature == signature
                and record.failed
                for record in self._records
            ):
                return [
                    f"identical failed {role.upper()} call must be corrected before retry"
                ]
        return []

    def register(
        self,
        call_id: str | None,
        role: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> None:
        record = OperationRecord(
            self.epoch, role, tool_name, _signature(tool_name, arguments)
        )
        self._records.append(record)
        if call_id:
            self._pending[str(call_id)] = record

    def observe_result(
        self,
        call_id: str | None,
        tool_name: str,
        role: str,
        error: bool,
    ) -> None:
        record = self._pending.pop(str(call_id), None) if call_id else None
        if record is None:
            record = next(
                (
                    item
                    for item in reversed(self._records)
                    if item.epoch == self.epoch
                    and item.tool_name == tool_name
                    and not item.succeeded
                ),
                None,
            )
        if record is None:
            return
        if error:
            updated = OperationRecord(
                record.epoch,
                role or record.role,
                record.tool_name,
                record.signature,
                succeeded=False,
                failed=True,
            )
            self._records[self._records.index(record)] = updated
            return
        updated = OperationRecord(
            record.epoch,
            role or record.role,
            record.tool_name,
            record.signature,
            succeeded=True,
            failed=False,
        )
        self._records[self._records.index(record)] = updated

    def records(self) -> tuple[OperationRecord, ...]:
        return tuple(self._records)

"""Call provenance ledger for ID-bearing tool invocations."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable


@dataclass(frozen=True)
class CallLineageRecord:
    call_id: str
    instruction_epoch: int
    tool_epoch: int
    operation_epoch: int
    tool_name: str
    tool_role: str
    input_ids: tuple[str, ...]
    output_candidate_ids: tuple[str, ...] = ()
    output_state_ids: tuple[str, ...] = ()
    succeeded: bool | None = None


class CallLineageLedger:
    """Record and validate tool-call lineage without consulting hidden state."""

    def __init__(self) -> None:
        self._records: dict[str, CallLineageRecord] = {}

    def reset(self) -> None:
        self._records.clear()

    def register(
        self,
        *,
        call_id: str,
        instruction_epoch: int,
        tool_epoch: int,
        operation_epoch: int,
        tool_name: str,
        tool_role: str,
        input_ids: Iterable[str] = (),
    ) -> CallLineageRecord:
        record = CallLineageRecord(
            call_id=str(call_id),
            instruction_epoch=int(instruction_epoch),
            tool_epoch=int(tool_epoch),
            operation_epoch=int(operation_epoch),
            tool_name=tool_name,
            tool_role=tool_role,
            input_ids=tuple(dict.fromkeys(str(value) for value in input_ids)),
        )
        self._records[record.call_id] = record
        return record

    def observe_result(
        self,
        *,
        call_id: str,
        tool_name: str,
        instruction_epoch: int,
        tool_epoch: int,
        succeeded: bool,
        output_candidate_ids: Iterable[str] = (),
        output_state_ids: Iterable[str] = (),
    ) -> tuple[CallLineageRecord | None, tuple[str, ...]]:
        record = self._records.get(str(call_id))
        failures: list[str] = []
        if record is None:
            return None, ("tool result has no registered call lineage",)
        if record.tool_name != tool_name:
            failures.append("tool result name does not match its call")
        if record.instruction_epoch != int(instruction_epoch):
            failures.append("tool result belongs to a stale instruction epoch")
        if record.tool_epoch != int(tool_epoch):
            failures.append("tool result belongs to a stale tool epoch")
        if failures:
            return record, tuple(failures)
        updated = replace(
            record,
            succeeded=bool(succeeded),
            output_candidate_ids=tuple(
                dict.fromkeys(str(value) for value in output_candidate_ids)
            ),
            output_state_ids=tuple(
                dict.fromkeys(str(value) for value in output_state_ids)
            ),
        )
        self._records[record.call_id] = updated
        return updated, ()

    def known_candidate_ids(self, *, instruction_epoch: int) -> set[str]:
        return {
            candidate_id
            for record in self._records.values()
            if record.instruction_epoch == instruction_epoch and record.succeeded
            for candidate_id in record.output_candidate_ids
        }

    def known_state_ids(
        self, *, instruction_epoch: int, operation_epoch: int
    ) -> set[str]:
        return {
            state_id
            for record in self._records.values()
            if record.instruction_epoch == instruction_epoch
            and record.operation_epoch == operation_epoch
            and record.succeeded
            for state_id in record.output_state_ids
        }

    def validate_inputs(
        self,
        *,
        tool_role: str,
        input_ids: Iterable[str],
        instruction_epoch: int,
        operation_epoch: int,
        profile_ids: Iterable[str] = (),
    ) -> tuple[str, ...]:
        ids = {str(value) for value in input_ids}
        if not ids:
            return ()
        candidates = self.known_candidate_ids(instruction_epoch=instruction_epoch)
        states = self.known_state_ids(
            instruction_epoch=instruction_epoch,
            operation_epoch=operation_epoch,
        )
        profiles = {str(value) for value in profile_ids}
        if tool_role in {"search", "utility", "read"}:
            allowed = candidates | states | profiles
        elif tool_role in {"enrich", "create"}:
            allowed = candidates | profiles
        elif tool_role == "state_read":
            allowed = states | profiles
        elif tool_role in {"pay", "cancel", "modify"}:
            allowed = states
        else:
            allowed = candidates | states | profiles
        return tuple(
            f"{candidate_id} has no valid {tool_role} lineage in the current epoch"
            for candidate_id in sorted(ids - allowed)
        )

    def records(self) -> tuple[CallLineageRecord, ...]:
        return tuple(self._records.values())

"""Transactional boundary for model and framework proposals."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from vita.data_model.message import AssistantMessage, ToolCall


@dataclass
class ActionTransaction:
    """One ``propose -> prepare -> validate -> emit -> commit`` lifecycle."""

    proposed: AssistantMessage
    prepared: AssistantMessage
    issues: tuple[str, ...] = ()
    emitted: bool = False
    committed: bool = False

    @classmethod
    def prepare(
        cls,
        proposal: AssistantMessage,
        prepare_call: Callable[[ToolCall], ToolCall] | None = None,
    ) -> "ActionTransaction":
        calls = [
            (
                prepare_call(call)
                if prepare_call is not None
                else ToolCall(
                    id=call.id,
                    name=call.name,
                    arguments=dict(call.arguments or {}),
                )
            )
            for call in (proposal.tool_calls or [])
        ]
        prepared = AssistantMessage(
            role="assistant",
            content=proposal.content,
            tool_calls=calls or None,
        )
        return cls(proposed=proposal, prepared=prepared)

    def validate(self, validator: Callable[[AssistantMessage], list[str]]) -> bool:
        self.issues = tuple(dict.fromkeys(validator(self.prepared)))
        return not self.issues

    def emit(self, messages: list) -> None:
        if self.issues:
            raise ValueError("cannot emit a rejected action transaction")
        if not self.emitted:
            messages.append(self.prepared)
            self.emitted = True

    def commit(self, callback: Callable[[AssistantMessage], None]) -> None:
        if not self.emitted:
            raise ValueError("cannot commit a proposal that was not emitted")
        if not self.committed:
            callback(self.prepared)
            self.committed = True


@dataclass(frozen=True)
class ReplanContext:
    """Ephemeral validation feedback; never appended to persistent dialogue."""

    issues: tuple[str, ...] = field(default_factory=tuple)

    def render(self) -> str:
        if not self.issues:
            return ""
        return "ADAPT internal validation rejected the previous proposal:\n- " + "\n- ".join(
            self.issues
        )

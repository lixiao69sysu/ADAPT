"""Validate tool-call drafts using only information visible to the agent."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from vita.data_model.message import (
    MultiToolMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from vita.environment.tool import Tool


_ID_TOKEN = re.compile(
    r"\b(?=[A-Za-z0-9#_-]{5,}\b)(?=[A-Za-z0-9#_-]*\d)"
    r"[A-Za-z][A-Za-z0-9#_-]*\b"
)


def _is_id_field(name: str | None) -> bool:
    return bool(name) and (
        name == "id" or name.endswith("_id") or name.endswith("_ids")
    )


def _collect_structured_ids(
    value: Any,
    field_name: str | None = None,
) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            found.update(_collect_structured_ids(child, str(key)))
    elif isinstance(value, list):
        for child in value:
            found.update(_collect_structured_ids(child, field_name))
    elif isinstance(value, str) and _is_id_field(field_name):
        found.add(value)
    return found


@dataclass
class EntityLedger:
    """IDs established by user messages or successful public tool results."""

    known_ids: set[str] = field(default_factory=set)
    entity_groups: list[set[str]] = field(default_factory=list)

    def _remember_group(self, ids: set[str]) -> None:
        if ids:
            self.known_ids.update(ids)
        if len(ids) > 1 and ids not in self.entity_groups:
            self.entity_groups.append(ids)

    def observe(self, message: object) -> None:
        if isinstance(message, MultiToolMessage):
            for tool_message in message.tool_messages:
                self.observe(tool_message)
            return

        if isinstance(message, ToolMessage):
            if message.error or not message.content:
                return
            try:
                payload = json.loads(message.content)
            except (TypeError, json.JSONDecodeError):
                payload = None
            if payload is not None:
                self._remember_group(_collect_structured_ids(payload))
            for line in message.content.splitlines() or [message.content]:
                self._remember_group(set(_ID_TOKEN.findall(line)))
            return

        if isinstance(message, UserMessage) and message.content:
            self.known_ids.update(_ID_TOKEN.findall(message.content))

    def knows(self, value: object) -> bool:
        return isinstance(value, str) and value in self.known_ids

    def co_occurs(self, first: str, second: str) -> bool:
        return any({first, second}.issubset(group) for group in self.entity_groups)


@dataclass(frozen=True)
class ValidationIssue:
    """A reason an Agent-generated tool-call draft is unsafe to execute."""

    code: str
    message: str


def _iter_id_arguments(
    value: Any,
    field_name: str | None = None,
):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _iter_id_arguments(child, str(key))
    elif isinstance(value, list):
        for child in value:
            yield from _iter_id_arguments(child, field_name)
    elif (
        isinstance(value, str)
        and _is_id_field(field_name)
        and field_name != "user_id"
    ):
        yield field_name, value


class ToolGuard:
    """Validate drafts against the tools and entities visible this subtask."""

    def __init__(self, tools: list[Tool] | None = None):
        self.update_tools(tools or [])

    def update_tools(self, tools: list[Tool]) -> None:
        self._tools = {tool.name: tool for tool in tools}

    def validate(
        self,
        call: ToolCall,
        ledger: EntityLedger,
    ) -> list[ValidationIssue]:
        tool = self._tools.get(call.name)
        if tool is None:
            return [
                ValidationIssue(
                    "unknown_tool",
                    f"工具 {call.name} 不在当前领域工具列表中",
                )
            ]

        try:
            tool.params.model_validate(call.arguments)
        except Exception as exc:
            return [
                ValidationIssue(
                    "invalid_arguments",
                    f"工具 {call.name} 参数不符合 schema: {exc}",
                )
            ]

        issues = []
        id_arguments = list(_iter_id_arguments(call.arguments))
        for field_name, value in id_arguments:
            if not ledger.knows(value):
                issues.append(
                    ValidationIssue(
                        "unknown_entity_id",
                        f"{field_name}={value!r} 未出现在用户消息或成功工具返回中",
                    )
                )
        primary = next(
            (
                value
                for field_name, value in id_arguments
                if field_name in {"store_id", "shop_id", "hotel_id", "flight_id"}
            ),
            None,
        )
        if primary and ledger.entity_groups:
            for field_name, value in id_arguments:
                if value == primary or field_name == "order_id":
                    continue
                if not ledger.co_occurs(primary, value):
                    logger.warning(
                        "ADAPT_CROSS_ENTITY_WARNING field={} value={} primary={}",
                        field_name,
                        value,
                        primary,
                    )
        return issues

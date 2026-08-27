"""Bind task/profile values to observable tool argument schemas.

The resolver is deliberately value-agnostic about benchmark entities.  It
matches semantic task slots to argument names exposed by the current tool and
never invents candidate or workflow IDs.
"""

from __future__ import annotations

import re
from typing import Any

from agent.decision import (
    ConstraintOperator,
    ConstraintTarget,
    TaskSpec,
    resolve_profile_address,
)

_ALIASES: dict[str, tuple[str, ...]] = {
    "address": ("address", "delivery_address", "location"),
    "date": (
        "date",
        "new_date",
        "booking_date",
        "check_in_date",
        "departure_date",
    ),
    "departure": ("departure", "departure_city", "from_city", "start_city"),
    "destination": ("destination", "arrival_city", "to_city", "end_city"),
    "party_size": ("party_size", "customer_count", "guest_count", "people_count"),
    "quantity": ("quantity", "count", "ticket_count", "room_count", "product_cnts"),
    "time": ("time", "booking_time", "reservation_time", "appointment_time"),
    "room_type": ("room_type",),
    "size": ("size",),
}


class ArgumentBindingResolver:
    """Compile deterministic, planner-owned non-ID arguments for one tool."""

    @classmethod
    def bind(
        cls,
        contract: Any,
        spec: TaskSpec,
        resolved_slots: dict[str, Any],
        profile: dict[str, Any],
    ) -> dict[str, Any]:
        arguments = {argument.name: argument for argument in contract.arguments}
        bound: dict[str, Any] = {}

        # Explicit task constraints resolve before generic slot copies so a
        # profile alias such as ``home`` cannot leak into the environment.
        for constraint in (*spec.must, *spec.avoid):
            if constraint.target != ConstraintTarget.ARGUMENT:
                continue
            names = cls._argument_names(constraint.kind, constraint.argument_name)
            argument_name = next(
                (name for name in names if name in arguments and name not in bound),
                "",
            )
            if not argument_name:
                continue
            value: Any = constraint.value
            if constraint.operator == ConstraintOperator.RESOLVES_PROFILE:
                value = resolve_profile_address(profile, constraint.value)
            if value not in (None, ""):
                bound[argument_name] = cls._coerce(
                    value,
                    arguments[argument_name].json_schema,
                    argument_name,
                )

        for name, value in resolved_slots.items():
            if (
                name in arguments
                and name not in bound
                and value not in (None, "", "__delegated__")
            ):
                bound[name] = cls._coerce(
                    value, arguments[name].json_schema, name
                )

        for slot, value in resolved_slots.items():
            if value in (None, "", "__delegated__"):
                continue
            names = cls._argument_names(slot, slot)
            argument_name = next(
                (name for name in names if name in arguments and name not in bound),
                "",
            )
            if argument_name:
                bound[argument_name] = cls._coerce(
                    value,
                    arguments[argument_name].json_schema,
                    argument_name,
                )
        return bound

    @classmethod
    def normalize_arguments(
        cls, contract: Any, values: dict[str, Any]
    ) -> dict[str, Any]:
        """Return a schema-shaped copy of model or planner arguments.

        Normalization is deliberately structural.  It uses only the current
        tool's public JSON schema and recursively coerces array items, so a
        value such as ``["1"]`` becomes ``[1]`` for ``array[integer]``.
        Unknown fields are preserved for the normal preflight/tool validator.
        """
        arguments = {argument.name: argument for argument in contract.arguments}
        return {
            name: cls._coerce(
                value,
                arguments[name].json_schema if name in arguments else {},
                name,
            )
            for name, value in dict(values or {}).items()
        }

    @classmethod
    def type_errors(cls, contract: Any, values: dict[str, Any]) -> list[str]:
        """Describe values that still violate the observable schema shape."""
        arguments = {argument.name: argument for argument in contract.arguments}
        errors: list[str] = []
        for name, value in values.items():
            argument = arguments.get(name)
            if argument is None:
                continue
            cls._collect_type_errors(value, argument.json_schema, name, errors)
        return errors

    @staticmethod
    def _argument_names(kind: str, explicit: str) -> tuple[str, ...]:
        values = (explicit,) if explicit else ()
        return tuple(dict.fromkeys((*values, *_ALIASES.get(kind, (kind,)))))

    @classmethod
    def _coerce(
        cls, value: Any, schema: dict[str, Any], argument_name: str
    ) -> Any:
        effective = cls._effective_schema(schema, value)
        json_type = str(effective.get("type", "") or "")
        normalized = value
        if json_type == "array":
            values = normalized if isinstance(normalized, list) else [normalized]
            item_schema = effective.get("items", {})
            return [
                cls._coerce(item, item_schema, f"{argument_name}[{index}]")
                for index, item in enumerate(values)
            ]
        if json_type == "object" and isinstance(normalized, dict):
            properties = effective.get("properties", {})
            return {
                key: cls._coerce(
                    item,
                    properties.get(key, {}),
                    f"{argument_name}.{key}",
                )
                for key, item in normalized.items()
            }
        if isinstance(normalized, str):
            number = re.fullmatch(
                r"\s*(-?\d+(?:\.\d+)?)\s*(?:人|位|张|份|个|间|杯|件|双|瓶)?\s*",
                normalized,
            )
            chinese_number = re.fullmatch(
                r"\s*([零〇一二两三四五六七八九十百]+)\s*"
                r"(?:人|位|张|份|个|间|杯|件|双|瓶)?\s*",
                normalized,
            )
            if json_type == "integer" and number:
                normalized = int(float(number.group(1)))
            elif json_type == "integer" and chinese_number:
                normalized = cls._chinese_integer(chinese_number.group(1))
            elif json_type == "number" and number:
                normalized = float(number.group(1))
            elif json_type == "number" and chinese_number:
                normalized = float(cls._chinese_integer(chinese_number.group(1)))
            elif json_type == "boolean" and normalized.casefold() in {
                "true", "false"
            }:
                normalized = normalized.casefold() == "true"
        # Legacy VitaBench order APIs expose parallel quantity arrays without
        # declaring an array type consistently.  The plural schema name is
        # sufficient structural evidence; no product vocabulary is involved.
        root_name = argument_name.split("[", 1)[0]
        if root_name.endswith(("_cnts", "_counts")) and not schema:
            values = normalized if isinstance(normalized, list) else [normalized]
            normalized = [
                int(item) if str(item).isdigit() else item for item in values
            ]
        return normalized

    @staticmethod
    def _chinese_integer(value: str) -> int:
        digits = {
            "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2,
            "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
            "八": 8, "九": 9,
        }
        total = 0
        current = 0
        for char in value:
            if char in digits:
                current = digits[char]
            elif char == "十":
                total += (current or 1) * 10
                current = 0
            elif char == "百":
                total += (current or 1) * 100
                current = 0
        return total + current

    @classmethod
    def _effective_schema(
        cls, schema: dict[str, Any], value: Any
    ) -> dict[str, Any]:
        options = schema.get("anyOf") or schema.get("oneOf") or ()
        if not options:
            return schema
        if value is None:
            return next(
                (option for option in options if option.get("type") == "null"),
                schema,
            )
        return next(
            (option for option in options if option.get("type") != "null"),
            schema,
        )

    @classmethod
    def _collect_type_errors(
        cls,
        value: Any,
        schema: dict[str, Any],
        path: str,
        errors: list[str],
    ) -> None:
        effective = cls._effective_schema(schema, value)
        json_type = str(effective.get("type", "") or "")
        expected = {
            "array": list,
            "object": dict,
            "integer": int,
            "number": (int, float),
            "string": str,
            "boolean": bool,
            "null": type(None),
        }.get(json_type)
        if expected is not None and (
            not isinstance(value, expected)
            or (json_type == "integer" and isinstance(value, bool))
        ):
            errors.append(f"{path} expects {json_type}, got {type(value).__name__}")
            return
        if json_type == "array" and isinstance(value, list):
            item_schema = effective.get("items", {})
            for index, item in enumerate(value):
                cls._collect_type_errors(
                    item, item_schema, f"{path}[{index}]", errors
                )
        elif json_type == "object" and isinstance(value, dict):
            properties = effective.get("properties", {})
            for key, item in value.items():
                cls._collect_type_errors(
                    item, properties.get(key, {}), f"{path}.{key}", errors
                )

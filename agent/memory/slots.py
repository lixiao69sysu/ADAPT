"""Shared typed-slot resolution for retrieval, asking, and runtime state.

Only stable preference-choice fields are resolved from history.  Task-instance
fields such as date, route, quantity, address, and reservation time must still
come from the current instruction or a current-session answer.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from typing import TYPE_CHECKING

from agent.memory.facts import PreferenceFact

if TYPE_CHECKING:
    from agent.decision import TaskSpec


_LOCAL_SCOPES = {"delivery", "instore", "local_commerce"}
_RESOLVABLE_PREFERENCE_SLOTS = {
    "room_type", "transport", "taste", "caffeine", "size", "budget",
}
_CANONICAL_MARKERS: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "room_type": (
        ("双床房", ("双床",)),
        ("大床房", ("大床",)),
        ("亲子房", ("亲子",)),
        ("套房", ("套房",)),
        ("海景房", ("海景",)),
        ("标准间", ("标准间",)),
    ),
    "transport": (
        ("高铁", ("高铁", "动车")),
        ("飞机", ("飞机", "航班", "机票")),
        ("火车", ("火车",)),
    ),
    "taste": (
        ("麻辣", ("麻辣", "香辣", "辣锅", "红油")),
        ("菌汤", ("菌汤", "菌菇")),
        ("番茄", ("番茄",)),
        ("清淡", ("清淡", "清汤")),
    ),
    "caffeine": (
        ("低咖啡因", ("低咖啡因", "脱因", "无咖啡因")),
        ("高咖啡因", ("高咖啡因", "双份浓缩", "提神")),
    ),
    "budget": (
        ("经济型", ("经济型", "性价比", "价格敏感")),
    ),
}
_SIZE_RE = re.compile(r"\b(3[4-9]|4[0-9]|5[0-2])(?:\s*[-~到]\s*(3[4-9]|4[0-9]|5[0-2]))?\s*码?")


def fact_relevant_to_task(fact: PreferenceFact, spec: TaskSpec) -> bool:
    """Conservative structural relevance shared by slot resolution."""
    if fact.status != "active":
        return False
    scope_match = fact.scope == spec.domain or (
        spec.domain in _LOCAL_SCOPES and fact.scope in _LOCAL_SCOPES
    )
    if scope_match and fact.facet in {spec.facet, "general", "travel", "service"}:
        return True
    if fact.scope == "general" and fact.facet == "general":
        return fact.dimension in _RESOLVABLE_PREFERENCE_SLOTS | {"safety", "attribute"}
    return fact.dimension == "safety" and spec.facet in {"restaurant", "beverage"}


def semantic_slot_value(fact: PreferenceFact, slot: str) -> str:
    """Project open-world fact text onto a typed field when evidence supports it."""
    if slot not in _RESOLVABLE_PREFERENCE_SLOTS:
        return ""
    value = (fact.value or "").strip()
    if not value:
        return ""
    if slot == "size":
        match = _SIZE_RE.search(value)
        return match.group(0).rstrip("码") if match else ""
    if fact.dimension not in {slot, "product", "explicit", "attribute", "like"}:
        return ""
    for canonical, markers in _CANONICAL_MARKERS.get(slot, ()):
        if any(marker in value for marker in markers):
            return canonical
    # A typed fact may carry an open-world value absent from the canonical
    # marker set. Preserve it, while fallback dimensions require field evidence.
    return value[:80] if fact.dimension == slot else ""


def resolve_preference_slots(
    spec: TaskSpec, facts: Iterable[PreferenceFact]
) -> dict[str, str]:
    """Resolve only unambiguous, strong, task-relevant preference slots.

    Multiple distinct active values keep the slot unresolved, so drift or a
    current-session clarification can decide it. Weak browse/search evidence
    never suppresses a necessary question.
    """
    values: dict[str, set[str]] = defaultdict(set)
    for fact in facts:
        if (
            fact.polarity != "positive"
            or not fact.decision_eligible
            or not fact_relevant_to_task(fact, spec)
        ):
            continue
        for slot in _RESOLVABLE_PREFERENCE_SLOTS:
            value = semantic_slot_value(fact, slot)
            if value:
                values[slot].add(value)
    resolved = {
        slot: next(iter(slot_values))
        for slot, slot_values in values.items()
        if len(slot_values) == 1
    }
    # Explicit current-task values always outrank historical resolution.
    for slot in spec.resolved_slots:
        resolved.pop(slot, None)
    return resolved


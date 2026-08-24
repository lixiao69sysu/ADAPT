"""Tool schema registry and phase-based action filtering."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from agent.decision import CandidateLedger, is_search_tool
from agent.runtime.state import RuntimePhase, TaskRuntime


class ToolRole(str, Enum):
    SEARCH = "search"
    READ = "read"
    CREATE = "create"
    PAY = "pay"
    CANCEL = "cancel"
    MODIFY = "modify"
    MEMORY = "memory"


@dataclass
class ToolMeta:
    name: str
    role: ToolRole
    required_arguments: set[str] = field(default_factory=set)
    id_arguments: dict[str, str] = field(default_factory=dict)
    state_effect: str = ""


class ToolRegistry:
    def __init__(self) -> None:
        self.tools: list[Any] = []
        self.meta: dict[str, ToolMeta] = {}

    def rebuild(self, tools: list[Any]) -> None:
        self.tools = list(tools or [])
        self.meta = {tool.name: self._inspect(tool) for tool in self.tools}

    def _inspect(self, tool: Any) -> ToolMeta:
        name = tool.name
        if name.startswith("pay_"):
            role = ToolRole.PAY
        elif name.startswith("create_") or name in {
            "instore_book",
            "instore_reservation",
        }:
            role = ToolRole.CREATE
        elif "cancel" in name:
            role = ToolRole.CANCEL
        elif "modify" in name or "change" in name:
            role = ToolRole.MODIFY
        elif name in {
            "suggest_question_tool",
            "record_preference_answer",
            "query_preference_memory",
        }:
            role = ToolRole.MEMORY
        elif is_search_tool(name):
            role = ToolRole.SEARCH
        else:
            role = ToolRole.READ
        required: set[str] = set()
        id_arguments: dict[str, str] = {}
        try:
            schema = tool.params.model_json_schema()
            required = set(schema.get("required", []))
            for key in schema.get("properties", {}):
                if key.endswith(("_id", "_ids")):
                    id_arguments[key] = key.removesuffix("_ids").removesuffix("_id")
        except (AttributeError, TypeError, ValueError):
            pass
        effect = (
            "unpaid_order"
            if role == ToolRole.CREATE
            else ("paid_order" if role == ToolRole.PAY else "")
        )
        return ToolMeta(name, role, required, id_arguments, effect)

    def role(self, name: str) -> ToolRole:
        return self.meta.get(name, ToolMeta(name, ToolRole.READ)).role

    def domain_hint(self) -> str:
        """Infer the active environment from observable tool names.

        Tool topology is a stronger signal than a product-word dictionary:
        it correctly handles unseen services and goods without teaching the
        runtime their category names.
        """
        names = {name.casefold() for name in self.meta}
        if any("instore" in name for name in names):
            return "instore"
        if any(
            marker in name
            for name in names
            for marker in (
                "_ota_",
                "hotel",
                "flight",
                "train",
                "attraction",
                "taxi",
            )
        ):
            return "ota"
        if any("delivery" in name for name in names):
            return "delivery"
        return ""

    def execution_ready(self, ledger: CandidateLedger, card=None) -> bool:
        """Whether CREATE has required IDs and a compliant candidate.

        Observing an ID is insufficient when every visible candidate violates
        a MUST/AVOID constraint. Keep SELECT open for a bounded second search
        until the deterministic shortlist contains a usable candidate.
        """
        if card is not None and not ledger.shortlist(card, limit=1):
            return False
        semantic_types = {
            "room": "product",
            "ticket": "product",
            "seat": "product",
        }
        observed_types = {candidate.entity_type for candidate in ledger.candidates.values()}
        for meta in self.meta.values():
            if meta.role != ToolRole.CREATE:
                continue
            required_id_types = {
                semantic_types.get(kind, kind)
                for argument, kind in meta.id_arguments.items()
                if argument in meta.required_arguments and argument != "user_id"
            }
            if required_id_types and required_id_types.issubset(observed_types):
                if "hotel" in meta.name:
                    hotels = {
                        candidate.candidate_id
                        for candidate in ledger.candidates.values()
                        if candidate.entity_type == "hotel"
                    }
                    expanded_hotels = {
                        parent_id
                        for candidate in ledger.candidates.values()
                        if candidate.entity_type == "product"
                        for parent_id in candidate.parent_ids
                        if parent_id in hotels
                    }
                    # A single hotel's rooms cannot establish the best match
                    # across brand/location/style preferences. Expand a bounded
                    # set of parent hotels before exposing CREATE.
                    if len(expanded_hotels) < min(6, len(hotels)):
                        continue
                return True
        return False

    def allowed_tools(self, runtime: TaskRuntime, ledger: CandidateLedger) -> list[Any]:
        allowed: list[Any] = []
        for tool in self.tools:
            meta = self.meta[tool.name]
            if meta.role == ToolRole.MEMORY:
                # Questioning and answer storage are framework-internal.
                continue
            if meta.role == ToolRole.SEARCH and not ledger.search_allowed(tool.name):
                continue
            if runtime.phase == RuntimePhase.NEED_INFO:
                if meta.role in {ToolRole.READ}:
                    allowed.append(tool)
                continue
            if runtime.phase in {RuntimePhase.START, RuntimePhase.SEARCH}:
                if runtime.revision_requested and ledger.pending_payment_ids:
                    if meta.role in {ToolRole.CANCEL, ToolRole.READ}:
                        allowed.append(tool)
                elif meta.role in {ToolRole.SEARCH, ToolRole.READ}:
                    allowed.append(tool)
                continue
            if runtime.phase == RuntimePhase.SELECT:
                if meta.role in {ToolRole.SEARCH, ToolRole.READ}:
                    allowed.append(tool)
                continue
            if runtime.phase == RuntimePhase.READY_TO_CREATE:
                # A usable shortlist already exists and execution is
                # authorized. Observation tools here only reopen a settled
                # decision and let the policy model loop.
                if (
                    meta.role == ToolRole.CREATE
                    and runtime.authorization.create_authorized
                ):
                    allowed.append(tool)
                continue
            if runtime.phase == RuntimePhase.READY_TO_PAY:
                if (
                    meta.role == ToolRole.PAY
                    and runtime.authorization.pay_authorized
                    and not runtime.authorization.pay_declined
                    or meta.role == ToolRole.READ
                ):
                    allowed.append(tool)
                continue
            if runtime.phase not in {RuntimePhase.DONE, RuntimePhase.UNSATISFIABLE}:
                allowed.append(tool)
        return allowed

    def validate_required(self, tool_name: str, arguments: dict[str, Any]) -> list[str]:
        meta = self.meta.get(tool_name)
        if not meta:
            return [f"unknown tool: {tool_name}"]
        return [
            f"missing required argument: {key}"
            for key in meta.required_arguments
            if arguments.get(key) is None
            or arguments.get(key) == ""
            or arguments.get(key) == []
        ]

"""Tool schema registry and phase-based action filtering."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from agent.decision import CandidateLedger, is_search_tool
from agent.runtime.contracts import ToolContract, ToolContractCompiler
from agent.runtime.state import RuntimePhase, TaskRuntime


class ToolRole(str, Enum):
    SEARCH = "search"
    ENRICH = "enrich"
    STATE_READ = "state_read"
    UTILITY = "utility"
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
    observation_schema: "ObservationSchema | None" = None
    question_arguments: dict[str, str] = field(default_factory=dict)
    argument_schemas: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class ObservationSchema:
    """Role annotations for one candidate object in a tool result.

    Field names are deliberately opaque. The result JSON Schema declares
    their roles through ``x-adapt-role`` and the candidate node declares its
    entity type through ``x-adapt-entity``.
    """

    entity_type: str
    id_field: str
    name_field: str = ""
    inventory_field: str = ""
    price_field: str = ""
    parent_fields: tuple[str, ...] = ()
    attribute_fields: tuple[str, ...] = ()
    constraint_fields: tuple[str, ...] = ()


class ToolRegistry:
    def __init__(self) -> None:
        self.tools: list[Any] = []
        self.meta: dict[str, ToolMeta] = {}
        self.contracts: dict[str, ToolContract] = {}

    def rebuild(self, tools: list[Any]) -> None:
        self.tools = list(tools or [])
        self.meta = {tool.name: self._inspect(tool) for tool in self.tools}
        # Milestone-A shadow compiler: later runtime stages consume these
        # immutable contracts, while rebuilding them here makes no environment
        # call and does not change the current action policy.
        self.contracts = ToolContractCompiler.compile(self.meta.values())

    def _inspect(self, tool: Any) -> ToolMeta:
        name = tool.name
        info = getattr(tool, "info", {}) or {}
        required: set[str] = set()
        id_arguments: dict[str, str] = {}
        question_arguments: dict[str, str] = {}
        argument_text: list[str] = []
        argument_schemas: dict[str, dict[str, Any]] = {}
        try:
            schema = tool.params.model_json_schema()
            required = set(schema.get("required", []))
            for key, property_schema in schema.get("properties", {}).items():
                argument_schemas[key] = dict(property_schema)
                declared_entity = str(property_schema.get("x-adapt-entity", ""))
                declared_argument_role = str(property_schema.get("x-adapt-role", ""))
                if declared_entity:
                    id_arguments[key] = declared_entity
                elif declared_argument_role == "user_id":
                    id_arguments[key] = "user"
                elif key.endswith(("_id", "_ids")):
                    id_arguments[key] = key.removesuffix("_ids").removesuffix("_id")
                argument_text.extend(
                    (key.casefold(), str(property_schema.get("description", "")).casefold())
                )
                if property_schema.get("x-adapt-question"):
                    question_arguments[key] = str(
                        property_schema.get("x-adapt-question-text") or f"请补充{key}。"
                    )
        except (AttributeError, TypeError, ValueError):
            pass
        observation_schema = _observation_schema(tool)
        declared_role = str(info.get("adapt_role", "")).casefold()
        if declared_role in {role.value for role in ToolRole}:
            role = ToolRole(declared_role)
        elif name.startswith("pay_"):
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
        elif _is_workflow_state_read(name, id_arguments, argument_text):
            role = ToolRole.STATE_READ
        elif is_search_tool(name):
            role = ToolRole.SEARCH
        elif _is_candidate_enrichment(
            name, required, id_arguments, observation_schema
        ):
            role = ToolRole.ENRICH
        else:
            role = ToolRole.UTILITY
        effect = (
            "unpaid_order"
            if role == ToolRole.CREATE
            else ("paid_order" if role == ToolRole.PAY else "")
        )
        return ToolMeta(
            name,
            role,
            required,
            id_arguments,
            effect,
            observation_schema,
            question_arguments,
            argument_schemas,
        )

    def result_schema(self, name: str) -> ObservationSchema | None:
        meta = self.meta.get(name)
        return meta.observation_schema if meta else None

    def contract(self, name: str) -> ToolContract | None:
        return self.contracts.get(name)

    def role(self, name: str) -> ToolRole:
        return self.meta.get(name, ToolMeta(name, ToolRole.READ)).role

    def domain_hint(self) -> str:
        """Infer the active environment from observable tool names.

        Tool topology is a stronger signal than a product-word dictionary:
        it correctly handles unseen services and goods without teaching the
        runtime their category names.
        """
        names = {name.casefold() for name in self.meta}
        declared = {
            str((getattr(tool, "info", {}) or {}).get("adapt_domain", ""))
            for tool in self.tools
        }
        declared.discard("")
        if len(declared) == 1:
            return next(iter(declared))
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
        return bool(self.candidate_decision(ledger, card).admissible)

    def candidate_decision(
        self,
        ledger: CandidateLedger,
        card=None,
        *,
        runtime=None,
        instruction_epoch: int = 0,
        fixed_arguments: dict[str, dict[str, Any]] | None = None,
        profile: dict[str, Any] | None = None,
    ):
        from agent.decision import DecisionCard
        from agent.runtime.candidate_decision import CandidateDecisionEngine

        # Lightweight tests and adapters may populate ``meta`` directly.
        # Recompile the immutable shadow view when that happens instead of
        # allowing two registries to become independent authorities.
        if set(self.contracts) != set(self.meta):
            self.contracts = ToolContractCompiler.compile(self.meta.values())
        return CandidateDecisionEngine(self).decide(
            ledger,
            card or DecisionCard(),
            runtime=runtime,
            instruction_epoch=instruction_epoch,
            fixed_arguments=fixed_arguments,
            profile=profile,
        )

    def candidate_entity_types(self, ledger: CandidateLedger) -> set[str]:
        """Derive selectable candidate types from CREATE schemas and topology."""
        decision = self.candidate_decision(ledger)
        resolved = {
            ledger.candidates[candidate_id].entity_type
            for binding in decision.admissible
            for candidate_id in binding.leaf_ids
            if candidate_id in ledger.candidates
        }
        return resolved or ledger.structural_leaf_types()

    def shortlist(self, ledger: CandidateLedger, card, limit: int = 5):
        decision = self.candidate_decision(ledger, card)
        selected = []
        seen: set[str] = set()
        for binding in decision.ordered:
            for candidate_id in binding.leaf_ids:
                if candidate_id in seen or candidate_id not in ledger.candidates:
                    continue
                seen.add(candidate_id)
                selected.append(ledger.candidates[candidate_id])
                if len(selected) >= limit:
                    return selected
        if selected:
            return selected
        return ledger.shortlist(card, limit=limit)

    def enrichment_ready(self, meta: ToolMeta, ledger: CandidateLedger) -> bool:
        required_types = {
            kind
            for argument, kind in meta.id_arguments.items()
            if argument in meta.required_arguments and kind != "user"
        }
        if not required_types:
            return False
        observed_types = {
            candidate.entity_type for candidate in ledger.candidates.values()
        }
        return required_types.issubset(observed_types)

    def has_enrichment_path(self, ledger: CandidateLedger) -> bool:
        return any(
            meta.role == ToolRole.ENRICH and self.enrichment_ready(meta, ledger)
            for meta in self.meta.values()
        )

    @staticmethod
    def _state_access_requested(
        runtime: TaskRuntime, ledger: CandidateLedger
    ) -> bool:
        if runtime.revision_requested or ledger.pending_payment_ids:
            return True
        if runtime.spec.action == "commit":
            return False
        text = runtime.spec.instruction.casefold()
        return runtime.spec.action == "modify" or any(
            marker in text
            for marker in (
                "状态", "订单", "预约记录", "预订记录", "查询记录",
                "status", "order", "booking", "reservation",
            )
        )

    def allowed_tools(self, runtime: TaskRuntime, ledger: CandidateLedger) -> list[Any]:
        allowed: list[Any] = []
        for tool in self.tools:
            meta = self.meta[tool.name]
            if meta.role == ToolRole.MEMORY:
                # Questioning and answer storage are framework-internal.
                continue
            if meta.role == ToolRole.SEARCH and not ledger.search_allowed(tool.name):
                continue
            if meta.role == ToolRole.ENRICH and not self.enrichment_ready(meta, ledger):
                continue
            if (
                meta.role == ToolRole.STATE_READ
                and not self._state_access_requested(runtime, ledger)
            ):
                continue
            if runtime.phase == RuntimePhase.NEED_INFO:
                if meta.role in {ToolRole.UTILITY, ToolRole.READ}:
                    allowed.append(tool)
                continue
            if runtime.phase in {RuntimePhase.START, RuntimePhase.SEARCH}:
                if runtime.revision_requested and ledger.pending_payment_ids:
                    if meta.role in {ToolRole.CANCEL, ToolRole.STATE_READ}:
                        allowed.append(tool)
                elif meta.role in {ToolRole.SEARCH, ToolRole.UTILITY, ToolRole.READ}:
                    allowed.append(tool)
                continue
            if runtime.phase == RuntimePhase.SELECT:
                if meta.role in {
                    ToolRole.SEARCH,
                    ToolRole.ENRICH,
                    ToolRole.UTILITY,
                    ToolRole.STATE_READ,
                    ToolRole.READ,
                }:
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
                    or meta.role == ToolRole.STATE_READ
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


def _is_workflow_state_read(
    name: str, id_arguments: dict[str, str], argument_text: list[str]
) -> bool:
    """Classify state/history tools before generic ``search_*`` handling."""
    lowered = name.casefold()
    if any(marker in lowered for marker in ("order", "booking", "reservation")):
        return True
    if any(
        kind in {"order", "booking", "reservation"}
        for kind in id_arguments.values()
    ):
        return True
    schema_text = " ".join(argument_text)
    return any(
        marker in lowered or marker in schema_text
        for marker in (
            "order_status",
            "order_detail",
            "search_delivery_orders",
            "booking_status",
            "reservation_status",
            "order id",
            "booking id",
            "reservation id",
            "订单id",
            "订单 id",
        )
    )


def _is_candidate_enrichment(
    name: str,
    required: set[str],
    id_arguments: dict[str, str],
    observation_schema: ObservationSchema | None,
) -> bool:
    required_entity_ids = {
        kind
        for argument, kind in id_arguments.items()
        if argument in required and kind != "user"
    }
    if not required_entity_ids:
        return False
    lowered = name.casefold()
    return observation_schema is not None or any(
        marker in lowered
        for marker in ("info", "detail", "option", "availability")
    )


def _observation_schema(tool: Any) -> ObservationSchema | None:
    try:
        schema = tool.returns.model_json_schema()
    except (AttributeError, TypeError, ValueError):
        return None
    candidate_schema = _find_candidate_schema(schema)
    if candidate_schema is None:
        return None
    entity_type = str(candidate_schema.get("x-adapt-entity", "")).strip()
    roles: dict[str, list[str]] = {}
    constraint_fields: list[str] = []
    for field_name, field_schema in candidate_schema.get("properties", {}).items():
        role = str(field_schema.get("x-adapt-role", "")).strip()
        if role:
            roles.setdefault(role, []).append(field_name)
        if field_schema.get("x-adapt-constraint"):
            constraint_fields.append(field_name)
    if not entity_type or not roles.get("id"):
        return None
    return ObservationSchema(
        entity_type=entity_type,
        id_field=roles["id"][0],
        name_field=(roles.get("name") or [""])[0],
        inventory_field=(roles.get("inventory") or [""])[0],
        price_field=(roles.get("price") or [""])[0],
        parent_fields=tuple(roles.get("parent_id", [])),
        attribute_fields=tuple(roles.get("attribute", [])),
        constraint_fields=tuple(constraint_fields),
    )


def _find_candidate_schema(node: Any) -> dict[str, Any] | None:
    if not isinstance(node, dict):
        return None
    if node.get("x-adapt-entity"):
        return node
    for child in node.values():
        values = child if isinstance(child, list) else [child]
        for value in values:
            found = _find_candidate_schema(value)
            if found is not None:
                return found
    return None

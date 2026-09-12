"""Tool schema registry and phase-based action filtering."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable

from agent.decision import CandidateLedger, is_search_tool
from agent.runtime.state import RuntimePhase, TaskRuntime

# Observable item vocabulary that means the task is about a purchasable good
# rather than a venue-level booking. Used only to decide whether a missing
# ``product`` entity is worth one bounded search (E-035).
PRODUCT_LEVEL_TERMS = (
    "券",
    "套餐",
    "商品",
    "外卖",
    "票",
    "房型",
    "房",
    "杯",
    "份",
    "餐",
    "按摩",
    "保洁",
)


def requires_product_entity(instruction: str, atoms: Iterable[str] = ()) -> bool:
    """Whether the current task is about an item rather than only a venue."""
    texts = [instruction or "", *[str(atom) for atom in atoms or ()]]
    return any(
        term in text for text in texts for term in PRODUCT_LEVEL_TERMS
    )



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
    argument_names: set[str] = field(default_factory=set)


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
        elif name in {"query_preference_memory", "read_preference_memory"}:
            # Memory *reads* stay available to the policy model. The stock agent
            # has them and uses them to ground a choice before searching; hiding
            # them (as this registry did) removed an observable capability and
            # left the model with only the fixed Decision Card (E-042).
            role = ToolRole.READ
        elif name in {
            "suggest_question_tool",
            "record_preference_answer",
        }:
            role = ToolRole.MEMORY
        elif is_search_tool(name):
            role = ToolRole.SEARCH
        else:
            role = ToolRole.READ
        required: set[str] = set()
        id_arguments: dict[str, str] = {}
        properties: set[str] = set()
        try:
            schema = tool.params.model_json_schema()
            required = set(schema.get("required", []))
            properties = set(schema.get("properties", {}))
            for key in properties:
                if key.endswith(("_id", "_ids")):
                    id_arguments[key] = key.removesuffix("_ids").removesuffix("_id")
        except (AttributeError, TypeError, ValueError):
            pass
        effect = (
            "unpaid_order"
            if role == ToolRole.CREATE
            else ("paid_order" if role == ToolRole.PAY else "")
        )
        return ToolMeta(name, role, required, id_arguments, effect, properties)

    def role(self, name: str) -> ToolRole:
        return self.meta.get(name, ToolMeta(name, ToolRole.READ)).role

    # Entity aliases: the same observed product satisfies a room, ticket or seat
    # argument. Unknown kinds fall back to their own name.
    _ENTITY_ALIASES = {
        "room": "product",
        "ticket": "product",
        "seat": "product",
    }

    def _required_entity_types(self, meta: ToolMeta) -> set[str]:
        return {
            self._ENTITY_ALIASES.get(kind, kind)
            for argument, kind in meta.id_arguments.items()
            if argument in meta.required_arguments and argument != "user_id"
        }

    def required_entity_types(self, tool_name: str) -> set[str]:
        meta = self.meta.get(tool_name)
        return self._required_entity_types(meta) if meta else set()

    def create_gaps(self, ledger: CandidateLedger) -> dict[str, list[str]]:
        """Entity kinds each CREATE tool needs but has not observed yet.

        Obligations are per create tool. A shop-only reservation tool must not
        make a coupon order look executable: that exposed
        ``create_instore_product_order`` while no product had been observed and
        the policy model filled ``product_id`` from memory (E-035).
        """
        observed = {
            candidate.entity_type for candidate in ledger.candidates.values()
        }
        gaps: dict[str, list[str]] = {}
        for meta in self.meta.values():
            if meta.role != ToolRole.CREATE:
                continue
            missing = self._required_entity_types(meta) - observed
            if missing:
                gaps[meta.name] = sorted(missing)
        return gaps

    def usable_create_tools(self, ledger: CandidateLedger) -> set[str]:
        observed = {
            candidate.entity_type for candidate in ledger.candidates.values()
        }
        usable: set[str] = set()
        for meta in self.meta.values():
            if meta.role != ToolRole.CREATE:
                continue
            # A tool whose schema exposes no entity argument cannot be checked
            # here; the ledger still rejects any unobserved ID at preflight.
            if not (self._required_entity_types(meta) - observed):
                usable.add(meta.name)
        return usable

    def search_tool_for(self, kind: str) -> str:
        """The search tool that can observe ``kind``, by name convention."""
        for name, meta in sorted(self.meta.items()):
            if meta.role == ToolRole.SEARCH and kind in name:
                return name
        return ""

    def execution_ready(self, ledger: CandidateLedger, card=None) -> bool:
        """Whether some CREATE tool can legally run with observed entities.

        Observing an ID is insufficient when every visible candidate violates
        a MUST/AVOID constraint. Keep SELECT open for a bounded second search
        until the deterministic shortlist contains a usable candidate.

        Readiness is per create tool: a tool whose required entity kinds are
        all observed may run, while a tool that still misses one kind may not
        (E-035).
        """
        if card is not None and not ledger.shortlist(card, limit=1):
            return False
        usable = self.usable_create_tools(ledger)
        if not usable:
            return False
        for name in usable:
            if "hotel" not in name:
                return True
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
            # A single hotel's rooms cannot establish the best match across
            # brand/location/style preferences. Expand a bounded set of parent
            # hotels before exposing CREATE.
            if len(expanded_hotels) >= min(6, len(hotels)):
                return True
        return False

    def allowed_tools(
        self,
        runtime: TaskRuntime,
        ledger: CandidateLedger,
        *,
        gate_phases: bool = True,
    ) -> list[Any]:
        if not gate_phases:
            # Isolation rig (E-046): every environment tool stays visible, so
            # the model keeps the action space the stock agent has. Write-time
            # validation is unaffected; only the per-phase crop is removed.
            return [
                tool
                for tool in self.tools
                if self.meta[tool.name].role != ToolRole.MEMORY
            ]
        allowed: list[Any] = []
        for tool in self.tools:
            meta = self.meta[tool.name]
            if meta.role == ToolRole.MEMORY:
                # Questioning and answer storage are framework-internal.
                continue
            if meta.role == ToolRole.SEARCH and not ledger.search_allowed(tool.name):
                continue
            if runtime.phase == RuntimePhase.NEED_INFO:
                # Observation stays available while a decision-critical slot is
                # open: the model may need a search to ask a useful question,
                # and hiding every search tool here left it with no legal action
                # at all (E-050).
                if meta.role in {ToolRole.SEARCH, ToolRole.READ}:
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
                # decision and let the policy model loop. A create tool whose
                # required entities are still unobserved stays hidden so the
                # model cannot fill an ID from memory (E-035).
                if (
                    meta.role == ToolRole.CREATE
                    and runtime.authorization.create_authorized
                    and tool.name in self.usable_create_tools(ledger)
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

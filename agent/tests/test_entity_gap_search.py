"""Per-entity execution readiness and deterministic gap search (E-035).

A shop search leaves ``product_id`` unobserved while a shop-only reservation
tool keeps CREATE "ready"; the policy model then fills ``product_id`` from
memory and every attempt is rejected. These tests pin the two halves of the
fix: readiness is per create tool, and the framework issues one bounded search
for a missing entity kind using observable keywords only.
"""

from __future__ import annotations

from agent.adapt_agent import ADAPTAgent
from agent.decision import Candidate, CandidateLedger, DecisionCard, TaskSpec
from agent.runtime.state import RuntimePhase, TaskRuntime
from agent.runtime.tools import ToolMeta, ToolRegistry, ToolRole


class _Debug:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emit(self, event: str, **payload) -> None:
        self.events.append({"event": event, **payload})


SHOP = "S17791026318502353_I00021"
PRODUCT = "S17791026318502353_P00001"
INSTRUCTION = "明天晚上帮我找个养生休闲的地方，顺便帮我看看有没有团购券，给我团一张"


def registry(*names: str) -> ToolRegistry:
    reg = ToolRegistry()
    reg.meta = {name: ToolMeta(name, role_for(name)) for name in names}
    return reg


def role_for(name: str) -> ToolRole:
    if name.startswith("create_"):
        return ToolRole.CREATE
    if name.startswith("pay_"):
        return ToolRole.PAY
    if "search" in name or "recommend" in name:
        return ToolRole.SEARCH
    return ToolRole.READ


def coupon_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.meta = {
        "instore_shop_search_recommend": ToolMeta(
            "instore_shop_search_recommend", ToolRole.SEARCH
        ),
        "instore_product_search_recommend": ToolMeta(
            "instore_product_search_recommend", ToolRole.SEARCH
        ),
        "instore_reservation": ToolMeta(
            "instore_reservation",
            ToolRole.CREATE,
            required_arguments={"user_id", "shop_id", "time"},
            id_arguments={"user_id": "user", "shop_id": "shop"},
        ),
        "create_instore_product_order": ToolMeta(
            "create_instore_product_order",
            ToolRole.CREATE,
            required_arguments={"user_id", "shop_id", "product_id"},
            id_arguments={
                "user_id": "user",
                "shop_id": "shop",
                "product_id": "product",
            },
        ),
    }
    return reg


def ledger_with(*entities: tuple[str, str]) -> CandidateLedger:
    ledger = CandidateLedger()
    for candidate_id, entity_type in entities:
        ledger.candidates[candidate_id] = Candidate(
            candidate_id, entity_type, "candidate", "raw", "tool"
        )
    return ledger


def build_agent(
    ledger: CandidateLedger,
    reg: ToolRegistry,
    *,
    execution_ready: bool = False,
) -> ADAPTAgent:
    agent = ADAPTAgent.__new__(ADAPTAgent)
    agent.task_spec = TaskSpec.compile(INSTRUCTION)
    agent.decision_card = DecisionCard(must=["团购券"])
    agent.ledger = ledger
    agent.tool_registry = reg
    agent.runtime = TaskRuntime.begin(agent.task_spec)
    agent.runtime.authorization.create_authorized = True
    agent.runtime.phase = RuntimePhase.SELECT
    agent.runtime.execution_ready = execution_ready
    agent.runtime.candidates_seen = len(ledger.candidates)
    agent._gap_search_tool = ""
    agent._recommendation_delivered = False
    agent.debug = _Debug()
    return agent


def test_create_gaps_report_the_unobserved_entity_kind():
    reg = coupon_registry()
    ledger = ledger_with((SHOP, "shop"))
    gaps = reg.create_gaps(ledger)
    assert gaps == {"create_instore_product_order": ["product"]}
    assert reg.create_gaps(ledger_with((SHOP, "shop"), (PRODUCT, "product"))) == {}


def test_usable_create_tools_are_evaluated_per_tool():
    reg = coupon_registry()
    shops_only = ledger_with((SHOP, "shop"))
    # A shop-only reservation tool must not make a coupon order executable.
    assert reg.usable_create_tools(shops_only) == {"instore_reservation"}
    assert reg.create_gaps(shops_only) == {
        "create_instore_product_order": ["product"]
    }
    with_product = ledger_with((SHOP, "shop"), (PRODUCT, "product"))
    assert reg.usable_create_tools(with_product) == {
        "instore_reservation",
        "create_instore_product_order",
    }
    assert reg.create_gaps(with_product) == {}


def test_execution_ready_needs_at_least_one_usable_create_tool():
    reg = ToolRegistry()
    reg.meta = {
        "instore_product_search_recommend": ToolMeta(
            "instore_product_search_recommend", ToolRole.SEARCH
        ),
        "create_instore_product_order": ToolMeta(
            "create_instore_product_order",
            ToolRole.CREATE,
            required_arguments={"user_id", "shop_id", "product_id"},
            id_arguments={
                "user_id": "user",
                "shop_id": "shop",
                "product_id": "product",
            },
        ),
    }
    assert not reg.execution_ready(ledger_with((SHOP, "shop")))
    assert reg.execution_ready(ledger_with((SHOP, "shop"), (PRODUCT, "product")))


def test_ready_to_create_hides_a_create_tool_with_missing_entities():
    reg = coupon_registry()
    reg.tools = [type("T", (), {"name": name})() for name in reg.meta]
    runtime = TaskRuntime.begin(TaskSpec.compile(INSTRUCTION))
    runtime.phase = RuntimePhase.READY_TO_CREATE
    runtime.authorization.create_authorized = True
    shops_only = ledger_with((SHOP, "shop"))
    exposed = {tool.name for tool in reg.allowed_tools(runtime, shops_only)}
    assert "create_instore_product_order" not in exposed
    assert "instore_reservation" in exposed
    with_product = ledger_with((SHOP, "shop"), (PRODUCT, "product"))
    exposed = {tool.name for tool in reg.allowed_tools(runtime, with_product)}
    assert "create_instore_product_order" in exposed


def test_gap_search_uses_observable_keywords_and_is_registered_once():
    reg = coupon_registry()
    reg.tools = [type("T", (), {"name": name})() for name in reg.meta]
    ledger = ledger_with((SHOP, "shop"))
    ledger.register_search(
        "instore_shop_search_recommend", {"keywords": ["养生", "搓背"]}
    )
    agent = build_agent(ledger, reg)
    message = agent._framework_entity_gap_search()
    assert message is not None
    call = message.tool_calls[0]
    assert call.name == "instore_product_search_recommend"
    assert call.arguments["keywords"] == ["养生", "搓背"]
    signature = ledger.signature(call.name, call.arguments)
    assert ledger.search_counts[signature] == 1
    # A second call in the same subtask must not repeat the same search.
    assert agent._framework_entity_gap_search() is None


def test_gap_search_skipped_when_execution_is_ready():
    reg = coupon_registry()
    ledger = ledger_with((SHOP, "shop"), (PRODUCT, "product"))
    agent = build_agent(ledger, reg, execution_ready=True)
    assert agent._framework_entity_gap_search() is None


def test_gap_search_requires_authorized_create():
    reg = coupon_registry()
    ledger = ledger_with((SHOP, "shop"))
    agent = build_agent(ledger, reg)
    agent.runtime.authorization.create_authorized = False
    assert agent._framework_entity_gap_search() is None


def test_gap_keywords_prefer_observed_search_terms():
    reg = coupon_registry()
    ledger = ledger_with((SHOP, "shop"))
    agent = build_agent(ledger, reg)
    ledger.register_search(
        "instore_shop_search_recommend", {"keywords": ["按摩", "养生"]}
    )
    assert agent._entity_gap_keywords() == ["按摩", "养生"]
    # Without any prior search the card's own task atoms are used.
    empty = build_agent(ledger_with((SHOP, "shop")), reg)
    assert empty._entity_gap_keywords() == ["团购券"]


def test_gap_search_never_replaces_the_first_venue_search():
    """An empty ledger must not trigger create-gap searches (ota regression)."""
    reg = coupon_registry()
    reg.meta["create_hotel_order"] = ToolMeta(
        "create_hotel_order",
        ToolRole.CREATE,
        required_arguments={"user_id", "hotel_id", "room_id"},
        id_arguments={"user_id": "user", "hotel_id": "hotel", "room_id": "room"},
    )
    agent = build_agent(CandidateLedger(), reg)
    assert agent._framework_entity_gap_search() is None


def test_gap_search_requires_an_observed_sibling_entity():
    reg = coupon_registry()
    # A candidate of an unrelated type does not witness the create tool.
    agent = build_agent(ledger_with(("S17791026318502353_P00009", "product")), reg)
    agent.tool_registry.meta = {
        "instore_product_search_recommend": ToolMeta(
            "instore_product_search_recommend", ToolRole.SEARCH
        ),
        "create_instore_product_order": ToolMeta(
            "create_instore_product_order",
            ToolRole.CREATE,
            required_arguments={"user_id", "shop_id", "product_id"},
            id_arguments={
                "user_id": "user",
                "shop_id": "shop",
                "product_id": "product",
            },
        ),
    }
    assert agent._framework_entity_gap_search() is None


def test_gap_search_skips_a_search_tool_with_arguments_it_cannot_supply():
    reg = ToolRegistry()
    reg.meta = {
        "product_search_recommend": ToolMeta(
            "product_search_recommend",
            ToolRole.SEARCH,
            required_arguments={"keywords", "city_name"},
        ),
        "create_instore_product_order": ToolMeta(
            "create_instore_product_order",
            ToolRole.CREATE,
            required_arguments={"user_id", "shop_id", "product_id"},
            id_arguments={
                "user_id": "user",
                "shop_id": "shop",
                "product_id": "product",
            },
        ),
    }
    agent = build_agent(ledger_with((SHOP, "shop")), reg)
    assert agent._framework_entity_gap_search() is None

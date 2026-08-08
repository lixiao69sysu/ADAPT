import json

from vita.data_model.message import ToolCall, ToolMessage, UserMessage
from vita.environment.tool import as_tool

from agent.harness.tool_guard import EntityLedger, ToolGuard


def search_products(keyword: str) -> list[dict]:
    """Search products."""
    return []


def pay_order(order_id: str) -> str:
    """Pay an existing order."""
    return "done"


def test_ledger_learns_ids_from_successful_tool_json():
    ledger = EntityLedger()
    ledger.observe(
        ToolMessage(
            id="call-1",
            name="create_order",
            role="tool",
            content=json.dumps({"order_id": "ORD-12345", "product_id": "SKU-77"}),
            requestor="assistant",
            error=False,
        )
    )

    assert ledger.knows("ORD-12345")
    assert ledger.knows("SKU-77")


def test_ledger_ignores_ids_echoed_by_failed_tool_output():
    ledger = EntityLedger()
    ledger.observe(
        ToolMessage(
            id="call-2",
            name="pay_order",
            role="tool",
            content="Order ORD-FAKE-99 not found",
            requestor="assistant",
            error=True,
        )
    )

    assert not ledger.knows("ORD-FAKE-99")


def test_ledger_accepts_id_explicitly_supplied_by_user():
    ledger = EntityLedger()
    ledger.observe(UserMessage(role="user", content="请支付订单 ORD-USER-88"))

    assert ledger.knows("ORD-USER-88")


def test_guard_rejects_unknown_tool_and_schema_error():
    guard = ToolGuard([as_tool(search_products)])

    unknown = guard.validate(ToolCall(name="missing_tool", arguments={}), EntityLedger())
    wrong_type = guard.validate(
        ToolCall(
            name="search_products",
            arguments={"keyword": ["not", "a", "string"]},
        ),
        EntityLedger(),
    )

    assert unknown[0].code == "unknown_tool"
    assert wrong_type[0].code == "invalid_arguments"


def test_guard_rejects_unseen_reference_id_but_accepts_public_id():
    guard = ToolGuard([as_tool(pay_order)])
    ledger = EntityLedger()
    call = ToolCall(name="pay_order", arguments={"order_id": "ORD-12345"})

    assert guard.validate(call, ledger)[0].code == "unknown_entity_id"

    ledger.known_ids.add("ORD-12345")
    assert guard.validate(call, ledger) == []


def test_guard_does_not_require_user_id_provenance():
    def read_profile(user_id: str) -> dict:
        """Read the current user's public profile."""
        return {}

    guard = ToolGuard([as_tool(read_profile)])
    call = ToolCall(name="read_profile", arguments={"user_id": "USER-1"})

    assert guard.validate(call, EntityLedger()) == []


def test_guard_allows_cross_record_combination_as_soft_diagnostic():
    def create_order(store_id: str, product_id: str) -> str:
        """Create an order."""
        return "done"

    ledger = EntityLedger()
    ledger.observe(ToolMessage(
        id="search", name="search", role="tool", requestor="assistant", error=False,
        content=(
            "store_id=STORE-1 product_id=PRODUCT-1\n"
            "store_id=STORE-2 product_id=PRODUCT-2"
        ),
    ))
    guard = ToolGuard([as_tool(create_order)])

    issues = guard.validate(
        ToolCall(
            name="create_order",
            arguments={"store_id": "STORE-1", "product_id": "PRODUCT-2"},
        ),
        ledger,
    )

    assert issues == []

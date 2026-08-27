"""Release gates derived from the failed execution-plan dev run.

These tests use pristine VitaBench's public tool schemas but never call an
environment, evaluator, rubric, or hidden task field.
"""

from __future__ import annotations

from agent.vitabench_bootstrap import enable_vitabench_utf8

enable_vitabench_utf8()

from agent.decision import (  # noqa: E402
    CandidateLedger,
    TaskSpec,
    build_decision_card,
)
from agent.runtime import RuntimePhase, TaskRuntime, ToolRegistry  # noqa: E402
from agent.runtime.argument_binding import ArgumentBindingResolver  # noqa: E402
from agent.counterfactual_gate import replay_checkpoint  # noqa: E402
from vita.domains.delivery.tools import DeliveryTools  # noqa: E402
from vita.domains.instore.tools import InStoreTools  # noqa: E402
from vita.domains.ota.tools import OTATools  # noqa: E402


def _public_tools():
    tools = []
    for toolkit in (DeliveryTools(None), InStoreTools(None), OTATools(None)):
        tools.extend(toolkit.get_tools().values())
    return tools


def _raw_value(schema):
    options = schema.get("anyOf") or schema.get("oneOf") or ()
    if options:
        schema = next(
            (option for option in options if option.get("type") != "null"),
            schema,
        )
    json_type = schema.get("type")
    if json_type == "array":
        return [_raw_value(schema.get("items", {}))]
    if json_type == "object":
        return {
            name: _raw_value(value)
            for name, value in schema.get("properties", {}).items()
        }
    if json_type == "integer":
        return "2"
    if json_type == "number":
        return "2.5"
    if json_type == "boolean":
        return "true"
    return "synthetic-value"


def test_all_public_write_schemas_recursively_normalize_argument_types():
    registry = ToolRegistry()
    registry.rebuild(_public_tools())
    checked = 0
    for name, contract in registry.contracts.items():
        if contract.role not in {"create", "pay", "cancel", "modify"}:
            continue
        raw = {
            argument.name: _raw_value(argument.json_schema)
            for argument in contract.arguments
        }
        normalized = ArgumentBindingResolver.normalize_arguments(contract, raw)
        assert not ArgumentBindingResolver.type_errors(contract, normalized), name
        checked += 1
    # Ensure the gate actually traversed all three domains rather than passing
    # vacuously after an import/schema regression.
    assert checked >= 20


def test_real_delivery_count_array_and_ota_quantities_are_numeric():
    registry = ToolRegistry()
    registry.rebuild(_public_tools())

    delivery = registry.contract("create_delivery_order")
    normalized = ArgumentBindingResolver.normalize_arguments(
        delivery, {"product_cnts": ["1"]}
    )
    assert normalized["product_cnts"] == [1]
    assert not ArgumentBindingResolver.type_errors(delivery, normalized)

    for name in ("create_train_order", "create_flight_order"):
        contract = registry.contract(name)
        normalized = ArgumentBindingResolver.normalize_arguments(
            contract, {"quantity": "2"}
        )
        assert normalized["quantity"] == 2
        assert not ArgumentBindingResolver.type_errors(contract, normalized)


def test_colloquial_jiu_is_not_promoted_to_an_exact_entity_constraint():
    for instruction in (
        "一到下午猪瘾就犯了，给我送个巧克力到单位来~",
        "要是下雨就找个室内的约上。",
    ):
        spec = TaskSpec.compile(instruction)
        assert not [item for item in spec.must if item.kind == "entity"]

    explicit = TaskSpec.compile("就极光片吧")
    assert [item.value for item in explicit.must if item.kind == "entity"] == [
        "极光片"
    ]


def test_missing_action_date_does_not_make_observed_candidate_inadmissible():
    registry = ToolRegistry()
    registry.rebuild(DeliveryTools(None).get_tools().values())
    spec = TaskSpec.compile(
        "26号那天要开会，提前给我点个咖啡提神吧",
        domain_hint="delivery",
    )
    card = build_decision_card(spec, [])
    runtime = TaskRuntime.begin(spec)
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "StoreProduct(store_id=S900_S00001, product_name=虚构咖啡, "
        "product_id=S900_P00001, attributes=标准咖啡因, quantity=9)",
    )
    fixed = {
        name: ArgumentBindingResolver.bind(
            contract, spec, runtime.resolved_slots, {"user_id": "user-1"}
        )
        for name, contract in registry.contracts.items()
        if contract.role == "create"
    }
    decision = registry.candidate_decision(
        ledger,
        card,
        runtime=runtime,
        fixed_arguments=fixed,
        profile={"user_id": "user-1"},
    )
    assert decision.admissible
    assert decision.selected is not None
    assert decision.selected.create_tool == "create_delivery_order"


def test_enrichment_budget_is_global_per_subtask_and_resets():
    ledger = CandidateLedger(max_enrichment_reads_per_subtask=2)
    assert ledger.enrichment_budget_remaining() == 2
    ledger.register_enrichment_read("describe_parent", {"ref": "A"})
    ledger.register_enrichment_read("describe_parent", {"ref": "B"})
    assert ledger.enrichment_budget_remaining() == 0
    ledger.reset()
    assert ledger.enrichment_budget_remaining() == 2


def _execution_decision(domain, tools, instruction, observation, profile):
    registry = ToolRegistry()
    registry.rebuild(tools)
    spec = TaskSpec.compile(instruction, domain_hint=domain)
    runtime = TaskRuntime.begin(spec)
    ledger = CandidateLedger()
    ledger.observe("synthetic_search_recommend", observation)
    card = build_decision_card(spec, [])
    fixed = {
        name: ArgumentBindingResolver.bind(
            contract, spec, runtime.resolved_slots, profile
        )
        for name, contract in registry.contracts.items()
        if contract.role == "create"
    }
    return registry.candidate_decision(
        ledger,
        card,
        runtime=runtime,
        fixed_arguments=fixed,
        profile=profile,
    )


def test_cross_domain_search_select_create_contracts_are_executable():
    delivery = _execution_decision(
        "delivery",
        list(DeliveryTools(None).get_tools().values()),
        "帮我买一杯虚构饮品送到家里",
        "StoreProduct(store_id=S910_S00001, product_name=虚构饮品, "
        "product_id=S910_P00001, quantity=8)",
        {"user_id": "user-1", "常住住址": "虚构市星河路8号"},
    )
    assert delivery.next_phase == RuntimePhase.READY_TO_CREATE
    assert delivery.selected.create_tool == "create_delivery_order"
    assert delivery.selected.as_arguments()["product_cnts"] == [1]

    instore = _execution_decision(
        "instore",
        list(InStoreTools(None).get_tools().values()),
        "帮我预约个场馆，周六上午十点",
        "Shop(shop_name=虚构室内场馆, shop_id=S920_I00001, score=4.8)",
        {"user_id": "user-2"},
    )
    assert instore.next_phase == RuntimePhase.READY_TO_CREATE
    assert instore.selected.create_tool in {"instore_book", "instore_reservation"}

    ota = _execution_decision(
        "ota",
        list(OTATools(None).get_tools().values()),
        "9号帮我买一张从甲城到乙城的火车票",
        "Train(train_id=S930_T00001, train_number=G001)\n"
        "Seat(train_id=S930_T00001, seat_id=S930_P00001, "
        "seat_type=一等座, quantity=5)",
        {"user_id": "user-3"},
    )
    assert ota.next_phase == RuntimePhase.READY_TO_CREATE
    assert ota.selected.create_tool == "create_train_order"
    assert ota.selected.as_arguments()["quantity"] == 1


def test_real_noncreate_workflow_follows_state_read_provenance():
    registry = ToolRegistry()
    registry.rebuild(OTATools(None).get_tools().values())
    ledger = CandidateLedger()
    ledger.observe_state(
        "search_train_order", "TrainOrder(order_id=order-visible-7, status=paid)"
    )
    runtime = TaskRuntime.begin(TaskSpec.compile("取消我的火车订单", domain_hint="ota"))
    runtime.observe_workflow_state(has_state=True)
    allowed = [tool.name for tool in registry.allowed_tools(runtime, ledger)]
    assert "cancel_train_order" in allowed
    assert "cancel_hotel_order" not in allowed


def test_counterfactual_gate_normalizes_historical_write_shape(tmp_path):
    checkpoint = tmp_path / "observable-checkpoint.json"
    checkpoint.write_text(
        __import__("json").dumps(
            {
                "simulations": [
                    {
                        "task_id": "synthetic-user",
                        "messages": [
                            {
                                "role": "assistant",
                                "content": "你好，请问需要什么服务？",
                                "turn_idx": 0,
                            },
                            {
                                "role": "user",
                                "content": "帮我买一杯虚构饮品送到家里",
                                "turn_idx": 1,
                            },
                            {
                                "role": "tool",
                                "name": "delivery_product_search_recommand",
                                "content": (
                                    "StoreProduct(store_id=S940_S00001, "
                                    "product_name=虚构饮品, "
                                    "product_id=S940_P00001, quantity=5)"
                                ),
                                "error": False,
                                "turn_idx": 3,
                            },
                            {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "name": "create_delivery_order",
                                        "arguments": {
                                            "user_id": "synthetic-user",
                                            "store_id": "S940_S00001",
                                            "product_ids": ["S940_P00001"],
                                            "product_cnts": ["1"],
                                            "address": "虚构地址",
                                            "dispatch_time": "2026-08-26 10:00:00",
                                        },
                                    }
                                ],
                                "turn_idx": 4,
                            },
                        ],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    report = replay_checkpoint(checkpoint)
    assert report.raw_write_type_errors == 1
    assert report.normalized_write_type_errors == 0
    assert report.structurally_unbindable_subtasks == 0
    assert report.passed

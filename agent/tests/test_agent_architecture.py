"""Integration-focused tests for the complete ADAPT agent architecture."""

from __future__ import annotations

from unittest.mock import patch

from agent.adapt_agent import ADAPTAgent
from agent.decision import (
    Candidate,
    CandidateLedger,
    Constraint,
    ConstraintOperator,
    ConstraintTarget,
    DecisionCard,
    TaskSpec,
    build_decision_card,
)
from agent.lessons import ExecutionLessonStore
from agent.memory.adapt_memory import ADAPTMemory
from agent.memory.drift import DriftDetector
from agent.memory.fact_store import FactStore
from agent.memory.facts import PreferenceFact, fact_from_signal
from agent.memory.signals import Signal, SignalParser
from agent.runtime import (
    DebugEventStore,
    InformationGap,
    OperationJournal,
    QuestionGate,
    ResponseJournal,
    RuntimePhase,
    TaskRuntime,
    ToolErrorLedger,
    ToolRegistry,
    ToolRole,
)
from agent.runtime.alignment import EvidenceAlignment
from agent.runtime.ranking import CandidateRanker
from agent.runtime.tools import ToolMeta
from vita.data_model.message import AssistantMessage, ToolCall, ToolMessage
from vita.data_model.message import SystemMessage, UserMessage
from vita.agent.llm_agent import LLMAgentState


def test_read_is_pure_across_framework_repeats():
    memory = ADAPTMemory(language="chinese")
    query = "下周去上海，帮我买票"
    before = memory.proactive.asked_this_subtask
    outputs = [memory.read(query) for _ in range(3)]
    assert outputs[0] == outputs[1] == outputs[2]
    assert memory.proactive.asked_this_subtask == before


def test_pristine_delivery_state_tools_are_not_candidate_searches():
    """Real VitaBench tools have no x-adapt annotations."""
    from agent.vitabench_bootstrap import enable_vitabench_utf8

    enable_vitabench_utf8()
    from vita.domains.delivery.data_model import DeliveryDB
    from vita.domains.delivery.tools import DeliveryTools

    db = DeliveryDB(user_id="U-test", stores={}, orders={})
    registry = ToolRegistry()
    registry.rebuild(list(DeliveryTools(db).get_tools().values()))

    assert registry.role("delivery_product_search_recommand") == ToolRole.SEARCH
    assert registry.role("get_delivery_product_info") == ToolRole.ENRICH
    assert registry.role("search_delivery_orders") == ToolRole.STATE_READ
    assert registry.role("get_delivery_order_status") == ToolRole.STATE_READ
    assert registry.role("get_delivery_order_detail") == ToolRole.STATE_READ
    runtime = TaskRuntime.begin(TaskSpec.compile("帮我点一份外卖"))
    allowed = {tool.name for tool in registry.allowed_tools(runtime, CandidateLedger())}
    assert "delivery_product_search_recommand" in allowed
    assert "search_delivery_orders" not in allowed
    assert "get_delivery_order_status" not in allowed


def test_state_observation_never_pollutes_candidate_ledger():
    ledger = CandidateLedger()
    ledger.observe_state(
        "get_delivery_order_detail",
        "order_id: O900, product_id: P123, status:unpaid",
    )
    assert not ledger.candidates
    assert ledger.has_state_id("order")
    assert not ledger.has_state_id("product")
    assert "O900" in ledger.pending_payment_ids


def test_preflight_is_transactional_until_assistant_is_committed():
    class Tool:
        name = "search_quasar_nodes"

    class Debug:
        def emit(self, *args, **kwargs):
            pass

    agent = object.__new__(ADAPTAgent)
    agent.task_spec = TaskSpec.compile("帮我推荐一个节点")
    agent.runtime = TaskRuntime.begin(agent.task_spec)
    agent.tool_registry = ToolRegistry()
    agent.tool_registry.meta = {
        Tool.name: ToolMeta(Tool.name, ToolRole.SEARCH, set(), {})
    }
    agent.tool_errors = ToolErrorLedger()
    agent.ledger = CandidateLedger()
    agent.decision_card = DecisionCard()
    agent.user_profile = {}
    agent.question_gate = QuestionGate()
    agent.enable_candidate_validation = False
    agent.debug = Debug()
    call = ToolCall(id="q1", name=Tool.name, arguments={"keywords": ["blue"]})
    assistant = AssistantMessage(role="assistant", tool_calls=[call])

    phase = agent.runtime.phase
    assert not agent._preflight(assistant, [Tool()])
    assert not agent._preflight(assistant, [Tool()])
    assert agent.runtime.phase == phase
    assert agent.ledger.search_counts == {}
    assert agent.ledger.search_family_counts == {}

    agent._observe_assistant(assistant)
    assert agent.ledger.family_search_count(Tool.name) == 1


def test_tool_epoch_update_does_not_rebind_active_subtask():
    class Params:
        @staticmethod
        def model_json_schema():
            return {"type": "object", "properties": {}}

    class Tool:
        name = "search_ota_hotel"
        params = Params()
        returns = None
        info = {}

    agent = ADAPTAgent(
        tools=[],
        domain_policy="time={time}",
        memory=ADAPTMemory(language="chinese"),
        user_profile={"user_id": "epoch-test"},
        time="2026-08-25 12:00:00",
        language="chinese",
        enable_lessons=False,
    )
    agent.set_current_instruction("帮我推荐一份晚餐")
    assert agent.task_spec.domain == "delivery"
    agent._observe_input(UserMessage(role="user", content="我想吃清淡的"))

    agent.update_tools([Tool()])
    assert agent.task_spec.domain == "delivery"
    assert agent._pending_tool_registry is not None

    agent.set_current_instruction("帮我推荐一个住处")
    assert agent.task_spec.domain == "ota"
    assert agent._pending_tool_registry is None


def test_fictional_schema_drives_enrichment_without_facet_vocabulary():
    class Params:
        @staticmethod
        def model_json_schema():
            return {
                "type": "object",
                "required": ["quasar_id"],
                "properties": {
                    "quasar_id": {
                        "type": "string",
                        "x-adapt-entity": "quasar",
                    }
                },
            }

    class Tool:
        name = "inspect_quasar_info"
        params = Params()
        returns = None
        info = {}

    registry = ToolRegistry()
    registry.rebuild([Tool()])
    assert registry.role(Tool.name) == ToolRole.ENRICH

    agent = object.__new__(ADAPTAgent)
    agent.task_spec = TaskSpec.compile("帮我绑定一个完全虚构对象")
    agent.runtime = TaskRuntime.begin(agent.task_spec)
    agent.runtime.phase = RuntimePhase.SELECT
    agent.runtime.authorization.create_authorized = True
    agent.runtime.execution_ready = False
    agent.tool_registry = registry
    agent.ledger = CandidateLedger()
    agent.ledger.candidates["Q-17"] = __import__(
        "agent.decision", fromlist=["Candidate"]
    ).Candidate("Q-17", "quasar", "Azure Quasar", "", "search_quasars")
    agent.decision_card = DecisionCard()
    agent.user_profile = {}

    message = agent._framework_enrichment()
    assert message is not None
    assert message.tool_calls[0].name == "inspect_quasar_info"
    assert message.tool_calls[0].arguments == {"quasar_id": "Q-17"}


def test_response_journal_is_scoped_by_epoch_and_candidate_snapshot():
    journal = ResponseJournal()
    journal.commit(1, 3, "recommendation", candidate_ids=("Q-2", "Q-1"))
    assert journal.has(1, 3, "recommendation")
    assert journal.snapshot(1, 3, "recommendation") == ("Q-2", "Q-1")
    assert journal.latest_snapshot(1, "recommendation") == (
        3,
        ("Q-2", "Q-1"),
    )
    assert not journal.has(1, 4, "recommendation")
    assert not journal.has(2, 3, "recommendation")
    journal.reset()
    assert not journal.has(1, 3, "recommendation")
    assert journal.latest_snapshot(1, "recommendation") is None


def test_schema_selects_unseen_candidate_type_without_entity_priority():
    ledger = CandidateLedger()
    ledger.candidates = {
        "legacy-product": Candidate(
            "legacy-product", "product", "Legacy Product", "", "search"
        ),
        "legacy-hotel": Candidate(
            "legacy-hotel", "hotel", "Legacy Hotel", "", "search"
        ),
        "quasar-7": Candidate(
            "quasar-7", "quasar", "Azure Quasar", "", "search"
        ),
    }
    registry = ToolRegistry()
    registry.meta = {
        "bind_quasar": ToolMeta(
            "bind_quasar",
            ToolRole.CREATE,
            {"quasar_id"},
            {"quasar_id": "quasar"},
        )
    }

    shortlist = registry.shortlist(ledger, DecisionCard(), limit=5)
    assert [candidate.candidate_id for candidate in shortlist] == ["quasar-7"]


def test_schema_shortlist_uses_leaf_nodes_when_parent_and_child_share_a_type():
    ledger = CandidateLedger()
    ledger.candidates = {
        "node-parent": Candidate(
            "node-parent", "node", "Parent", "", "search"
        ),
        "node-child": Candidate(
            "node-child",
            "node",
            "Child",
            "",
            "search",
            parent_ids=["node-parent"],
        ),
    }
    registry = ToolRegistry()
    registry.meta = {
        "activate_node": ToolMeta(
            "activate_node",
            ToolRole.CREATE,
            {"node_id"},
            {"node_id": "node"},
        )
    }

    shortlist = registry.shortlist(ledger, DecisionCard(), limit=5)
    assert [candidate.candidate_id for candidate in shortlist] == ["node-child"]


def test_framework_recommendation_persists_the_rendered_candidate_order():
    agent = object.__new__(ADAPTAgent)
    agent.task_spec = TaskSpec.compile("帮我推荐一个星体")
    agent.runtime = TaskRuntime.begin(agent.task_spec)
    agent.runtime.phase = RuntimePhase.SELECT
    agent.ledger = CandidateLedger()
    agent.ledger.candidates = {
        "Q-2": Candidate("Q-2", "quasar", "Second Quasar", "", "search"),
        "Q-1": Candidate("Q-1", "quasar", "First Quasar", "", "search"),
    }
    agent.decision_card = DecisionCard()
    agent.tool_registry = ToolRegistry()
    agent.operations = OperationJournal()
    agent.responses = ResponseJournal()

    message = agent._framework_recommendation()

    assert message is not None
    snapshot = agent.responses.latest_snapshot(
        agent.operations.epoch, "recommendation"
    )
    assert snapshot is not None
    assert snapshot[1] == ("Q-2", "Q-1")
    assert message.content.index("Second Quasar") < message.content.index("First Quasar")


def test_explicit_ordinal_after_visible_recommendation_authorizes_create():
    class Debug:
        def emit(self, *args, **kwargs):
            pass

    class Proactive:
        pending_question = ""

    class Memory:
        proactive = Proactive()

    agent = object.__new__(ADAPTAgent)
    agent.task_spec = TaskSpec.compile("帮我推荐一份晚餐")
    agent.runtime = TaskRuntime.begin(agent.task_spec)
    agent.runtime.phase = RuntimePhase.DONE
    agent.ledger = CandidateLedger()
    agent.ledger.candidates["P-1"] = Candidate(
        "P-1", "product", "First option", "", "search_options"
    )
    agent.decision_card = DecisionCard()
    agent.tool_registry = ToolRegistry()
    agent.tool_registry.meta = {
        "create_order": ToolMeta(
            "create_order",
            ToolRole.CREATE,
            {"product_id"},
            {"product_id": "product"},
        )
    }
    agent.operations = OperationJournal()
    agent.responses = ResponseJournal()
    agent.debug = Debug()
    agent.memory = Memory()
    agent.enable_lessons = False
    agent.responses.commit(
        agent.operations.epoch,
        agent.ledger.candidate_version,
        "recommendation",
        candidate_ids=("P-1",),
    )

    agent._observe_input(UserMessage(role="user", content="行，就第一个吧。"))

    assert agent.runtime.authorization.create_authorized
    assert agent.runtime.selected_candidate_id == "P-1"
    assert agent.runtime.phase == RuntimePhase.READY_TO_CREATE
    assert agent.operations.epoch == 2


def test_ordinal_without_visible_recommendation_does_not_grant_authorization():
    agent = object.__new__(ADAPTAgent)
    agent.runtime = TaskRuntime.begin(TaskSpec.compile("帮我推荐一份晚餐"))
    agent.ledger = CandidateLedger()
    agent.ledger.candidates["P-1"] = Candidate(
        "P-1", "product", "First option", "", "search_options"
    )
    agent.decision_card = DecisionCard()
    agent.tool_registry = ToolRegistry()
    agent.tool_registry.meta = {
        "create_order": ToolMeta(
            "create_order", ToolRole.CREATE, {"product_id"}, {"product_id": "product"}
        )
    }
    agent.operations = OperationJournal()
    agent.responses = ResponseJournal()

    agent._resolve_user_selection("第一个")
    assert not agent.runtime.authorization.create_authorized


def test_ordinal_binds_the_rendered_snapshot_not_a_recomputed_ranking():
    agent = object.__new__(ADAPTAgent)
    agent.runtime = TaskRuntime.begin(TaskSpec.compile("帮我推荐一个选项"))
    agent.runtime.phase = RuntimePhase.DONE
    agent.ledger = CandidateLedger()
    agent.ledger.candidates = {
        "P-1": Candidate("P-1", "product", "Preferred", "blue", "search"),
        "P-2": Candidate("P-2", "product", "Displayed first", "red", "search"),
    }
    agent.decision_card = DecisionCard(prefer=["blue"])
    assert agent.ledger.shortlist(agent.decision_card)[0].candidate_id == "P-1"
    agent.tool_registry = ToolRegistry()
    agent.tool_registry.meta = {
        "create_order": ToolMeta(
            "create_order", ToolRole.CREATE, {"product_id"}, {"product_id": "product"}
        )
    }
    agent.operations = OperationJournal()
    agent.responses = ResponseJournal()
    agent.responses.commit(
        agent.operations.epoch,
        agent.ledger.candidate_version,
        "recommendation",
        candidate_ids=("P-2", "P-1"),
    )

    agent._resolve_user_selection("就选第一个")
    assert agent.runtime.selected_candidate_id == "P-2"
    assert agent.runtime.authorization.create_authorized


def test_agent_stop_protocol_is_not_rejected_by_preflight():
    agent = object.__new__(ADAPTAgent)
    agent.task_spec = TaskSpec.compile("帮我推荐一个选项")
    agent.runtime = TaskRuntime.begin(agent.task_spec)
    agent.question_gate = QuestionGate()
    assert not agent._preflight(
        AssistantMessage(role="assistant", content="###STOP###"), []
    )


def test_question_budget_changes_only_on_commit_and_answer_is_recorded():
    memory = ADAPTMemory(language="chinese")
    memory.begin_subtask("下周去上海，帮我买票")
    question = memory.propose_question("下周去上海，帮我买票")
    assert question
    assert memory.proactive.asked_this_subtask == 0
    assert memory.commit_question(question)
    assert not memory.commit_question(question)
    assert memory.proactive.asked_this_subtask == 1
    assert memory.record_user_answer("坐高铁")
    assert any(fact.value == "坐高铁" for fact in memory.facts)
    assert any(fact.dimension == "transport" for fact in memory.facts)


def test_identical_consecutive_subtasks_receive_independent_question_budgets():
    memory = ADAPTMemory(language="chinese")
    instruction = "帮我订一家酒店"

    memory.begin_subtask(instruction)
    first_question = memory.propose_question(instruction, "ota")
    assert first_question
    assert memory.commit_question(first_question)
    assert memory.proactive.asked_this_subtask == 1

    memory.begin_subtask(instruction)
    assert memory.proactive.asked_this_subtask == 0
    assert memory.proactive.pending_question is None
    assert memory.propose_question(instruction, "ota") == first_question


def test_stable_room_preference_resolves_runtime_gap_and_suppresses_repeat_question():
    memory = ADAPTMemory(language="chinese")
    memory.fact_store.ingest(PreferenceFact(
        "room", "ota", "hotel", "product", "云端双床房两晚", "positive",
        0.9, "2026-01-01", "order", category="hotel",
    ))
    instruction = "帮我订一家酒店"
    assert memory.resolve_task_slots(instruction)["room_type"] == "双床房"
    assert memory.propose_question(instruction, "ota") is None
    spec = TaskSpec.compile(instruction)
    spec.resolved_slots.update(memory.resolve_task_slots(instruction))
    runtime = TaskRuntime.begin(spec)
    assert "room_type" not in runtime.critical_gaps()


def test_weak_or_conflicting_room_history_does_not_suppress_question():
    memory = ADAPTMemory(language="chinese")
    memory.fact_store.ingest(PreferenceFact(
        "weak", "ota", "hotel", "product", "临湖双床房", "positive",
        0.5, "2026-01-01", "search", category="hotel",
        decision_eligible=False,
    ))
    instruction = "帮我订一家酒店"
    assert memory.propose_question(instruction, "ota")
    memory.fact_store.ingest(PreferenceFact(
        "twin", "ota", "hotel", "room_type", "双床房", "positive",
        0.9, "2026-01-02", "conversation", category="hotel",
    ))
    memory.fact_store.ingest(PreferenceFact(
        "queen", "ota", "hotel", "room_type", "大床房", "positive",
        0.9, "2026-01-03", "conversation", category="hotel",
    ))
    assert "room_type" not in memory.resolve_task_slots(instruction)
    assert memory.propose_question(instruction, "ota")


def test_framework_answer_is_written_to_the_typed_task_slot():
    memory = ADAPTMemory(language="chinese")
    memory.begin_subtask("帮我买一双拖鞋送到家")
    assert memory.commit_question("请告诉我需要的尺码。")
    assert memory.record_user_answer("42-43码", dimension="size")
    fact = next(fact for fact in memory.facts if fact.value == "42-43码")
    assert fact.dimension == "size"
    assert fact.facet == "retail"


def test_multiple_avoids_and_brands_do_not_compete():
    drift = DriftDetector(drift_threshold=2)
    signals = [
        Signal("avoids_food", "香菜", 0.9, "2026-01-01", "complaint"),
        Signal("avoids_food", "花生", 0.9, "2026-01-02", "complaint"),
        Signal("brand_loyalty", "霸王茶姬", 0.8, "2026-01-03", "order", raw="奶茶"),
        Signal("brand_loyalty", "亚朵酒店", 0.8, "2026-01-04", "order", raw="酒店"),
    ]
    for signal in signals:
        drift.observe(signal)
    assert not drift.drift_summary()


def test_candidate_ledger_rejects_unobserved_id_and_wrong_variant():
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "StoreProduct(store_name=霸王茶姬, store_id=S1_S00001, "
        "product_name=糯糯青山(大杯), product_id=S1_P00001, "
        "attributes=口味:半糖, 温度:多冰, quantity=2)",
    )
    card = DecisionCard(must=["糯糯青山", "少糖", "多冰"])
    wrong = ledger.validate_write(
        "create_delivery_order",
        {"user_id": "U1", "store_id": "S1_S00001", "product_ids": ["S1_P99999"]},
        card,
    )
    assert any("was not returned" in error for error in wrong)
    assert any("少糖" in error for error in wrong)


def test_candidate_ledger_accepts_exact_observed_variant():
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "StoreProduct(store_name=霸王茶姬, store_id=S1_S00001, "
        "product_name=糯糯青山, product_id=S1_P00001, "
        "attributes=口味:少糖, 温度:多冰, quantity=2)",
    )
    card = DecisionCard(must=["糯糯青山", "少糖", "多冰"])
    assert not ledger.validate_write(
        "create_delivery_order",
        {"user_id": "U1", "store_id": "S1_S00001", "product_ids": ["S1_P00001"]},
        card,
    )


def test_write_can_choose_within_shortlist_but_respects_explicit_selection():
    ledger = CandidateLedger()
    ledger.observe(
        "instore_product_search_recommend",
        "\n".join(
            [
                "ShopProduct(shop_id=S1_I00001, product_id=S1_P00001, "
                "name=酸菜鱼汤锅套餐（含茅蒿/冰汤圆）, price=118, quantity=20)",
                "ShopProduct(shop_id=S1_I00002, product_id=S1_P00002, "
                "name=麻辣火锅套餐（含茅蒿/冰汤圆）, price=168, quantity=20)",
            ]
        ),
    )
    # Both candidates have equal active evidence, so framework ranking must
    # leave the choice to the policy model until the user explicitly selects.
    card = DecisionCard(prefer=["茅蒿", "冰汤圆"])
    assert not ledger.validate_ranked_choice(
        {"shop_id": "S1_I00001", "product_id": "S1_P00001"}, card
    )
    assert not ledger.validate_ranked_choice(
        {"shop_id": "S1_I00002", "product_id": "S1_P00002"}, card
    )
    errors = ledger.validate_ranked_choice(
        {"shop_id": "S1_I00001", "product_id": "S1_P00001"},
        card,
        selected_candidate_id="S1_P00002",
    )
    assert any("explicitly selected" in error for error in errors)


def test_unique_preference_evidence_leader_ranks_but_does_not_lock_write():
    ledger = CandidateLedger()
    ledger.observe(
        "instore_product_search_recommend",
        "\n".join(
            [
                "ShopProduct(shop_id=S1_I00001, product_id=S1_P00081, "
                "name=酸菜鱼汤锅套餐（含茼蒿/冰汤圆）, price=118, quantity=20)",
                "ShopProduct(shop_id=S1_I00002, product_id=S1_P10101, "
                "name=麻辣火锅聚餐4人套餐（含茼蒿/毛肚/鸭血/肥牛/冰汤圆）, "
                "price=168, quantity=20)",
            ]
        ),
    )
    card = DecisionCard(prefer=["麻辣火锅", "茼蒿", "冰汤圆"])
    leader = ledger.unique_evidence_leader(card)
    assert leader is not None
    assert leader.candidate_id == "S1_P10101"
    errors = ledger.validate_ranked_choice(
        {"shop_id": "S1_I00001", "product_id": "S1_P00081"}, card
    )
    assert not errors
    assert not ledger.validate_ranked_choice(
        {"shop_id": "S1_I00002", "product_id": "S1_P10101"}, card
    )


def test_soft_ranking_never_makes_a_grounded_candidate_inadmissible():
    ledger = CandidateLedger()
    for index in range(9):
        candidate_id = f"P-{index}"
        ledger.candidates[candidate_id] = Candidate(
            candidate_id,
            "quasar",
            f"Quasar {index}",
            "preferred" if index < 8 else "fallback",
            "search_quasars",
        )
    card = DecisionCard(prefer=["preferred"])
    assert "P-8" not in {
        candidate.candidate_id for candidate in ledger.shortlist(card, limit=8)
    }
    assert not ledger.validate_ranked_choice(
        {"quasar_id": "P-8"},
        card,
        id_arguments={"quasar_id": "quasar"},
    )


def test_tied_preference_evidence_does_not_lock_framework_choice():
    ledger = CandidateLedger()
    ledger.observe(
        "instore_product_search_recommend",
        "\n".join(
            [
                "ShopProduct(shop_id=S1_I00001, product_id=S1_P00001, "
                "name=麻辣火锅套餐A, price=118, quantity=20)",
                "ShopProduct(shop_id=S1_I00002, product_id=S1_P00002, "
                "name=麻辣火锅套餐B, price=168, quantity=20)",
            ]
        ),
    )
    card = DecisionCard(prefer=["麻辣火锅"])
    assert ledger.unique_evidence_leader(card) is None
    assert not ledger.validate_ranked_choice(
        {"shop_id": "S1_I00001", "product_id": "S1_P00001"}, card
    )
    assert not ledger.validate_ranked_choice(
        {"shop_id": "S1_I00002", "product_id": "S1_P00002"}, card
    )


def test_unique_evidence_leader_is_not_assumed_to_be_scalar_rank_first():
    ledger = CandidateLedger()
    ledger.observe(
        "instore_product_search_recommend",
        "\n".join(
            [
                "ShopProduct(shop_id=S1_I00001, product_id=S1_P00001, "
                "name=新近单项券, price=9, quantity=100, tags=['单项'])",
                "ShopProduct(shop_id=S1_I00002, product_id=S1_P00002, "
                "name=稳定组合券, price=99, quantity=10, "
                "tags=['偏好甲', '偏好乙'])",
            ]
        ),
    )
    card = DecisionCard(prefer=["单项", "偏好甲", "偏好乙"])

    # Exercise the invariant directly: evidence leadership is the unique
    # decisive-score maximum, regardless of any scalar shortlist ordering.
    with patch.object(
        CandidateRanker,
        "decisive_preference_scores",
        return_value={"S1_P00001": 1.0, "S1_P00002": 2.0},
    ):
        leader = ledger.unique_evidence_leader(card)

    assert leader is not None
    assert leader.candidate_id == "S1_P00002"


def test_open_world_alignment_induces_unseen_attribute_keys_from_candidates():
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "\n".join(
            [
                "StoreProduct(store_id=S1_S00001, product_id=S1_P00001, "
                "product_name=星云机械键盘, attributes=轴体：静音红轴, "
                "配列：75键, 连接：三模, quantity=8, "
                "tags=['静音红轴', '75键', '三模'])",
                "StoreProduct(store_id=S1_S00002, product_id=S1_P00002, "
                "product_name=极光机械键盘, attributes=轴体：青轴, "
                "配列：104键, 连接：有线, quantity=8, "
                "tags=['青轴', '104键', '有线'])",
            ]
        ),
    )
    candidates = [
        candidate
        for candidate in ledger.candidates.values()
        if candidate.entity_type == "product"
    ]
    card = DecisionCard(prefer=["静音红轴75键三模"])
    alignment = EvidenceAlignment(
        candidates, card.prefer, lambda value, raw: value in raw
    )
    atoms = {(atom.attribute_key, atom.value) for atom in alignment.atoms}
    assert {"静音红轴", "75键", "三模"} == {value for _, value in atoms}
    assert any(key == "轴体" for key, value in atoms if value == "静音红轴")
    counts = CandidateRanker.preference_match_counts(candidates, card)
    assert counts["S1_P00001"] == 3
    assert counts["S1_P00002"] == 0


def test_composite_preference_coverage_uses_same_atoms_for_every_candidate():
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "\n".join(
            [
                "StoreProduct(store_id=S1_S00001, product_id=S1_P00001, "
                "product_name=系列A角色甲Q版摆件, quantity=5, "
                "tags=['系列A', '角色甲', 'Q版', '摆件'])",
                "StoreProduct(store_id=S1_S00002, product_id=S1_P00002, "
                "product_name=系列A角色甲挂件, quantity=5, "
                "tags=['系列A', '角色甲', '挂件'])",
                "StoreProduct(store_id=S1_S00003, product_id=S1_P00003, "
                "product_name=系列A随机款, quantity=5, "
                "tags=['系列A', '随机'])",
            ]
        ),
    )
    card = DecisionCard(prefer=["系列A角色甲Q版挂件"])
    candidates = [
        candidate
        for candidate in ledger.candidates.values()
        if candidate.entity_type == "product"
    ]
    counts = CandidateRanker.preference_match_counts(candidates, card)
    assert counts == {
        "S1_P00001": 3,
        "S1_P00002": 3,
        "S1_P00003": 1,
    }
    assert ledger.unique_evidence_leader(card) is None
    gap = ledger.preference_coverage_gap(
        {"store_id": "S1_S00003", "product_ids": ["S1_P00003"]}, card
    )
    assert "maximum observable preference-coverage set" in gap


def test_open_world_alignment_does_not_transfer_attributes_across_candidate_sets():
    retail = CandidateLedger()
    retail.observe(
        "delivery_product_search_recommand",
        "StoreProduct(store_id=S1_S00001, product_id=S1_P00001, "
        "product_name=轻量跑鞋, attributes=重量：轻量, quantity=5, "
        "tags=['轻量'])",
    )
    hotel = CandidateLedger()
    hotel.observe(
        "hotel_search_recommand",
        "Hotel(hotel_id=S1_H00001, hotel_name=安静花园酒店, "
        "attributes=环境：安静, tags=['安静'])",
    )
    retail_counts = CandidateRanker.preference_match_counts(
        list(retail.candidates.values()), DecisionCard(prefer=["轻量"])
    )
    hotel_counts = CandidateRanker.preference_match_counts(
        list(hotel.candidates.values()), DecisionCard(prefer=["轻量"])
    )
    assert retail_counts["S1_P00001"] == 1
    assert hotel_counts["S1_H00001"] == 0


def test_structured_hotel_scenario_scopes_unknown_room_name_without_text_markers():
    signal = Signal(
        "prefers_product",
        "静谧睡眠空间一晚",
        0.7,
        "2026-01-01 10:00:00",
        "order",
        '{"scenario":"hotel","merchant_name":"云栖居",'
        '"items":[{"product_name":"静谧睡眠空间一晚"}]}',
    )
    fact = fact_from_signal(signal)
    assert fact.scope == "ota"
    assert fact.facet == "hotel"


def test_structured_travel_scenario_uses_observed_transport_evidence():
    signal = Signal(
        "prefers_product",
        "晨间经济舱",
        0.7,
        "2026-01-01 10:00:00",
        "order",
        '{"scenario":"travel_ticket","tags":["飞机"],'
        '"items":[{"product_name":"晨间经济舱"}]}',
    )
    fact = fact_from_signal(signal)
    assert fact.scope == "ota"
    assert fact.facet == "flight"


def test_hotel_location_preference_aligns_to_more_specific_observed_tag():
    memory = ADAPTMemory(language="chinese")
    memory.update(
        [
            {
                "date": "2026-01-01",
                "behavior": [
                    {
                        "behavior_type": "order",
                        "content": {
                            "scenario": "hotel",
                            "merchant_name": "云栖居",
                            "tags": ["近地铁口"],
                            "items": [
                                {
                                    "product_name": "静谧睡眠空间一晚",
                                    "price": 300,
                                    "quantity": 1,
                                }
                            ],
                        },
                    }
                ],
                "dialogue": [],
            }
        ]
    )
    card = memory.compile_task("帮我订一晚酒店")
    ledger = CandidateLedger()
    ledger.observe(
        "hotel_search_recommand",
        "Hotel(hotel_id=S9_H00001, hotel_name=远山居, "
        "score=4.8, tags=['近地铁口'])",
    )
    candidate = ledger.candidates["S9_H00001"]
    alignment = CandidateRanker.preference_alignment([candidate], card)
    assert any(atom.value == "近地铁" for atom in alignment.matches(candidate))


def test_candidate_grounding_recovers_fact_even_when_fixed_facet_is_wrong():
    """Facet routing may miss, but observed attributes get the final say."""
    fact = PreferenceFact(
        fact_id="f-brand",
        scope="delivery",
        facet="restaurant",
        dimension="product",
        value="青岛啤酒经典",
        polarity="positive",
        confidence=0.9,
        observed_at="2024-11-01",
        source_type="order",
    )
    card = build_decision_card(
        TaskSpec.compile("帮我买两瓶酒送到家"), [fact]
    )
    # It stays out of the compact prompt because the inferred facet differs,
    # but remains eligible for post-search grounding.
    assert "青岛啤酒经典" not in card.prefer
    assert "青岛啤酒经典" in card.preference_pool

    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "StoreProduct(store_id=S1_S00001, product_id=S1_P00001, "
        "product_name=青岛经典啤酒500ml, attributes=品牌：青岛, "
        "类型：啤酒, quantity=10, tags=['青岛', '经典', '啤酒'])\n"
        "StoreProduct(store_id=S1_S00002, product_id=S1_P00002, "
        "product_name=雪花清爽啤酒500ml, attributes=品牌：雪花, "
        "类型：啤酒, quantity=10, tags=['雪花', '清爽', '啤酒'])",
    )
    candidates = [
        candidate for candidate in ledger.candidates.values()
        if candidate.entity_type == "product"
    ]
    counts = CandidateRanker.preference_match_counts(candidates, card)
    assert counts["S1_P00001"] > counts["S1_P00002"]


def test_second_stage_retrieval_recovers_fact_crowded_out_before_search():
    memory = ADAPTMemory(language="chinese")
    for index in range(100):
        memory.fact_store.ingest(PreferenceFact(
            f"noise-{index}", "ota", "hotel", "product", f"旅居历史商品{index}",
            "positive", 0.99, f"2026-03-{index % 28 + 1:02d}", "order",
            category="hotel",
        ))
    remembered = PreferenceFact(
        "remembered", "instore", "service", "product", "星河静音三模鼠标",
        "positive", 0.55, "2025-01-01", "order", category="retail",
    )
    memory.fact_store.ingest(remembered)
    card = memory.compile_task("帮我买个鼠标送到家")
    assert remembered.value not in card.preference_pool

    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "StoreProduct(store_id=S1_S00001, product_id=S1_P00001, "
        "product_name=星河静音三模鼠标, quantity=5, tags=['静音', '三模'])\n"
        "StoreProduct(store_id=S1_S00002, product_id=S1_P00002, "
        "product_name=普通有线鼠标, quantity=5, tags=['有线'])",
    )
    stats = memory.apply_candidate_grounding(
        card, list(ledger.candidates.values())
    )
    assert remembered.value in card.preference_pool
    assert not any(value.startswith("旅居历史商品") for value in card.preference_pool)
    assert stats["grounded_positive"] == 1
    assert ledger.unique_evidence_leader(card).candidate_id == "S1_P00001"


def test_second_stage_retrieval_uses_candidate_fields_for_wrong_facet_and_avoid():
    memory = ADAPTMemory(language="chinese")
    memory.fact_store.ingest(PreferenceFact(
        "location", "delivery", "retail", "attribute", "近地铁", "positive",
        0.8, "2026-01-01", "order", category="retail",
    ))
    memory.fact_store.ingest(PreferenceFact(
        "avoid", "delivery", "beverage", "avoid", "临街嘈杂", "negative",
        0.9, "2026-01-02", "complaint", category="临街嘈杂",
    ))
    card = memory.compile_task("帮我订一晚酒店")
    ledger = CandidateLedger()
    ledger.observe(
        "hotel_search_recommand",
        "Hotel(hotel_id=S9_H00001, hotel_name=云庭酒店, "
        "tags=['近地铁口', '临街嘈杂'], quantity=3)\n"
        "Hotel(hotel_id=S9_H00002, hotel_name=山居酒店, "
        "tags=['安静'], quantity=3)",
    )
    stats = memory.apply_candidate_grounding(card, list(ledger.candidates.values()))
    assert "近地铁" in card.preference_pool
    assert "临街嘈杂" in card.avoid
    assert stats["grounded_negative"] == 1
    ranked = ledger.shortlist(card)
    assert [candidate.candidate_id for candidate in ranked] == ["S9_H00002"]


def test_second_stage_retrieval_preserves_multisource_decisiveness():
    memory = ADAPTMemory(language="chinese")
    for fact_id, source in (("search", "search"), ("browse", "high_freq_browse")):
        memory.facts.append(PreferenceFact(
            fact_id, "delivery", "retail", "product", "系列X", "positive",
            0.5, "2026-01-01", source, evidence_types=[source],
            decision_eligible=False,
        ))
    card = memory.compile_task("帮我买双运动鞋")
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "StoreProduct(store_id=S1_S00001, product_id=S1_P00001, "
        "product_name=系列X联名鞋, quantity=5, tags=['系列X'])\n"
        "StoreProduct(store_id=S1_S00002, product_id=S1_P00002, "
        "product_name=系列Y联名鞋, quantity=5, tags=['系列Y'])",
    )
    memory.apply_candidate_grounding(card, list(ledger.candidates.values()))
    assert set(card.preference_source_types["系列X"]) == {"search", "high_freq_browse"}
    assert ledger.unique_evidence_leader(card).candidate_id == "S1_P00001"


def test_tiered_runtime_routes_entities_and_protects_non_entity_facts():
    memory = ADAPTMemory(
        language="chinese", entity_index_max_entries=500
    )
    for index in range(520):
        memory.facts.append(PreferenceFact(
            f"entity-{index}", "delivery", "retail", "product", f"开放实体{index}",
            "positive", 0.7, f"2026-01-{index % 28 + 1:02d}", "order",
            category="retail",
        ))
    protected = PreferenceFact(
        "safety", "delivery", "restaurant", "safety", "花生", "negative",
        0.95, "2026-02-01", "conversation", category="花生",
    )
    memory.facts.append(protected)
    assert len(memory.entity_index) == 500
    assert memory.preference_store.facts == [protected]
    assert len(memory.facts) == 501


def test_tiered_runtime_fairness_keeps_new_small_facet_bucket():
    memory = ADAPTMemory(
        language="chinese", entity_index_max_entries=4
    )
    for index in range(8):
        memory.facts.append(PreferenceFact(
            f"retail-{index}", "delivery", "retail", "product", f"零售实体{index}",
            "positive", 0.9, "2026-01-01", "order", category="retail",
        ))
    hotel = PreferenceFact(
        "hotel", "ota", "hotel", "product", "静谧双床空间", "positive",
        0.6, "2026-01-02", "order", category="hotel",
    )
    memory.facts.append(hotel)
    assert hotel.value in {fact.value for fact in memory.entity_index.facts}
    assert len(memory.entity_index) == 4


def test_tiered_runtime_feature_flag_restores_mixed_store():
    memory = ADAPTMemory(
        language="chinese", enable_tiered_compaction=False
    )
    entity = PreferenceFact(
        "entity", "delivery", "retail", "product", "传统存储实体", "positive",
        0.7, "2026-01-01", "order", category="retail",
    )
    memory.facts.append(entity)
    assert memory.preference_store.facts == [entity]
    assert len(memory.entity_index) == 0
    assert memory.storage_stats()["tiered_compaction"] is False


def test_tiered_runtime_exact_aggregation_preserves_multisource_strength():
    memory = ADAPTMemory(language="chinese")
    for fact_id, value, source in (
        ("search", "星河·经典款", "search"),
        ("browse", "星河经典款", "high_freq_browse"),
    ):
        memory.facts.append(PreferenceFact(
            fact_id, "delivery", "retail", "product", value, "positive",
            0.5, "2026-01-01", source, category="retail",
            evidence_types=[source], decision_eligible=False,
        ))
    assert len(memory.entity_index) == 1
    merged = memory.entity_index.facts[0]
    assert set(merged.evidence_types) == {"search", "high_freq_browse"}
    assert merged.decision_eligible


def test_current_instruction_is_an_open_world_alignment_source():
    card = build_decision_card(
        TaskSpec.compile("帮我买一个三模静音键盘"), []
    )
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "StoreProduct(store_id=S1_S00001, product_id=S1_P00001, "
        "product_name=星云键盘, attributes=连接：三模, quantity=5, "
        "tags=['三模', '静音'])\n"
        "StoreProduct(store_id=S1_S00002, product_id=S1_P00002, "
        "product_name=极光键盘, attributes=连接：有线, quantity=5, "
        "tags=['有线', '青轴'])",
    )
    candidates = [
        candidate for candidate in ledger.candidates.values()
        if candidate.entity_type == "product"
    ]
    counts = CandidateRanker.preference_match_counts(candidates, card)
    assert counts["S1_P00001"] == 2
    assert counts["S1_P00002"] == 0


def test_grounded_atoms_preserve_observable_fact_strength_without_duplicates():
    card = DecisionCard(
        preference_pool=["品牌甲经典", "品牌乙经典", "品牌甲"],
        preference_weights={"品牌甲经典": 0.95, "品牌乙经典": 0.55, "品牌甲": 0.7},
    )
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "StoreProduct(store_id=S1_S00001, product_id=S1_P00001, "
        "product_name=品牌甲经典款, quantity=5, tags=['品牌甲', '经典'])\n"
        "StoreProduct(store_id=S1_S00002, product_id=S1_P00002, "
        "product_name=品牌乙经典款, quantity=5, tags=['品牌乙', '经典'])",
    )
    candidates = [
        candidate for candidate in ledger.candidates.values()
        if candidate.entity_type == "product"
    ]
    alignment = CandidateRanker.preference_alignment(candidates, card)
    # 经典 is one attribute even though several source facts repeat it.
    assert [atom.value for atom in alignment.atoms].count("经典") == 1
    scores = CandidateRanker.preference_scores(candidates, card)
    assert scores["S1_P00001"] > scores["S1_P00002"]


def test_candidate_render_exposes_dynamic_preference_evidence():
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "StoreProduct(store_id=S1_S00001, product_id=S1_P00001, "
        "product_name=三模键盘, quantity=5, tags=['三模'])",
    )
    rendered = ledger.render(DecisionCard(prefer=["三模"]))
    assert "preference_evidence=['三模']" in rendered


def test_preference_undercoverage_remains_soft_ranking_evidence():
    class Tool:
        name = "create_delivery_order"

    class Debug:
        def emit(self, *args, **kwargs):
            pass

    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "\n".join(
            [
                "StoreProduct(store_id=S1_S00001, product_id=S1_P00001, "
                "product_name=系列B静音三模款, quantity=5, "
                "tags=['系列B', '静音', '三模'])",
                "StoreProduct(store_id=S1_S00002, product_id=S1_P00002, "
                "product_name=系列B静音款, quantity=5, "
                "tags=['系列B', '静音'])",
                "StoreProduct(store_id=S1_S00003, product_id=S1_P00003, "
                "product_name=系列B静音三模备选款, quantity=5, "
                "tags=['系列B', '静音', '三模'])",
            ]
        ),
    )
    agent = object.__new__(ADAPTAgent)
    agent.runtime = TaskRuntime.begin(TaskSpec.compile("帮我买个键盘送到家里"))
    agent.runtime.phase = RuntimePhase.READY_TO_CREATE
    agent.tool_registry = ToolRegistry()
    agent.tool_registry.meta = {
        "create_delivery_order": ToolMeta(
            "create_delivery_order", ToolRole.CREATE, set(), {}
        )
    }
    agent.tool_errors = ToolErrorLedger()
    agent.ledger = ledger
    agent.decision_card = DecisionCard(prefer=["系列B静音三模"])
    agent.user_profile = {}
    agent.question_gate = QuestionGate()
    agent.enable_candidate_validation = True
    agent.debug = Debug()
    observed = []
    agent._record_lesson = lambda failure, trigger, correction: observed.append(failure)
    lower = ToolCall(
        id="lower-evidence",
        name="create_delivery_order",
        arguments={
            "store_id": "S1_S00002",
            "product_ids": ["S1_P00002"],
        },
    )

    assert not agent._preflight(
        AssistantMessage(role="assistant", tool_calls=[lower]), [Tool()]
    )
    assert observed == []

    agent.ledger.require_max_preference_coverage = True
    problems = agent._preflight(
        AssistantMessage(role="assistant", tool_calls=[lower]), [Tool()]
    )
    assert not any("maximum observable preference-coverage set" in p for p in problems)

    best = ToolCall(
        id="best-evidence",
        name="create_delivery_order",
        arguments={
            "store_id": "S1_S00001",
            "product_ids": ["S1_P00001"],
        },
    )
    assert not agent._preflight(
        AssistantMessage(role="assistant", tool_calls=[best]), [Tool()]
    )


def test_shortlist_hard_filters_wrong_product_category():
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "\n".join(
            [
                "StoreProduct(store_id=S1_S00001, product_id=S1_P00001, "
                "product_name=软欧包(原味), price=15, quantity=30)",
                "StoreProduct(store_id=S1_S00002, product_id=S1_P00002, "
                "product_name=四季奶茶(无小料), price=12, quantity=30)",
            ]
        ),
    )
    card = DecisionCard(
        must=["奶茶"],
        prefer=["原味"],
        constraints=[Constraint("类别", "奶茶")],
    )
    shortlist = ledger.shortlist(card)
    assert [candidate.candidate_id for candidate in shortlist] == ["S1_P00002"]


def test_unserialized_category_hypernym_does_not_erase_observed_candidates():
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "StoreProduct(store_id=S1_S00001, product_id=S1_P00001, "
        "product_name=秋季连帽卫衣, attributes=适合季节：秋季, "
        "quantity=10, tags=['卫衣', '秋装', '保暖'])\n"
        "StoreProduct(store_id=S1_S00001, product_id=S1_P00002, "
        "product_name=秋季摇粒绒外套, attributes=适合季节：秋季, "
        "quantity=8, tags=['外套', '秋装', '保暖'])",
    )
    card = build_decision_card(
        TaskSpec.compile("帮我买两件秋天穿的衣服送到家"), []
    )
    assert {candidate.candidate_id for candidate in ledger.shortlist(card)} == {
        "S1_P00001", "S1_P00002"
    }
    assert not ledger.validate_write(
        "create_delivery_order",
        {
            "store_id": "S1_S00001",
            "product_ids": ["S1_P00001", "S1_P00002"],
            "address": "测试收货地址",
        },
        card,
    )


def test_task_identity_uses_candidate_name_not_parent_name():
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "\n".join(
            [
                "StoreProduct(store_name=星港航标核心总汇, store_id=S1_S00001, "
                "product_name=折叠绳, product_id=S1_P00001, quantity=30)",
                "StoreProduct(store_name=星港航标核心总汇, store_id=S1_S00001, "
                "product_name=航标核心, product_id=S1_P00002, quantity=20)",
                "StoreProduct(store_name=星港航标核心总汇, store_id=S1_S00001, "
                "product_name=防尘套, product_id=S1_P00003, quantity=40)",
            ]
        ),
    )
    spec = TaskSpec.compile("给我推荐一个航标核心")
    card = build_decision_card(spec, [])
    assert [candidate.candidate_id for candidate in ledger.shortlist(card)] == [
        "S1_P00002"
    ]


def test_unannotated_candidate_fields_rank_but_do_not_become_hard_constraints():
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "\n".join(
            [
                "StoreProduct(store_id=S1_S00001, product_name=极光棱镜, "
                "product_id=S1_P00001, resonance: 低频, quantity=30)",
                "StoreProduct(store_id=S1_S00001, product_name=极光棱镜, "
                "product_id=S1_P00002, resonance: 高频, quantity=20)",
            ]
        ),
    )
    card = DecisionCard(task_intent=["请选低频的极光棱镜"])
    grounded = ledger.ground_task_constraints(card)
    assert grounded == []
    assert [candidate.candidate_id for candidate in ledger.shortlist(card)] == [
        "S1_P00001",
        "S1_P00002",
    ]


def test_unannotated_service_rows_do_not_form_cross_candidate_conjunction():
    ledger = CandidateLedger()
    ledger.observe(
        "instore_product_search",
        "\n".join(
            [
                "ShopProduct(shop_id=S2_S00001, product_id=S2_P00001, "
                "product_name=经典造型券, service_type=理发, quantity=8)",
                "ShopProduct(shop_id=S2_S00001, product_id=S2_P00002, "
                "product_name=夏日套餐, service_type=护理, quantity=6)",
            ]
        ),
    )
    card = DecisionCard(task_intent=["帮我买一个理发店套餐"])
    assert ledger.ground_task_constraints(card) == []

    registry = ToolRegistry()
    registry.meta = {
        "create_instore_product_order": ToolMeta(
            "create_instore_product_order",
            ToolRole.CREATE,
            {"shop_id", "product_id"},
            {"shop_id": "store", "product_id": "product"},
        )
    }
    # The framework may rank the two observed rows, but it must not deadlock
    # SELECT -> CREATE by requiring values taken from different rows.
    assert registry.execution_ready(ledger, card)


def test_retail_signals_preserve_cart_item_and_operational_preferences():
    signals = SignalParser().parse(
        [
            {
                "date": "2026-05-10",
                "behavior": [
                    {
                        "behavior_type": "order",
                        "content": {
                            "scenario": "delivery",
                            "merchant_name": "优衣库",
                            "tags": ["低饱和色系", "0-30分钟配送"],
                            "items": [{"product_name": "基础黑薄外套"}],
                        },
                    },
                    {
                        "behavior_type": "add_to_cart",
                        "content": {
                            "item_name": "Nike男跑步鞋HJ9198-003",
                            "price": 449,
                            "quantity": 1,
                        },
                    },
                ],
            }
        ]
    )
    assert any(s.predicate == "intent_product" and s.object == "Nike男跑步鞋HJ9198-003" for s in signals)
    attributes = {s.object for s in signals if s.predicate == "attribute_preference"}
    assert {"低饱和色系", "配送30分钟内"} <= attributes


def test_observable_interest_fields_are_normalized_without_domain_rules():
    signals = SignalParser().parse(
        [
            {
                "date": "2026-05-11",
                "behavior": [
                    {
                        "behavior_type": "favorite",
                        "content": {
                            "target_name": "系列X联名运动鞋",
                            "target_type": "product",
                        },
                    },
                    {
                        "behavior_type": "high_freq_browse",
                        "content": {"keyword": "系列X联名", "duration_minutes": 18},
                    },
                ],
            }
        ]
    )
    assert any(
        signal.type == "favorite"
        and signal.predicate == "intent_product"
        and signal.object == "系列X联名运动鞋"
        and signal.confidence > 0.8
        for signal in signals
    )
    assert any(
        signal.type == "high_freq_browse"
        and signal.predicate == "observable_interest"
        and signal.object == "系列X联名"
        for signal in signals
    )


def test_single_weak_interest_can_rank_but_cannot_lock_write_choice():
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "StoreProduct(store_id=S1_S00001, product_id=S1_P00001, "
        "product_name=系列X联名鞋, quantity=5, tags=['运动鞋', '系列X'])\n"
        "StoreProduct(store_id=S1_S00002, product_id=S1_P00002, "
        "product_name=系列Y联名鞋, quantity=5, tags=['运动鞋', '系列Y'])",
    )
    weak = PreferenceFact(
        "weak-search", "delivery", "retail", "searches", "系列X", "positive",
        0.4, "2026-05-11", "search", evidence_types=["search"],
        decision_eligible=False,
    )
    card = build_decision_card(TaskSpec.compile("帮我买双运动鞋"), [weak])
    ranked = ledger.shortlist(card)
    assert ranked[0].candidate_id == "S1_P00001"
    assert ledger.unique_evidence_leader(card) is None


def test_favorite_or_multisource_interest_can_be_decisive():
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "StoreProduct(store_id=S1_S00001, product_id=S1_P00001, "
        "product_name=系列X联名鞋, quantity=5, tags=['运动鞋', '系列X'])\n"
        "StoreProduct(store_id=S1_S00002, product_id=S1_P00002, "
        "product_name=系列Y联名鞋, quantity=5, tags=['运动鞋', '系列Y'])",
    )
    favorite = PreferenceFact(
        "favorite", "delivery", "retail", "product", "系列X联名运动鞋",
        "positive", 0.82, "2026-05-11", "favorite",
        evidence_types=["favorite"], decision_eligible=True,
    )
    card = build_decision_card(TaskSpec.compile("帮我买双运动鞋"), [favorite])
    assert ledger.unique_evidence_leader(card).candidate_id == "S1_P00001"

    weak_sources = [
        PreferenceFact(
            "search", "delivery", "retail", "searches", "系列X", "positive",
            0.4, "2026-05-10", "search", evidence_types=["search"],
            decision_eligible=False,
        ),
        PreferenceFact(
            "browse", "delivery", "retail", "product", "系列X", "positive",
            0.6, "2026-05-11", "high_freq_browse",
            evidence_types=["high_freq_browse"], decision_eligible=False,
        ),
    ]
    combined = build_decision_card(
        TaskSpec.compile("帮我买双运动鞋"), weak_sources
    )
    assert ledger.unique_evidence_leader(combined).candidate_id == "S1_P00001"


def test_food_preferences_do_not_cross_facets_but_safety_constraints_do():
    facts = [
        PreferenceFact(
            "restaurant-taste",
            "delivery",
            "restaurant",
            "taste",
            "麻辣锅底",
            "positive",
            0.9,
            "2026-05-15",
            "order",
        ),
        PreferenceFact(
            "restaurant-safety",
            "delivery",
            "restaurant",
            "safety",
            "花生",
            "negative",
            0.9,
            "2026-05-16",
            "conversation",
        ),
    ]
    card = build_decision_card(TaskSpec.compile("帮我点一杯奶茶"), facts)
    assert "麻辣锅底" not in card.prefer
    assert "花生" in card.avoid


def test_category_tagged_facts_wait_for_candidate_grounding():
    facts = [
        PreferenceFact(
            "coffee-temp",
            "delivery",
            "beverage",
            "temperature",
            "冰饮",
            "positive",
            0.9,
            "2026-05-01",
            "order",
            category="咖啡",
        ),
        PreferenceFact(
            "milk-tea-temp",
            "delivery",
            "beverage",
            "temperature",
            "热饮",
            "positive",
            0.9,
            "2026-05-02",
            "order",
            category="奶茶",
        ),
    ]
    card = build_decision_card(TaskSpec.compile("帮我点一杯奶茶"), facts)
    assert "热饮" in card.prefer
    assert "冰饮" not in card.prefer
    assert {"热饮", "冰饮"}.issubset(card.preference_pool)


def test_newer_topping_avoidance_suppresses_old_likes_but_not_new_exception():
    facts = [
        PreferenceFact(
            "old-pearl",
            "delivery",
            "beverage",
            "topping",
            "招牌芋圆奶茶（冰）",
            "positive",
            0.8,
            "2024-10-12",
            "order",
            category="奶茶",
        ),
        PreferenceFact(
            "avoid-topping",
            "delivery",
            "beverage",
            "avoid",
            "小料",
            "negative",
            0.9,
            "2025-12-27",
            "conversation",
            category="小料",
        ),
        PreferenceFact(
            "new-brulee",
            "delivery",
            "beverage",
            "topping",
            "布蕾",
            "positive",
            0.9,
            "2026-05-22",
            "order",
            category="奶茶",
        ),
    ]
    card = build_decision_card(TaskSpec.compile("帮我点杯奶茶"), facts)
    assert "招牌芋圆奶茶（冰）" in card.prefer
    assert "布蕾" in card.prefer
    assert {"招牌芋圆奶茶（冰）", "布蕾"}.issubset(card.preference_pool)


def test_search_normalization_preserves_open_world_semantics():
    agent = ADAPTAgent.__new__(ADAPTAgent)
    agent.task_spec = TaskSpec.compile("帮我点杯喝的送到公司")
    agent.decision_card = DecisionCard(
        avoid=["小料"],
        prefer=[
            "老红糖珍珠奶茶（热）不加小料",
            "珍珠奶茶（冰）不加小料",
        ],
    )
    call = ToolCall(
        id="search",
        name="delivery_product_search_recommand",
        arguments={"keywords": ["老红糖珍珠奶茶", "热", "不加小料", "热"]},
    )
    agent._normalize_search_call(call)
    assert call.arguments["keywords"] == ["老红糖珍珠奶茶", "热", "不加小料"]


def test_no_topping_order_note_suppresses_catalog_topping_signal():
    signals = SignalParser().parse(
        [
            {
                "type": "order",
                "timestamp": "2026-01-01 00:00:00",
                "content": {
                    "merchant_name": "饮品店",
                    "items": [{"product_name": "珍珠奶茶（热）不加小料"}],
                },
            }
        ]
    )
    tastes = {
        signal.object
        for signal in signals
        if signal.predicate == "taste_preference"
    }
    assert "珍珠" not in tastes
    assert "热饮" in tastes


def test_food_avoidance_keeps_its_source_category():
    fact = fact_from_signal(
        Signal(
            "avoids_food",
            "小料",
            0.9,
            "2026-01-01",
            "conversation",
            raw="帮我点杯珍珠奶茶，不加小料",
        )
    )
    assert fact.category == "奶茶"


def test_category_tagged_avoidance_waits_for_candidate_grounding():
    fact = PreferenceFact(
        fact_id="no-topping",
        scope="delivery",
        facet="beverage",
        dimension="avoid",
        value="小料",
        polarity="negative",
        confidence=0.9,
        observed_at="2026-01-01",
        source_type="conversation",
        category="奶茶",
    )
    coffee = build_decision_card(TaskSpec.compile("帮我点个咖啡提神"), [fact])
    broad_drink = build_decision_card(
        TaskSpec.compile("帮我点杯喝的送到公司"), [fact]
    )
    assert "小料" not in coffee.avoid
    assert "小料" not in broad_drink.avoid


def test_open_world_field_preference_ranks_exact_observed_value_before_price():
    ledger = CandidateLedger()
    ledger.observe(
        "instore_product_search_recommend",
        "\n".join(
            [
                "ShopProduct(shop_id=S1_I00001, product_id=S1_P00001, "
                "name=星云套件, core: 晶核, price=168, quantity=20)",
                "ShopProduct(shop_id=S1_I00002, product_id=S1_P00002, "
                "name=星云套件, core: 雾核, price=118, quantity=20)",
            ]
        ),
    )
    card = DecisionCard(prefer=["晶核"])
    assert ledger.shortlist(card, limit=1)[0].candidate_id == "S1_P00001"


def test_schema_field_exclusion_is_hard_and_preferences_cannot_bypass_it():
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "\n".join(
            [
                "StoreProduct(store_id=S1_S00001, product_name=星云片, "
                "product_id=S1_P00001, payload: 无, quantity=20)",
                "StoreProduct(store_id=S1_S00002, product_name=星云片, "
                "product_id=S1_P00002, payload: 红砂, quantity=20)",
            ]
        ),
    )
    card = DecisionCard(
        avoid=["红砂"],
        prefer=["红砂"],
        constraints=[
            Constraint(
                "payload",
                "红砂",
                operator=ConstraintOperator.EXCLUDES,
                source="memory",
            )
        ],
    )
    ranked = [candidate.candidate_id for candidate in ledger.shortlist(card)]
    assert ranked == ["S1_P00001"]
    assert not ledger.validate_write(
        "create_delivery_order",
        {
            "store_id": "S1_S00001",
            "product_ids": ["S1_P00001"],
            "product_cnts": [1],
        },
        card,
    )


def test_unrelated_preferences_cannot_bypass_topping_exclusion():
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "StoreProduct(store_id=S1_S00001, product_name=珍珠奶茶, "
        "product_id=S1_P00001, attributes=大杯, 热饮, 加珍珠小料, "
        "quantity=20, tags=['奶茶', '珍珠', '热饮'])",
    )
    card = DecisionCard(
        avoid=["小料"],
        prefer=["珍珠", "热饮", "蜜雪冰城", "书亦烧仙草（人民路店）"],
        constraints=[
            Constraint(
                "avoid",
                "小料",
                operator=ConstraintOperator.EXCLUDES,
                source="memory",
            )
        ],
    )
    assert not ledger.shortlist(card)
    errors = ledger.validate_write(
        "create_delivery_order",
        {"store_id": "S1_S00001", "product_ids": ["S1_P00001"]},
        card,
    )
    assert any("小料" in error for error in errors)


def test_execution_ready_requires_a_compliant_shortlist():
    class Params:
        @classmethod
        def model_json_schema(cls):
            return {
                "required": ["store_id", "product_ids", "user_id"],
                "properties": {
                    "store_id": {},
                    "product_ids": {},
                    "user_id": {},
                },
            }

    class Tool:
        name = "create_delivery_order"
        params = Params

    registry = ToolRegistry()
    registry.rebuild([Tool()])
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "StoreProduct(store_id=S1_S00001, product_name=珍珠奶茶, "
        "product_id=S1_P00001, attributes=热饮, 加珍珠小料, quantity=2)",
    )
    card = DecisionCard(
        avoid=["小料"],
        constraints=[
            Constraint("avoid", "小料", operator=ConstraintOperator.EXCLUDES)
        ],
    )
    assert not registry.execution_ready(ledger, card)


def test_open_world_preferences_rank_candidate_with_most_observed_fields():
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "\n".join(
            [
                "StoreProduct(store_id=S1_S00001, product_name=雾海模组, "
                "product_id=S1_P00001, tone: 灰白, latency: 25, quantity=10)",
                "StoreProduct(store_id=S1_S00002, product_name=雾海模组, "
                "product_id=S1_P00002, tone: 荧绿, latency: 35, quantity=10)",
            ]
        ),
    )
    card = DecisionCard(
        prefer=["灰白", "latency: 25"],
    )
    assert ledger.shortlist(card, limit=1)[0].candidate_id == "S1_P00001"


def test_recommendation_correction_reopens_done_runtime():
    runtime = TaskRuntime.begin(TaskSpec.compile("想入一双新鞋，给我推荐"))
    runtime.phase = RuntimePhase.DONE
    runtime.observe_user("你推荐的是袜子，我要的是鞋，这怎么行？")
    assert runtime.phase == RuntimePhase.SEARCH
    assert runtime.revision_requested


def test_hierarchical_child_is_selected_without_parent_category_rule():
    ledger = CandidateLedger()
    ledger.observe(
        "get_ota_hotel_info",
        "\n".join(
            [
                "Hotel(hotel_id=S1_H00001, hotel_name=汉庭酒店)",
                "HotelProduct(room_type=简约大床房, date=2026-02-19, "
                "quantity=3, room_id=S1_P00001)",
            ]
        ),
    )
    card = DecisionCard(task_intent=["请选简约大床房"])
    assert [candidate.candidate_id for candidate in ledger.shortlist(card)] == [
        "S1_P00001"
    ]


def test_task_spec_captures_address_and_route_constraints():
    delivery = TaskSpec.compile("就糯香青山吧，少糖多冰，送到公司前台")
    address = next(constraint for constraint in delivery.must if constraint.kind == "address")
    assert address.value == "company"
    assert address.target.value == "argument"
    route = TaskSpec.compile("帮我订从北京到上海的高铁票")
    must = [constraint.value for constraint in route.must]
    assert "北京" in must
    assert "上海" in must


def test_instore_reservation_is_validated_as_write():
    ledger = CandidateLedger()
    errors = ledger.validate_write(
        "instore_reservation",
        {"user_id": "U1", "shop_id": "S1_I99999", "time": "2026-08-23 18:00:00"},
        DecisionCard(),
    )
    assert errors


def test_restaurant_facts_remain_in_current_task_scope():
    memory = ADAPTMemory(language="chinese")
    memory.begin_subtask("预订一家餐厅")
    memory.update([
        {
            "type": "conversation",
            "timestamp": "2026-08-22 10:00:00",
            "content": "我喜欢清淡的餐厅",
        }
    ])
    assert all(fact.scope == "instore" for fact in memory.facts)


def test_third_identical_search_is_counted_for_rejection():
    ledger = CandidateLedger()
    args = {"keywords": ["奶茶"]}
    assert ledger.register_search("delivery_product_search_recommand", args) == 1
    assert ledger.register_search("delivery_product_search_recommand", args) == 2
    assert ledger.register_search("delivery_product_search_recommand", args) == 3


def test_keyword_changes_do_not_reset_semantic_search_family_budget():
    ledger = CandidateLedger()
    tool = "instore_product_search_recommend"
    assert ledger.register_search(tool, {"keywords": ["室内", "陶艺"]}) == 1
    assert ledger.register_search(tool, {"keywords": ["室内", "攀岩"]}) == 1
    assert ledger.register_search(tool, {"keywords": ["室内", "音乐现场"]}) == 1
    assert ledger.family_search_count(tool) == 3


def test_agent_blocks_third_search_family_even_when_keywords_change():
    class Tool:
        name = "instore_product_search_recommend"

    class Debug:
        def emit(self, *args, **kwargs):
            pass

    agent = object.__new__(ADAPTAgent)
    agent.task_spec = TaskSpec.compile("帮我预约一个室内场馆")
    agent.runtime = TaskRuntime.begin(agent.task_spec)
    agent.runtime.phase = RuntimePhase.SEARCH
    agent.tool_registry = ToolRegistry()
    agent.tool_registry.meta = {
        Tool.name: ToolMeta(Tool.name, ToolRole.SEARCH, set(), {})
    }
    agent.tool_errors = ToolErrorLedger()
    agent.ledger = CandidateLedger()
    agent.decision_card = DecisionCard()
    agent.user_profile = {}
    agent.question_gate = QuestionGate()
    agent.enable_candidate_validation = False
    agent.debug = Debug()
    agent._record_lesson = lambda *args: None

    for index, keywords in enumerate((("室内", "陶艺"), ("室内", "攀岩"))):
        call = ToolCall(
            id=f"search-{index}",
            name=Tool.name,
            arguments={"keywords": list(keywords)},
        )
        assistant = AssistantMessage(role="assistant", tool_calls=[call])
        assert not agent._preflight(assistant, [Tool()])
        agent._observe_assistant(assistant)

    third = ToolCall(
        id="search-2",
        name=Tool.name,
        arguments={"keywords": ["室内", "音乐现场"]},
    )
    problems = agent._preflight(
        AssistantMessage(role="assistant", tool_calls=[third]), [Tool()]
    )
    assert any("semantic search family budget exhausted" in item for item in problems)


def test_third_identical_failed_write_is_rejected():
    failures = ToolErrorLedger(max_identical_failures=2)
    arguments = {
        "user_id": "U1",
        "product_ids": ["S1_P00001"],
        "address": "四川省绵阳市科学城7区1栋1单元",
    }
    for index in range(2):
        call_id = f"create-{index}"
        failures.register_proposal(
            call_id, "create_delivery_order", arguments, ToolRole.CREATE
        )
        failures.observe_result(
            call_id,
            "create_delivery_order",
            "Longitude and latitude not found for address",
            True,
        )
    assert "already failed 2 times" in failures.rejection_reason(
        "create_delivery_order", arguments, ToolRole.CREATE
    )


def test_address_recovery_requires_current_subtask_success_evidence():
    failures = ToolErrorLedger()
    full_address = "四川省绵阳市科学城7区1栋1单元"
    resolved_prefix = "四川省绵阳市科学城7区"
    failures.register_proposal(
        "resolve-full",
        "address_to_longitude_latitude",
        {"address": full_address},
        ToolRole.READ,
    )
    failures.observe_result(
        "resolve-full",
        "address_to_longitude_latitude",
        "Longitude and latitude not found for address",
        True,
    )
    probe = failures.next_address_probe()
    assert probe is not None
    assert probe.probe_value == resolved_prefix
    failures.register_proposal(
        "framework-probe",
        probe.tool_name,
        {probe.argument: probe.probe_value},
        ToolRole.READ,
    )
    assert failures.next_address_probe() is None
    arguments = {"address": full_address}
    assert failures.recover(
        "create_delivery_order", arguments, ToolRole.CREATE
    ) is None

    failures.observe_result(
        "framework-probe",
        "address_to_longitude_latitude",
        "Longitude: 104.7, Latitude: 31.5",
        False,
    )
    recovery = failures.recover(
        "create_delivery_order", arguments, ToolRole.CREATE
    )
    assert recovery is not None
    assert arguments["address"] == resolved_prefix

    failures.reset()
    reset_arguments = {"address": full_address}
    assert failures.recover(
        "create_delivery_order", reset_arguments, ToolRole.CREATE
    ) is None


def test_framework_address_probe_is_read_only_and_single_use():
    class Debug:
        def __init__(self):
            self.events = []

        def emit(self, event, **payload):
            self.events.append({"event": event, **payload})

    agent = object.__new__(ADAPTAgent)
    agent.runtime = TaskRuntime.begin(TaskSpec.compile("帮我买个东西送到家里"))
    agent.tool_registry = ToolRegistry()
    agent.tool_registry.meta = {
        "address_to_longitude_latitude": ToolMeta(
            "address_to_longitude_latitude", ToolRole.READ, {"address"}, {}
        )
    }
    agent.tool_errors = ToolErrorLedger()
    agent.debug = Debug()
    full_address = "四川省绵阳市科学城7区1栋1单元"
    agent.tool_errors.register_proposal(
        "create-failed",
        "create_delivery_order",
        {"address": full_address},
        ToolRole.CREATE,
    )
    agent.tool_errors.observe_result(
        "create-failed",
        "create_delivery_order",
        "Longitude and latitude not found for address",
        True,
    )

    message = agent._framework_parameter_probe()
    assert message is not None
    call = message.tool_calls[0]
    assert call.name == "address_to_longitude_latitude"
    assert call.arguments == {"address": "四川省绵阳市科学城7区"}
    agent._observe_assistant(message)
    assert agent._framework_parameter_probe() is None


def test_agent_preflight_applies_proven_address_recovery_before_write():
    class Tool:
        name = "create_delivery_order"

    class Debug:
        def __init__(self):
            self.events = []

        def emit(self, event, **payload):
            self.events.append({"event": event, **payload})

    full_address = "四川省绵阳市科学城7区1栋1单元"
    resolved_prefix = "四川省绵阳市科学城7区"
    agent = object.__new__(ADAPTAgent)
    agent.runtime = TaskRuntime.begin(TaskSpec.compile("帮我买个手办送到家里"))
    agent.runtime.phase = RuntimePhase.READY_TO_CREATE
    agent.tool_registry = ToolRegistry()
    agent.tool_registry.meta = {
        "create_delivery_order": ToolMeta(
            "create_delivery_order", ToolRole.CREATE, {"address"}, {}
        )
    }
    agent.tool_errors = ToolErrorLedger()
    agent.ledger = CandidateLedger()
    agent.decision_card = DecisionCard(
        constraints=[
            Constraint(
                "address",
                "home",
                operator=ConstraintOperator.RESOLVES_PROFILE,
                target=ConstraintTarget.ARGUMENT,
            )
        ]
    )
    agent.user_profile = {"home": full_address}
    agent.question_gate = QuestionGate()
    agent.enable_candidate_validation = True
    agent.debug = Debug()
    agent._record_lesson = lambda *args: None

    agent.tool_errors.register_proposal(
        "resolve-full",
        "address_to_longitude_latitude",
        {"address": full_address},
        ToolRole.READ,
    )
    agent.tool_errors.observe_result(
        "resolve-full",
        "address_to_longitude_latitude",
        "Longitude and latitude not found for address",
        True,
    )
    agent.tool_errors.register_proposal(
        "resolve-prefix",
        "address_to_longitude_latitude",
        {"address": resolved_prefix},
        ToolRole.READ,
    )
    agent.tool_errors.observe_result(
        "resolve-prefix",
        "address_to_longitude_latitude",
        "Longitude: 104.7, Latitude: 31.5",
        False,
    )
    call = ToolCall(
        id="create-recovered",
        name="create_delivery_order",
        arguments={"address": full_address},
    )
    problems = agent._preflight(
        AssistantMessage(role="assistant", tool_calls=[call]), [Tool()]
    )
    assert not problems
    assert call.arguments["address"] == resolved_prefix
    assert any(
        event["event"] == "tool_parameter_recovered"
        for event in agent.debug.events
    )


def test_framework_recovered_write_freezes_unaffected_create_arguments():
    class Tool:
        name = "create_delivery_order"

    class Debug:
        def __init__(self):
            self.events = []

        def emit(self, event, **payload):
            self.events.append({"event": event, **payload})

    full_address = "四川省绵阳市科学城7区1栋1单元"
    resolved_prefix = "四川省绵阳市科学城"
    original = {
        "user_id": "U1",
        "store_id": "S1_S00001",
        "product_ids": ["S1_P00001"],
        "product_cnts": [1],
        "address": full_address,
        "dispatch_time": "2026-08-23 15:00:00",
        "note": "保持原备注",
    }
    agent = object.__new__(ADAPTAgent)
    agent.runtime = TaskRuntime.begin(TaskSpec.compile("帮我买个手办送到家里"))
    agent.runtime.phase = RuntimePhase.WAIT_CREATE_RESULT
    agent.tool_registry = ToolRegistry()
    agent.tool_registry.tools = [Tool()]
    agent.tool_registry.meta = {
        "create_delivery_order": ToolMeta(
            "create_delivery_order", ToolRole.CREATE, set(), {}
        )
    }
    agent.tool_errors = ToolErrorLedger()
    agent.ledger = CandidateLedger()
    agent.decision_card = DecisionCard()
    agent.user_profile = {}
    agent.question_gate = QuestionGate()
    agent.enable_candidate_validation = False
    agent.debug = Debug()
    agent._record_lesson = lambda *args: None

    agent.tool_errors.register_proposal(
        "failed-create", "create_delivery_order", original, ToolRole.CREATE
    )
    agent.tool_errors.observe_result(
        "failed-create",
        "create_delivery_order",
        "Longitude and latitude not found for address",
        True,
    )
    agent.tool_errors.register_proposal(
        "resolved-prefix",
        "address_to_longitude_latitude",
        {"address": resolved_prefix},
        ToolRole.READ,
    )
    agent.tool_errors.observe_result(
        "resolved-prefix",
        "address_to_longitude_latitude",
        "Longitude: 104.7, Latitude: 31.5",
        False,
    )

    message = agent._framework_recovered_write()
    assert message is not None
    replayed = message.tool_calls[0].arguments
    assert replayed["address"] == resolved_prefix
    assert {
        key: value for key, value in replayed.items() if key != "address"
    } == {
        key: value for key, value in original.items() if key != "address"
    }
    assert any(
        event["event"] == "recovered_write_replayed"
        for event in agent.debug.events
    )

    agent._operation_journal().register(
        "successful-create", "create", "create_delivery_order", replayed
    )
    agent._operation_journal().observe_result(
        "successful-create", "create_delivery_order", "create", False
    )
    assert agent._framework_recovered_write() is None


def test_agent_blocks_third_write_after_observing_two_real_failures():
    class Tool:
        name = "create_delivery_order"

    class Debug:
        def emit(self, *args, **kwargs):
            pass

    agent = object.__new__(ADAPTAgent)
    agent.runtime = TaskRuntime.begin(TaskSpec.compile("帮我买个手办送到家里"))
    agent.runtime.phase = RuntimePhase.READY_TO_CREATE
    agent.tool_registry = ToolRegistry()
    agent.tool_registry.meta = {
        "create_delivery_order": ToolMeta(
            "create_delivery_order", ToolRole.CREATE, set(), {}
        )
    }
    agent.tool_errors = ToolErrorLedger()
    agent.ledger = CandidateLedger()
    agent.decision_card = DecisionCard()
    agent.user_profile = {}
    agent.question_gate = QuestionGate()
    agent.enable_candidate_validation = False
    agent.debug = Debug()
    agent._record_lesson = lambda *args: None
    arguments = {
        "address": "四川省绵阳市科学城7区1栋1单元",
        "product_ids": ["S1_P00001"],
    }

    for index in range(2):
        call = ToolCall(
            id=f"failed-create-{index}",
            name="create_delivery_order",
            arguments=dict(arguments),
        )
        assistant = AssistantMessage(role="assistant", tool_calls=[call])
        assert not agent._preflight(assistant, [Tool()])
        agent._observe_assistant(assistant)
        agent._observe_input(
            ToolMessage(
                id=call.id,
                name=call.name,
                role="tool",
                content="Longitude and latitude not found for address",
                error=True,
            )
        )

    third = ToolCall(
        id="failed-create-2",
        name="create_delivery_order",
        arguments=dict(arguments),
    )
    problems = agent._preflight(
        AssistantMessage(role="assistant", tool_calls=[third]), [Tool()]
    )
    assert any("already failed 2 times" in problem for problem in problems)


def test_unpaid_order_is_tracked_until_payment():
    ledger = CandidateLedger()
    ledger.observe("create_delivery_order", "Order(order_id:OT123abc, status:unpaid)")
    assert "OT123abc" in ledger.pending_payment_ids
    ledger.observe("pay_delivery_order", "Payment successful")
    assert not ledger.pending_payment_ids


def test_lessons_are_user_local_and_facet_scoped():
    first = ExecutionLessonStore("user-a")
    second = ExecutionLessonStore("user-b")
    first.add("delivery", "beverage", "tool_error", "bad id", "copy the observed ID")
    assert first.relevant("delivery", "beverage")
    assert not first.relevant("ota", "hotel")
    assert not second.all()


def test_task_spec_does_not_define_hidden_evaluation_fields():
    spec = TaskSpec.compile("就糯糯青山吧，少糖多冰，送到公司")
    assert "糯糯青山" in [constraint.value for constraint in spec.must]
    assert "少糖" not in [constraint.value for constraint in spec.must]
    ledger = CandidateLedger()
    ledger.observe(
        "search",
        "StoreProduct(store_id=S1_S00001, product_id=S1_P00001, "
        "product_name=糯糯青山, sweetness: 少糖, ice: 多冰, quantity=3)",
    )
    card = build_decision_card(spec, [])
    grounded = ledger.ground_task_constraints(card)
    # Legacy, unannotated result fields remain ranking evidence. They must not
    # silently become evaluator-like hidden requirements at runtime.
    assert grounded == []
    assert not hasattr(spec, "rubric")
    assert not hasattr(spec, "reward")
    assert not hasattr(spec, "target_product_ids")


def test_question_gate_blocks_reasking_after_delegation():
    runtime = TaskRuntime.begin(TaskSpec.compile("帮我买一双拖鞋送到家"))
    runtime.observe_user("尺码我不太清楚，你看着办")
    runtime.observe_candidates(2)
    decision = QuestionGate().evaluate("您想选第一双还是第二双？", runtime)
    assert not decision.allowed
    assert runtime.authorization.choice_delegated


def test_candidate_choice_question_counts_once():
    runtime = TaskRuntime.begin(TaskSpec.compile("推荐几款鼠标"))
    runtime.observe_candidates(2)
    gate = QuestionGate()
    decision = gate.evaluate("您想选第一款还是第二款？", runtime)
    assert decision.allowed
    gate.commit(decision, runtime)
    assert "candidate_choice" in runtime.asked_dimensions
    assert not gate.evaluate("还需要我帮您选择吗？", runtime).allowed


def test_direct_commit_delegates_candidate_choice_and_avoids_reconfirmation():
    runtime = TaskRuntime.begin(TaskSpec.compile("帮我点杯喝的送到公司"))
    assert runtime.authorization.candidate_choice_authorized
    runtime.observe_candidates(5)
    assert runtime.phase == RuntimePhase.READY_TO_CREATE
    assert not QuestionGate().evaluate("确认下单吗？", runtime).allowed


def test_natural_order_phrasing_compiles_as_commit():
    for instruction in (
        "26号开会，提前给我点个咖啡提神",
        "下午来杯冰咖啡",
        "帮我订一间明晚的大床房",
        "帮我再点一杯奶茶送到家",
    ):
        spec = TaskSpec.compile(instruction)
        assert spec.action == "commit"


def test_goal_contract_generalizes_transaction_paraphrases_without_item_rules():
    for instruction in (
        "请替我选一双合适的鞋买好送到家",
        "帮我们订下周末的酒店",
        "帮我定张去贵阳的车票",
        "这家酒店你给我订一下吧",
        "给我买件衣服送到家里",
        "帮我团张周末的券",
    ):
        spec = TaskSpec.compile(instruction)
        assert spec.action == "commit"
        assert spec.completion.requires_write


def test_goal_contract_does_not_upgrade_information_or_negated_requests():
    for instruction in (
        "帮我看看有没有合适的团购券",
        "推荐几家酒店给我比较一下",
        "这些鞋怎么选",
        "先别帮我买，推荐几款就好",
    ):
        spec = TaskSpec.compile(instruction)
        assert spec.action == "recommend"
        assert not spec.completion.requires_write


def test_address_parser_rejects_discourse_tail_and_resolves_profile_aliases():
    no_address = TaskSpec.compile("晚上六点前送到就行")
    assert not any(item.kind == "address" for item in no_address.must)

    company = TaskSpec.compile("帮我点一杯果汁送公司来")
    address = next(item for item in company.must if item.kind == "address")
    assert address.value == "company"

    home = TaskSpec.compile("给我买件衣服送到家")
    address = next(item for item in home.must if item.kind == "address")
    assert address.value == "home"


def test_preflight_binds_profile_alias_over_model_guessed_address():
    class Tool:
        name = "create_delivery_order"

    class Debug:
        def emit(self, *args, **kwargs):
            pass

    home = "北京市海淀区中关村大街1号"
    agent = object.__new__(ADAPTAgent)
    agent.task_spec = TaskSpec.compile("帮我买个新物品送到家")
    agent.runtime = TaskRuntime.begin(agent.task_spec)
    agent.runtime.phase = RuntimePhase.READY_TO_CREATE
    agent.tool_registry = ToolRegistry()
    agent.tool_registry.meta = {
        "create_delivery_order": ToolMeta(
            "create_delivery_order", ToolRole.CREATE, {"address"}, {}
        )
    }
    agent.tool_errors = ToolErrorLedger()
    agent.ledger = CandidateLedger()
    agent.decision_card = DecisionCard(
        constraints=[
            Constraint(
                "address",
                "home",
                ConstraintTarget.ARGUMENT,
                ConstraintOperator.RESOLVES_PROFILE,
                argument_name="address",
            )
        ]
    )
    agent.user_profile = {"home": home}
    agent.question_gate = QuestionGate()
    agent.enable_candidate_validation = True
    agent.debug = Debug()
    agent._record_lesson = lambda *args: None
    call = ToolCall(
        id="bind-home",
        name="create_delivery_order",
        arguments={"address": "模型猜测的地址"},
    )
    problems = agent._preflight(
        AssistantMessage(role="assistant", tool_calls=[call]), [Tool()]
    )
    assert not any("address" in problem for problem in problems)
    assert call.arguments["address"] == home


def test_profile_address_prefers_street_address_over_resident_city():
    from agent.decision import resolve_profile_address

    profile = {
        "常住地": "河南省郑州市",
        "常住住址": "郑州市金水区国基路某小区2栋302",
        "工作地址": "郑州市某医院护士站",
    }
    assert resolve_profile_address(profile, "home") == profile["常住住址"]
    assert resolve_profile_address(profile, "company") == profile["工作地址"]


def test_completed_recommendation_reopens_when_user_authorizes_selected_candidate():
    runtime = TaskRuntime.begin(TaskSpec.compile("推荐两家酒店"))
    runtime.observe_candidates(2, execution_ready=True)
    runtime.phase = RuntimePhase.DONE
    runtime.observe_user("可以，就订第一个吧")
    assert runtime.authorization.create_authorized
    assert runtime.selection_made
    assert runtime.phase == RuntimePhase.READY_TO_CREATE


def test_completed_recommendation_does_not_reopen_for_selection_without_transaction():
    runtime = TaskRuntime.begin(TaskSpec.compile("推荐两家酒店"))
    runtime.observe_candidates(2, execution_ready=True)
    runtime.phase = RuntimePhase.DONE
    runtime.observe_user("我觉得第一个不错")
    assert runtime.selection_made
    assert not runtime.authorization.create_authorized
    assert runtime.phase == RuntimePhase.DONE


def test_operation_journal_allows_failed_retry_but_blocks_duplicate_success():
    journal = OperationJournal()
    arguments = {"shop_id": "S1_I00001", "product_id": "S1_P00001"}
    journal.register("failed", "create", "create_order", arguments)
    journal.observe_result("failed", "create_order", "create", True)
    assert not journal.validate("create")

    journal.register("success", "create", "create_order", arguments)
    journal.observe_result("success", "create_order", "create", False)
    assert any("already succeeded" in error for error in journal.validate("create"))

    journal.begin_new_epoch()
    assert not journal.validate("create")


def test_task_spec_owns_runtime_question_contract():
    class Memory:
        def __init__(self):
            self.committed = []

        def propose_gap(self, instruction, domain):
            return InformationGap(
                "room_type", "这次需要大床房还是双床房？", "memory"
            )

        def commit_question(self, question):
            self.committed.append(question)
            return True

    class Debug:
        def emit(self, *args, **kwargs):
            pass

    agent = object.__new__(ADAPTAgent)
    agent.task_spec = TaskSpec.compile("帮我订明天晚上重庆的酒店")
    agent.runtime = TaskRuntime.begin(agent.task_spec)
    agent.decision_card = DecisionCard()
    agent.ledger = CandidateLedger()
    agent.memory = Memory()
    agent.debug = Debug()

    question = agent._framework_question()

    assert question == "请告诉我需要大床房还是双床房。"
    assert agent.runtime.pending_question_dimension == "room_type"
    assert agent.memory.committed == [question]


def test_tool_schema_declares_unseen_question_dimension():
    class Params:
        @classmethod
        def model_json_schema(cls):
            return {
                "required": ["chosen_sigil", "resonance"],
                "properties": {
                    "chosen_sigil": {"x-adapt-entity": "relic"},
                    "resonance": {
                        "type": "string",
                        "x-adapt-question": True,
                        "x-adapt-question-text": "请告诉我要晨鸣还是夜鸣。",
                    },
                },
            }

    class Tool:
        name = "seal_the_choice"
        info = {"adapt_role": "create"}
        params = Params

    agent = object.__new__(ADAPTAgent)
    agent.task_spec = TaskSpec.compile("请帮我下单一枚遗物")
    agent.runtime = TaskRuntime.begin(agent.task_spec)
    agent.tool_registry = ToolRegistry()
    agent.tool_registry.rebuild([Tool()])
    contract = agent._information_gap_contract()
    assert any(gap.dimension == "resonance" for gap in contract.gaps)
    assert any("晨鸣还是夜鸣" in gap.question for gap in contract.gaps)


def test_current_clarification_becomes_hard_candidate_constraint():
    agent = object.__new__(ADAPTAgent)
    agent.task_spec = TaskSpec.compile("26号开会，给我点个咖啡提神")
    agent.decision_card = DecisionCard(prefer=["生椰拿铁（标准咖啡因）"])
    agent._promote_current_answer("caffeine", "低咖啡因", "下午喝")
    assert agent.decision_card.must[0] == "低咖啡因"
    assert agent.decision_card.constraints[0].hard
    assert agent.decision_card.constraints[0].source == "current_user_answer"


def test_future_default_dialogue_becomes_structured_preference():
    signals = SignalParser().parse(
        [
            {
                "date": "2026-01-01",
                "dialogue": [
                    {"role": "user", "content": "以后吃火锅都得加一份冰汤圆"}
                ],
            }
        ]
    )
    assert any(
        signal.predicate == "explicit_preference" and signal.object == "冰汤圆"
        for signal in signals
    )


def test_soft_preference_conflict_does_not_invent_required_dimension():
    agent = object.__new__(ADAPTAgent)
    agent.task_spec = TaskSpec.compile("今晚聚餐想吃个汤锅，帮我下单个套餐")
    agent.decision_card = DecisionCard(prefer=["麻辣火锅", "菌汤锅底", "茼蒿"])
    agent.runtime = TaskRuntime.begin(agent.task_spec)
    agent.ledger = CandidateLedger()
    assert not agent._information_gap_contract().gaps


def test_candidate_attribute_diversity_is_ranked_without_synthetic_question():
    agent = object.__new__(ADAPTAgent)
    agent.task_spec = TaskSpec.compile("帮我下单个火锅套餐")
    agent.decision_card = DecisionCard(prefer=["麻辣火锅"])
    agent.runtime = TaskRuntime.begin(agent.task_spec)
    agent.ledger = CandidateLedger()
    agent.ledger.observe(
        "instore_product_search_recommend",
        "ShopProduct(shop_id=S1_I00001, product_id=S1_P00001, name=麻辣套餐（冰汤圆）)\n"
        "ShopProduct(shop_id=S1_I00002, product_id=S1_P00002, name=麻辣套餐（冰粉）)",
    )
    assert not agent._information_gap_contract().gaps
    agent.decision_card.prefer.append("冰汤圆")
    assert agent.ledger.shortlist(agent.decision_card)[0].name.endswith("冰汤圆）")


def test_conditional_restaurant_preferences_resolve_for_current_scenario():
    signals = SignalParser().parse(
        [
            {
                "date": "2026-01-01",
                "dialogue": [
                    {
                        "role": "user",
                        "content": "四个人以上还是麻辣火锅更合适，人少就吃菌汤锅",
                    }
                ],
            }
        ]
    )
    facts = [fact_from_signal(signal) for signal in signals]
    group_card = build_decision_card(
        TaskSpec.compile("今晚聚餐想吃个汤锅，帮我下单个套餐"), facts
    )
    assert "麻辣火锅" in group_card.prefer
    assert not any("菌汤" in value for value in group_card.prefer)

    small_card = build_decision_card(
        TaskSpec.compile("我一个人想吃个汤锅套餐"), facts
    )
    assert "菌汤锅" in small_card.prefer
    assert not any("麻辣" in value for value in small_card.prefer)


def test_memory_update_refreshes_the_live_decision_card_snapshot():
    class CompilingMemory:
        def compile_task(self, instruction):
            assert "聚餐" in instruction
            return DecisionCard(prefer=["麻辣火锅", "冰汤圆"])

    class Debug:
        def emit(self, *args, **kwargs):
            pass

    agent = object.__new__(ADAPTAgent)
    agent._current_instruction = "今晚聚餐想吃个汤锅"
    agent.task_spec = TaskSpec.compile(agent._current_instruction)
    agent.decision_card = DecisionCard(prefer=["菌汤锅底"])
    agent.memory = CompilingMemory()
    agent.debug = Debug()
    agent._refresh_decision_after_memory_update()
    assert agent.decision_card.prefer == ["麻辣火锅", "冰汤圆"]


def test_ready_to_create_generation_context_drops_search_anchor():
    class Tool:
        name = "create_delivery_order"

    agent = object.__new__(ADAPTAgent)
    agent.runtime = TaskRuntime.begin(TaskSpec.compile("帮我点杯奶茶送到家"))
    agent.runtime.phase = RuntimePhase.READY_TO_CREATE
    state = LLMAgentState(
        system_messages=[SystemMessage(role="system", content="Candidate Ledger: P1")],
        messages=[
            UserMessage(role="user", content="帮我点杯奶茶送到家"),
            UserMessage(role="user", content="old search correction"),
        ],
    )
    focused = agent._generation_messages(state, [Tool()], 1)
    rendered = "\n".join(message.content or "" for message in focused)
    assert "create_delivery_order" in rendered
    assert "Call exactly one exposed CREATE tool" in rendered
    assert "old search correction" in rendered
    assert len(focused) == 2


def test_ready_to_create_context_does_not_lock_soft_preference_leader():
    class Tool:
        name = "create_instore_order"

    agent = object.__new__(ADAPTAgent)
    agent.runtime = TaskRuntime.begin(TaskSpec.compile("帮我下单一个火锅套餐"))
    agent.runtime.phase = RuntimePhase.READY_TO_CREATE
    agent.decision_card = DecisionCard(prefer=["麻辣火锅", "茼蒿", "冰汤圆"])
    agent.ledger = CandidateLedger()
    agent.ledger.observe(
        "instore_product_search_recommend",
        "\n".join(
            [
                "ShopProduct(shop_id=S1_I00001, product_id=S1_P00081, "
                "name=酸菜鱼汤锅套餐（含茼蒿/冰汤圆）, price=118, quantity=20)",
                "ShopProduct(shop_id=S1_I00002, product_id=S1_P10101, "
                "name=麻辣火锅聚餐4人套餐（含茼蒿/毛肚/鸭血/肥牛/冰汤圆）, "
                "price=168, quantity=20)",
            ]
        ),
    )
    state = LLMAgentState(
        system_messages=[SystemMessage(role="system", content="Candidate Ledger")],
        messages=[UserMessage(role="user", content="帮我下单一个火锅套餐")],
    )
    rendered = "\n".join(
        message.content or ""
        for message in agent._generation_messages(state, [Tool()], 0)
    )
    assert "unique preference-evidence leader" not in rendered
    assert "Framework-selected candidate" not in rendered
    assert "best compliant Candidate Ledger IDs" in rendered


def test_direct_commit_cannot_ask_candidate_choice_before_hierarchy_is_ready():
    runtime = TaskRuntime.begin(TaskSpec.compile("帮我订明晚重庆的酒店"))
    runtime.observe_candidates(5, execution_ready=False)
    decision = QuestionGate().evaluate("订第一家商务大床房可以吗？", runtime)
    assert runtime.phase == RuntimePhase.SELECT
    assert not decision.allowed
    assert "expand required details" in decision.reason


def test_enrichment_read_signature_is_single_use():
    ledger = CandidateLedger()
    arguments = {"hotel_id": "S1_H00001"}
    assert ledger.register_enrichment_read("get_ota_hotel_info", arguments) == 1
    assert ledger.register_enrichment_read("get_ota_hotel_info", arguments) == 2


def test_ready_to_create_exposes_only_create_tools():
    class Params:
        @classmethod
        def model_json_schema(cls):
            return {"properties": {}}

    class Tool:
        def __init__(self, name):
            self.name = name
            self.params = Params

    registry = ToolRegistry()
    registry.rebuild(
        [
            Tool("delivery_product_search_recommand"),
            Tool("get_delivery_product_info"),
            Tool("create_delivery_order"),
            Tool("pay_delivery_order"),
        ]
    )
    runtime = TaskRuntime.begin(TaskSpec.compile("帮我点杯喝的送到公司"))
    runtime.observe_candidates(5)
    names = [tool.name for tool in registry.allowed_tools(runtime, CandidateLedger())]
    assert runtime.phase == RuntimePhase.READY_TO_CREATE
    assert names == ["create_delivery_order"]


def test_hotel_parent_candidate_requires_room_before_create_phase():
    class SearchParams:
        @classmethod
        def model_json_schema(cls):
            return {"properties": {}}

    class CreateParams:
        @classmethod
        def model_json_schema(cls):
            return {
                "required": ["hotel_id", "room_id", "user_id"],
                "properties": {
                    "hotel_id": {},
                    "room_id": {},
                    "user_id": {},
                },
            }

    class Tool:
        def __init__(self, name, params=SearchParams):
            self.name = name
            self.params = params

    registry = ToolRegistry()
    registry.rebuild(
        [
            Tool("hotel_search_recommand"),
            Tool("get_ota_hotel_info"),
            Tool("create_hotel_order", CreateParams),
        ]
    )
    ledger = CandidateLedger()
    ledger.observe(
        "hotel_search_recommand",
        "Hotel(hotel_id=S1_H00001, hotel_name=汉庭酒店)",
    )
    runtime = TaskRuntime.begin(TaskSpec.compile("帮我订明晚重庆的酒店"))
    runtime.observe_candidates(1, execution_ready=registry.execution_ready(ledger))
    assert runtime.phase == RuntimePhase.SELECT
    assert [tool.name for tool in registry.allowed_tools(runtime, ledger)] == [
        "hotel_search_recommand",
        "get_ota_hotel_info",
    ]
    # With one observed parent, one expansion is sufficient. Larger result
    # sets require bounded multi-parent coverage.
    ledger.observe(
        "get_ota_hotel_info",
        "Hotel(hotel_id=S1_H00001, products=HotelProduct(product_id=S1_P00001, room_type=大床房, date=2026-02-19, quantity=1))",
    )
    runtime.observe_candidates(2, execution_ready=registry.execution_ready(ledger))
    assert runtime.phase == RuntimePhase.READY_TO_CREATE


def test_execution_readiness_does_not_encode_category_coverage_policy():
    class Params:
        @classmethod
        def model_json_schema(cls):
            return {
                "required": ["hotel_id", "room_id", "user_id"],
                "properties": {"hotel_id": {}, "room_id": {}, "user_id": {}},
            }

    class Tool:
        name = "create_hotel_order"
        params = Params

    registry = ToolRegistry()
    registry.rebuild([Tool()])
    ledger = CandidateLedger()
    for index in range(8):
        ledger.observe(
            "hotel_search_recommand",
            f"Hotel(hotel_id=S1_H0000{index}, hotel_name=汉庭{index})",
        )
    ledger.observe(
        "get_ota_hotel_info",
        "Hotel(hotel_id=S1_H00000, products=HotelProduct(room_id=S1_P00000, room_type=大床房, quantity=1))",
    )
    # Required IDs are now ready after one valid parent-child observation.
    # Expanding more parents is a ranking choice, never a hard WRITE gate.
    assert registry.execution_ready(ledger)
    rendered = ledger.render(
        DecisionCard(prefer=["汉庭"]),
        entity_types=registry.candidate_entity_types(ledger),
    )
    assert "Ranked candidate view" in rendered
    assert "S1_P00000" in rendered
    assert "S1_H00001" not in rendered


def test_agent_batches_hierarchical_hotel_enrichment_without_llm():
    class Params:
        @classmethod
        def model_json_schema(cls):
            return {
                "required": ["hotel_id"],
                "properties": {"hotel_id": {}},
            }

    class Tool:
        name = "get_ota_hotel_info"
        params = Params

    registry = ToolRegistry()
    registry.rebuild([Tool()])
    ledger = CandidateLedger()
    for index in range(8):
        ledger.observe(
            "hotel_search_recommand",
            f"Hotel(hotel_id=S1_H0000{index}, hotel_name=汉庭酒店{index}, tags=['近地铁'])",
        )
    spec = TaskSpec.compile("帮我订明晚重庆的酒店")
    runtime = TaskRuntime.begin(spec)
    runtime.observe_candidates(8, execution_ready=False)
    agent = object.__new__(ADAPTAgent)
    agent.runtime = runtime
    agent.task_spec = spec
    agent.decision_card = DecisionCard(prefer=["汉庭", "近地铁"])
    agent.ledger = ledger
    agent.tool_registry = registry
    message = agent._framework_enrichment()
    assert message is not None
    assert len(message.tool_calls) == 6
    assert {call.name for call in message.tool_calls} == {"get_ota_hotel_info"}
    assert len({call.arguments["hotel_id"] for call in message.tool_calls}) == 6


def test_room_id_rejects_hotel_id_even_when_both_are_observed():
    ledger = CandidateLedger()
    ledger.observe(
        "hotel_search_recommand",
        "Hotel(hotel_id=S1_H00001, hotel_name=汉庭酒店)",
    )
    errors = ledger.validate_write(
        "create_hotel_order",
        {"hotel_id": "S1_H00001", "room_id": "S1_H00001", "user_id": "U1"},
        DecisionCard(),
    )
    assert any("room ID" in error for error in errors)


def test_parent_child_write_needs_no_category_constraint():
    ledger = CandidateLedger()
    ledger.observe(
        "hotel_search_recommand",
        "Hotel(hotel_id=S1_H00001, hotel_name=汉庭酒店)",
    )
    ledger.observe(
        "get_ota_hotel_info",
        "HotelProduct(room_type=简约大床房, date=2026-02-19, quantity=1, room_id=S1_P00001)",
    )
    card = DecisionCard()
    assert not ledger.validate_write(
        "create_hotel_order",
        {"hotel_id": "S1_H00001", "room_id": "S1_P00001", "user_id": "U1"},
        card,
    )


def test_schema_parent_validation_does_not_reverse_legacy_store_relation():
    ledger = CandidateLedger()
    ledger.observe(
        "get_store",
        "StoreProduct(store_id=S1_S00001, product_id=S1_P00001, "
        "product_name=星云片, quantity=2)",
    )
    meta = ToolMeta(
        "commit_pair",
        ToolRole.CREATE,
        {"store_ref", "item_ref"},
        {"store_ref": "store", "item_ref": "product"},
    )
    assert not ledger.validate_write(
        "commit_pair",
        {"store_ref": "S1_S00001", "item_ref": "S1_P00001"},
        DecisionCard(),
        tool_meta=meta,
    )


def test_hotel_date_constraint_is_satisfied_by_observed_room_candidate():
    ledger = CandidateLedger()
    ledger.observe(
        "get_ota_hotel_info",
        "Hotel(hotel_id=S1_H00001, hotel_name=汉庭酒店, "
        "products=HotelProduct(room_type=大床房, date=2027-11-13, "
        "quantity=2, room_id=S1_P00001))",
    )
    card = DecisionCard(
        constraints=[
            Constraint(
                "date",
                "11月13日",
                ConstraintTarget.ARGUMENT,
                ConstraintOperator.EQUALS,
                argument_name="date",
            )
        ]
    )
    assert not ledger.validate_write(
        "create_hotel_order",
        {"hotel_id": "S1_H00001", "room_id": "S1_P00001", "user_id": "U1"},
        card,
    )


def test_hotel_date_constraint_rejects_wrong_observed_room_date():
    ledger = CandidateLedger()
    ledger.observe(
        "get_ota_hotel_info",
        "Hotel(hotel_id=S1_H00001, hotel_name=汉庭酒店, "
        "products=HotelProduct(room_type=大床房, date=2027-11-14, "
        "quantity=2, room_id=S1_P00001))",
    )
    card = DecisionCard(
        constraints=[
            Constraint(
                "date",
                "11月13日",
                ConstraintTarget.ARGUMENT,
                ConstraintOperator.EQUALS,
                argument_name="date",
            )
        ]
    )
    errors = ledger.validate_write(
        "create_hotel_order",
        {"hotel_id": "S1_H00001", "room_id": "S1_P00001", "user_id": "U1"},
        card,
    )
    assert any("selected candidate does not satisfy required date" in error for error in errors)


def test_hotel_date_range_binds_checkin_to_room_and_preserves_checkout_as_workflow():
    spec = TaskSpec.compile("帮我订一家30号到3号的酒店")
    checkin = next(item for item in spec.must if item.kind == "date")
    checkout = next(item for item in spec.must if item.kind == "checkout_date")
    assert checkin.value == "30号"
    assert checkout.value == "3号"
    assert checkout.target == ConstraintTarget.WORKFLOW

    ledger = CandidateLedger()
    ledger.observe(
        "get_ota_hotel_info",
        "Hotel(hotel_id=S1_H00001, hotel_name=海景酒店, "
        "products=HotelProduct(room_type=大床房, date=2025-01-30, "
        "quantity=2, room_id=S1_P00001))",
    )
    assert not ledger.validate_write(
        "create_hotel_order",
        {"hotel_id": "S1_H00001", "room_id": "S1_P00001", "user_id": "U1"},
        build_decision_card(spec, []),
    )


def test_compact_hotel_date_range_normalizes_missing_start_suffix():
    spec = TaskSpec.compile("请帮我订23-25号的酒店")
    values = {item.kind: item.value for item in spec.must}
    assert values["date"] == "23号"
    assert values["checkout_date"] == "25号"


def test_explicit_write_date_must_agree_with_selected_ticket_candidate():
    ledger = CandidateLedger()
    ledger.observe(
        "get_ota_flight_info",
        "Flight(flight_id=S1_F00001, products=FlightProduct("
        "seat_type=经济舱, date=2027-11-14, quantity=2, seat_id=S1_P00001))",
    )
    card = DecisionCard(
        constraints=[
            Constraint(
                "date",
                "11月13日",
                ConstraintTarget.ARGUMENT,
                ConstraintOperator.EQUALS,
                argument_name="date",
            )
        ]
    )
    errors = ledger.validate_write(
        "create_flight_order",
        {
            "flight_id": "S1_F00001",
            "seat_id": "S1_P00001",
            "user_id": "U1",
            "date": "2027-11-13",
            "quantity": 1,
        },
        card,
    )
    assert any("selected candidate conflicts with required date" in error for error in errors)


def test_multiline_room_candidates_keep_hotel_parent_and_reject_cross_pairing():
    ledger = CandidateLedger()
    ledger.observe(
        "get_ota_hotel_info",
        "\n".join(
            [
                "Hotel(hotel_id=S1_H00001, hotel_name=汉庭酒店, products=HotelProduct(room_type=商务大床房, date=2026-02-19, quantity=2, room_id=S1_P00001))",
                "HotelProduct(room_type=简约大床房, date=2026-02-19, quantity=3, room_id=S1_P00002)",
            ]
        ),
    )
    assert "S1_H00001" in ledger.candidates["S1_P00002"].parent_ids
    ledger.observe(
        "hotel_search_recommand",
        "Hotel(hotel_id=S1_H00002, hotel_name=另一家酒店)",
    )
    errors = ledger.validate_write(
        "create_hotel_order",
        {"hotel_id": "S1_H00002", "room_id": "S1_P00002", "user_id": "U1"},
        DecisionCard(),
    )
    assert any("was not observed under hotel_id" in error for error in errors)


def test_product_constraints_ignore_other_products_from_same_store():
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "\n".join(
            [
                "StoreProduct(store_id=S1_S00001, product_name=经典原味奶茶, product_id=S1_P00001, attributes=热饮, 无小料, quantity=3)",
                "StoreProduct(store_id=S1_S00001, product_name=芋圆奶茶, product_id=S1_P00002, attributes=热饮, 加芋圆小料, quantity=3)",
            ]
        ),
    )
    card = DecisionCard(avoid=["小料"])
    errors = ledger.validate_write(
        "create_delivery_order",
        {"store_id": "S1_S00001", "product_ids": ["S1_P00001"]},
        card,
    )
    assert not errors


def test_payment_does_not_reapply_create_address_constraints():
    ledger = CandidateLedger()
    ledger.observe(
        "create_delivery_order",
        "Order(order_id:O123456789, status:unpaid)",
    )
    card = DecisionCard(
        constraints=[
            Constraint(
                kind="address",
                value="company",
                target=ConstraintTarget.ARGUMENT,
            )
        ]
    )
    assert not ledger.validate_write(
        "pay_delivery_order",
        {"order_id": "O123456789", "user_id": "U1"},
        card,
        {"company": "测试公司地址"},
    )


def test_task_identity_ranks_unseen_entity_without_category_feature_table():
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "\n".join(
            [
                "StoreProduct(store_id=S1_S00001, product_name=红枣晶核, product_id=S1_P00001, quantity=3, price=10)",
                "StoreProduct(store_id=S1_S00002, product_name=极光棱镜, product_id=S1_P00002, quantity=3, price=10)",
            ]
        ),
    )
    card = DecisionCard(task_intent=["请给我极光棱镜"])
    assert ledger.shortlist(card, limit=1)[0].candidate_id == "S1_P00002"


def test_ungrounded_short_task_does_not_let_memory_choose_another_entity_class():
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "StoreProduct(store_id=S1_S00001, product_name=怡泉纯净水, "
        "product_id=S1_P00001, quantity=5, price=12)\n"
        "StoreProduct(store_id=S1_S00002, product_name=鲜牛奶, "
        "product_id=S1_P00002, quantity=5, price=8)",
    )
    card = DecisionCard(
        task_intent=["家里的水喝完了，帮我买点"],
        preference_pool=["鲜牛奶"],
        preference_weights={"鲜牛奶": 5.0},
    )
    candidates = [
        candidate
        for candidate in ledger.candidates.values()
        if candidate.entity_type == "product"
    ]
    shortlist = ledger.shortlist(card)
    assert [candidate.candidate_id for candidate in shortlist] == ["S1_P00001"]


def test_grounded_unseen_task_allows_memory_to_rank_within_entity_family():
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "StoreProduct(store_id=S1_S00001, product_name=星环机械键盘静音版, "
        "product_id=S1_P00001, quantity=5, price=20)\n"
        "StoreProduct(store_id=S1_S00002, product_name=星环机械键盘标准版, "
        "product_id=S1_P00002, quantity=5, price=20)",
    )
    card = DecisionCard(
        task_intent=["帮我买一个机械键盘"],
        preference_pool=["静音"],
        preference_weights={"静音": 2.0},
    )
    assert ledger.shortlist(card, limit=1)[0].candidate_id == "S1_P00001"


def test_candidate_ledger_policy_scope_uses_tool_family_and_parent_topology():
    ledger = CandidateLedger()
    ledger.register_search("delivery_product_search_recommand", {"keywords": ["星环"]})
    ledger.observe(
        "delivery_product_search_recommand",
        "StoreProduct(store_id=S1_S00001, product_name=星环器, "
        "product_id=S1_P00001, quantity=5)",
    )
    assert ledger.policy_tool_family() == "product_search"
    assert ledger.policy_entity_signature() == "product(store)+store"


def test_multiple_open_world_preferences_remain_distinct_evidence_atoms():
    ledger = CandidateLedger()
    ledger.observe(
        "instore_product_search_recommend",
        "\n".join(
            [
                "ShopProduct(shop_id=S1_I00001, product_id=S1_P00001, name=菌汤火锅4人套餐（含茼蒿/冰汤圆）, quantity=3)",
                "ShopProduct(shop_id=S1_I00002, product_id=S1_P00002, name=麻辣火锅4人套餐（含茼蒿/冰汤圆）, quantity=3)",
            ]
        ),
    )
    spec = TaskSpec.compile("今晚聚餐想吃个汤锅，帮我下单个套餐")
    card = DecisionCard(
        prefer=["麻辣火锅", "菌汤锅底", "茼蒿", "冰汤圆"],
    )
    assert ledger.shortlist(card, limit=1)[0].candidate_id == "S1_P00002"


def test_post_create_user_revision_reopens_search_and_compiles_party_size():
    runtime = TaskRuntime.begin(TaskSpec.compile("今晚聚餐吃汤锅，帮我下单套餐"))
    runtime.phase = RuntimePhase.READY_TO_PAY
    runtime.observe_user("换个大点的，我们有六个人")
    assert runtime.phase == RuntimePhase.SEARCH
    assert runtime.revision_requested
    revised = TaskSpec.compile("今晚聚餐吃汤锅，帮我下单套餐。换个六个人的")
    assert any(c.kind == "party_size" and c.value == "6人" for c in revised.must)


def test_hotel_order_tags_become_scoped_service_attribute_facts():
    signals = SignalParser().parse(
        [
            {
                "type": "order",
                "timestamp": "2026-01-28 00:00:00",
                "content": {
                    "scenario": "hotel",
                    "merchant_name": "汉庭酒店（成都店）",
                    "tags": ["汉庭", "近地铁", "简约风格", "经济型"],
                    "items": [{"product_name": "大床房", "quantity": 1}],
                },
            }
        ]
    )
    attributes = {
        signal.object: fact_from_signal(signal)
        for signal in signals
        if signal.predicate == "attribute_preference"
    }
    assert attributes["近地铁"].facet == "hotel"
    assert attributes["近地铁"].dimension == "location"
    assert attributes["简约风格"].dimension == "attribute"
    assert attributes["经济型"].dimension == "budget"


def test_hotel_room_ranking_uses_parent_location_and_room_style():
    ledger = CandidateLedger()
    ledger.observe(
        "get_ota_hotel_info",
        "Hotel(hotel_id=S1_H00003, hotel_name=汉庭酒店(观音桥店), tags=['经济型'], products=HotelProduct(room_type=简约大床房, date=2026-02-19, quantity=5, room_id=S1_P00006))",
    )
    ledger.observe(
        "get_ota_hotel_info",
        "Hotel(hotel_id=S1_H00038, hotel_name=汉庭酒店(北站地铁店), tags=['近地铁', '经济型'], products=HotelProduct(room_type=简约大床房, date=2026-02-19, quantity=5, room_id=S1_P00075))",
    )
    card = DecisionCard(
        prefer=["大床房", "近地铁", "简约风格", "汉庭酒店"]
    )
    assert ledger.shortlist(card, limit=1)[0].candidate_id == "S1_P00075"


def test_payment_confirmation_preserves_ready_to_pay():
    runtime = TaskRuntime.begin(TaskSpec.compile("帮我点杯咖啡送到家"))
    runtime.phase = RuntimePhase.READY_TO_PAY
    runtime.observe_user("确认支付")
    assert runtime.phase == RuntimePhase.READY_TO_PAY
    assert runtime.authorization.pay_authorized


def test_framework_asks_payment_once_before_exposing_pay():
    runtime = TaskRuntime.begin(TaskSpec.compile("帮我点杯咖啡送到家"))
    runtime.phase = RuntimePhase.READY_TO_PAY
    agent = object.__new__(ADAPTAgent)
    agent.runtime = runtime
    assert "支付" in agent._framework_payment_question()
    assert not agent._framework_payment_question()
    runtime.observe_user("可以，帮我付了")
    assert runtime.authorization.pay_authorized
    assert not agent._framework_payment_question()


def test_natural_self_payment_phrase_declines_agent_payment():
    runtime = TaskRuntime.begin(TaskSpec.compile("帮我点杯咖啡送到家"))
    runtime.phase = RuntimePhase.READY_TO_PAY
    runtime.observe_user("不用了，我自己会付")
    assert runtime.authorization.pay_declined
    assert not runtime.authorization.pay_authorized
    assert runtime.phase == RuntimePhase.DONE


def test_id_semantics_reject_product_id_in_store_slot():
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "StoreProduct(store_id=S1_S00001, product_name=鼠标, product_id=S1_P00001, quantity=3)",
    )
    errors = ledger.validate_write(
        "create_delivery_order",
        {"user_id": "U1", "store_id": "S1_P00001", "product_ids": ["S1_P00001"]},
        DecisionCard(),
    )
    assert any("expects store ID" in error for error in errors)


def test_debug_sidecar_filters_hidden_fields(tmp_path):
    store = DebugEventStore({"user_id": "U1"})
    store.emit("decision", phase="search", reward=1, target_product_ids=["hidden"])
    path = tmp_path / "trace.jsonl"
    store.dump_jsonl(path)
    content = path.read_text(encoding="utf-8")
    assert '"phase": "search"' in content
    assert "reward" not in content
    assert "target_product_ids" not in content


def test_memory_update_is_incremental_for_replayed_interactions():
    memory = ADAPTMemory(language="chinese")
    interaction = {
        "type": "order", "timestamp": "2026-01-01 10:00:00",
        "content": {"store_name": "川菜馆", "product_name": "水煮鱼"},
    }
    memory.update([interaction])
    event_count = len(memory.stream)
    fact_count = len(memory.facts)
    memory.update([interaction])
    assert len(memory.stream) == event_count
    assert len(memory.facts) == fact_count


def test_context_snapshot_keeps_recent_answer_and_tool_error():
    runtime = TaskRuntime.begin(TaskSpec.compile("帮我买一双拖鞋送到家"))
    runtime.observe_user("42-43码")
    runtime.observe_tool_result("create_delivery_order", "product id invalid", error=True)
    rendered = runtime.render()
    assert "42-43码" in rendered
    assert "product id invalid" in rendered


def test_required_argument_validation_handles_list_values():
    registry = ToolRegistry()
    registry.meta["search"] = ToolMeta(
        "search", ToolRole.SEARCH, required_arguments={"keywords"}
    )
    assert not registry.validate_required("search", {"keywords": ["奶茶"]})
    assert registry.validate_required("search", {"keywords": []}) == [
        "missing required argument: keywords"
    ]


def test_decision_card_reserves_space_for_must_and_prefer():
    card = DecisionCard(
        must=["address=company", "authorization=create"],
        avoid=[f"noise-{index}" for index in range(8)],
        prefer=["奶茶", "无小料", "经典原味"],
    )
    rendered = card.render()
    assert "address=company" in rendered
    assert "奶茶" in rendered
    assert "noise-2" not in rendered


def test_shortlist_does_not_spend_slots_on_parent_store_ids():
    ledger = CandidateLedger()
    for index in range(6):
        ledger.observe(
            "delivery_product_search_recommand",
            f"StoreProduct(store_id=S1_S0000{index}, product_name=奶茶{index}, "
            f"product_id=S1_P0000{index}, quantity=10, price={10 + index})",
        )
    shortlist = ledger.shortlist(DecisionCard(prefer=["奶茶"]))
    assert len(shortlist) == 5
    assert all(candidate.entity_type == "product" for candidate in shortlist)


def test_fact_store_rejects_non_consumption_negative_noise_and_merges_containment():
    store = FactStore()

    def negative(value: str, fact_id: str) -> PreferenceFact:
        return PreferenceFact(
            fact_id=fact_id,
            scope="delivery",
            facet="beverage",
            dimension="avoid",
            value=value,
            polarity="negative",
            confidence=0.9,
            observed_at="2026-01-01",
            source_type="conversation",
            category=value,
        )

    assert store.ingest(negative("某外卖配送员", "noise")) is None
    store.ingest(negative("任何小料", "avoid-1"))
    store.ingest(negative("小料", "avoid-2"))
    assert [fact.value for fact in store.active()] == ["小料"]


def test_general_game_facts_do_not_pollute_hotel_decision_card():
    facts = [
        PreferenceFact(
            fact_id="game",
            scope="general",
            facet="general",
            dimension="explicit",
            value="喜欢玩无畏契约",
            polarity="positive",
            confidence=0.99,
            observed_at="2026-02-01",
            source_type="conversation",
        ),
        PreferenceFact(
            fact_id="hotel",
            scope="ota",
            facet="hotel",
            dimension="brand",
            value="汉庭酒店(遂宁店)",
            polarity="positive",
            confidence=0.8,
            observed_at="2026-01-01",
            source_type="order",
        ),
    ]
    card = build_decision_card(TaskSpec.compile("帮我订明晚重庆的酒店"), facts)
    assert "喜欢玩无畏契约" not in card.prefer
    assert "汉庭酒店(遂宁店)" in card.prefer
    assert "汉庭酒店(遂宁店)" in card.preference_pool


def test_ranker_matches_brand_root_across_city_variants():
    ledger = CandidateLedger()
    ledger.observe(
        "hotel_search_recommand",
        "Hotel(hotel_id=S1_H00001, hotel_name=希岸酒店(重庆店), score=4.9)\n"
        "Hotel(hotel_id=S1_H00002, hotel_name=汉庭酒店(重庆大坪店), score=4.5)",
    )
    shortlist = ledger.shortlist(DecisionCard(prefer=["汉庭酒店(遂宁店)"]))
    assert shortlist[0].candidate_id == "S1_H00002"


def test_explicit_facet_preference_ranks_before_brand_history():
    facts = [
        PreferenceFact(
            fact_id="brand",
            scope="local_commerce",
            facet="beverage",
            dimension="brand",
            value="某奶茶店",
            polarity="positive",
            confidence=0.99,
            observed_at="2026-02-01",
            source_type="order",
        ),
        PreferenceFact(
            fact_id="explicit",
            scope="local_commerce",
            facet="beverage",
            dimension="explicit",
            value="奶茶不加小料/原味",
            polarity="positive",
            confidence=0.9,
            observed_at="2026-01-01",
            source_type="conversation",
        ),
    ]
    card = build_decision_card(TaskSpec.compile("帮我点杯喝的送到公司"), facts)
    assert "奶茶不加小料/原味" in card.preference_pool
    assert "某奶茶店" in card.preference_pool


def test_negated_attribute_does_not_trigger_avoid_constraint():
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "StoreProduct(store_id=S1_S00001, product_name=经典原味奶茶, "
        "product_id=S1_P00001, attributes=大杯, 热饮, 无小料, quantity=30)",
    )
    card = DecisionCard(avoid=["小料"])
    assert ledger.shortlist(card)[0].candidate_id == "S1_P00001"
    assert not ledger.validate_write(
        "create_delivery_order",
        {"store_id": "S1_S00001", "product_ids": ["S1_P00001"]},
        card,
    )


def test_current_category_constraint_does_not_directly_name_all_ota_facts():
    facts = [
        PreferenceFact(
            fact_id="ticket",
            scope="ota",
            facet="attraction",
            dimension="product",
            value="古羌城门票",
            polarity="positive",
            confidence=0.99,
            observed_at="2026-02-01",
            source_type="order",
        ),
        PreferenceFact(
            fact_id="hotel-brand",
            scope="ota",
            facet="hotel",
            dimension="brand",
            value="汉庭酒店",
            polarity="positive",
            confidence=0.8,
            observed_at="2026-01-01",
            source_type="order",
        ),
    ]
    card = build_decision_card(TaskSpec.compile("帮我订明晚重庆的酒店"), facts)
    assert "古羌城门票" not in card.prefer
    assert "汉庭酒店" in card.prefer
    assert {"古羌城门票", "汉庭酒店"}.issubset(card.preference_pool)


def test_open_world_task_identity_filters_unseen_product_categories():
    pairs = [
        ("帮我买个空气炸锅送到家", "星云空气炸锅5L", "星云电饭煲5L"),
        ("帮我买台卧室除湿机送到家", "静音卧室除湿机", "静音卧室加湿器"),
        ("帮我买一盒隐形眼镜送到家", "日抛隐形眼镜", "偏光太阳镜"),
        ("帮我买个露营灯送到家", "防水露营灯", "家用阅读灯"),
    ]
    for instruction, expected, distraction in pairs:
        ledger = CandidateLedger()
        ledger.observe(
            "delivery_product_search_recommand",
            "StoreProduct(store_id=S1_S00001, product_name="
            f"{expected}, product_id=S1_P00001, quantity=5)\n"
            "StoreProduct(store_id=S1_S00001, product_name="
            f"{distraction}, product_id=S1_P00002, quantity=5)",
        )
        card = build_decision_card(TaskSpec.compile(instruction), [])
        shortlist = ledger.shortlist(card)
        assert shortlist
        assert shortlist[0].name == expected
        assert all(candidate.name != distraction for candidate in shortlist)


def test_open_world_alternatives_remain_a_ranking_choice():
    ledger = CandidateLedger()
    ledger.observe(
        "delivery_product_search_recommand",
        "StoreProduct(store_id=S1_S00001, product_name=空气炸锅, "
        "product_id=S1_P00001, quantity=5)\n"
        "StoreProduct(store_id=S1_S00001, product_name=电饭煲, "
        "product_id=S1_P00002, quantity=5)",
    )
    card = build_decision_card(
        TaskSpec.compile("空气炸锅或者电饭煲都行，帮我买一个送到家"), []
    )
    assert {candidate.name for candidate in ledger.shortlist(card)} == {
        "空气炸锅",
        "电饭煲",
    }


def test_tool_topology_overrides_text_lexicon_for_unseen_service():
    registry = ToolRegistry()
    registry.meta = {
        "instore_shop_search_recommend": ToolMeta(
            "instore_shop_search_recommend", ToolRole.SEARCH
        ),
        "instore_reservation": ToolMeta(
            "instore_reservation", ToolRole.CREATE
        ),
    }
    assert registry.domain_hint() == "instore"
    spec = TaskSpec.compile(
        "帮我预约一家室内攀岩馆", domain_hint=registry.domain_hint()
    )
    assert spec.domain == "instore"
    assert spec.facet == "service"


def test_candidate_selection_is_not_replayed_as_candidate_observation():
    runtime = TaskRuntime.begin(TaskSpec.compile("帮我看看空气炸锅"))
    runtime.observe_candidates(2, execution_ready=True)
    runtime.authorization.create_authorized = True
    runtime.select_candidate("S1_P00001", execution_ready=True)
    assert runtime.phase == RuntimePhase.READY_TO_CREATE
    assert sum(event["event"] == "candidates" for event in runtime.events) == 1
    assert sum(event["event"] == "selection" for event in runtime.events) == 1


def test_schema_driven_candidate_flow_uses_no_vitabench_names_or_id_shapes():
    class SearchParams:
        @classmethod
        def model_json_schema(cls):
            return {"properties": {"incantation": {"type": "string"}}}

    class SearchReturns:
        @classmethod
        def model_json_schema(cls):
            return {
                "type": "object",
                "properties": {
                    "nebula": {
                        "type": "object",
                        "properties": {
                            "echoes": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "x-adapt-entity": "relic",
                                    "properties": {
                                        "sigil": {
                                            "type": "string",
                                            "x-adapt-role": "id",
                                        },
                                        "caption": {
                                            "type": "string",
                                            "x-adapt-role": "name",
                                        },
                                        "reserve": {
                                            "type": "integer",
                                            "x-adapt-role": "inventory",
                                        },
                                        "cost": {
                                            "type": "number",
                                            "x-adapt-role": "price",
                                        },
                                        "origin_ref": {
                                            "type": "string",
                                            "x-adapt-role": "parent_id",
                                        },
                                        "traits": {
                                            "type": "object",
                                            "x-adapt-role": "attribute",
                                        },
                                    },
                                },
                            }
                        },
                    }
                },
            }

    class CommitParams:
        @classmethod
        def model_json_schema(cls):
            return {
                "required": ["chosen_sigil", "actor_token"],
                "properties": {
                    "chosen_sigil": {
                        "type": "string",
                        "x-adapt-entity": "relic",
                    },
                    "actor_token": {
                        "type": "string",
                        "x-adapt-role": "user_id",
                    },
                },
            }

    class EmptyReturns:
        @classmethod
        def model_json_schema(cls):
            return {"type": "string"}

    class Tool:
        def __init__(self, name, role, params, returns):
            self.name = name
            self.info = {"adapt_role": role, "adapt_domain": "synthetic"}
            self.params = params
            self.returns = returns

    search = Tool("survey_constellation", "search", SearchParams, SearchReturns)
    commit = Tool("seal_the_choice", "create", CommitParams, EmptyReturns)
    registry = ToolRegistry()
    registry.rebuild([search, commit])
    ledger = CandidateLedger()
    ledger.observe(
        search.name,
        {
            "nebula": {
                "echoes": [
                    {
                        "sigil": "sig::empty",
                        "caption": "Aurora Prism Basic",
                        "reserve": 0,
                        "cost": 3,
                        "origin_ref": "forge::north",
                        "traits": {"hue": "violet", "material": "glass"},
                    },
                    {
                        "sigil": "sig::ready",
                        "caption": "Aurora Prism Expedition",
                        "reserve": 4,
                        "cost": 8,
                        "origin_ref": "forge::north",
                        "traits": {"hue": "violet", "material": "quartz"},
                    },
                    {
                        "sigil": "sig::other",
                        "caption": "Lunar Compass",
                        "reserve": 9,
                        "cost": 1,
                        "origin_ref": "forge::south",
                        "traits": {"hue": "silver", "material": "steel"},
                    },
                ]
            }
        },
        registry.result_schema(search.name),
    )
    card = DecisionCard(task_intent=["Acquire an Aurora Prism"], prefer=["violet"])
    assert [candidate.candidate_id for candidate in ledger.shortlist(card)] == [
        "sig::ready"
    ]
    assert registry.execution_ready(ledger, card)
    meta = registry.meta[commit.name]
    assert not ledger.validate_write(
        commit.name,
        {"chosen_sigil": "sig::ready", "actor_token": "actor::7"},
        card,
        tool_meta=meta,
    )
    assert ledger.validate_write(
        commit.name,
        {"chosen_sigil": "invented::id", "actor_token": "actor::7"},
        card,
        tool_meta=meta,
    ) == ["chosen_sigil=invented::id was not returned by a tool in this subtask"]


def test_schema_driven_flow_accepts_root_array_and_plural_opaque_ids():
    class SearchParams:
        @classmethod
        def model_json_schema(cls):
            return {"properties": {"rune": {"type": "string"}}}

    class SearchReturns:
        @classmethod
        def model_json_schema(cls):
            return {
                "type": "array",
                "items": {
                    "type": "object",
                    "x-adapt-entity": "glyph",
                    "properties": {
                        "mark": {"x-adapt-role": "id"},
                        "title": {
                            "x-adapt-role": "name",
                            "x-adapt-constraint": True,
                        },
                        "stockpulse": {"x-adapt-role": "inventory"},
                        "tone": {"x-adapt-role": "attribute"},
                        "cadence": {
                            "x-adapt-role": "attribute",
                            "x-adapt-constraint": True,
                        },
                    },
                },
            }

    class CommitParams:
        @classmethod
        def model_json_schema(cls):
            return {
                "required": ["selected_marks", "keeper"],
                "properties": {
                    "selected_marks": {
                        "type": "array",
                        "x-adapt-entity": "glyph",
                    },
                    "keeper": {"x-adapt-role": "user_id"},
                },
            }

    class Tool:
        def __init__(self, name, role, params, returns):
            self.name = name
            self.info = {"adapt_role": role}
            self.params = params
            self.returns = returns

    search = Tool("listen_for_glyphs", "search", SearchParams, SearchReturns)
    commit = Tool("bind_many_marks", "create", CommitParams, SearchReturns)
    registry = ToolRegistry()
    registry.rebuild([search, commit])
    ledger = CandidateLedger()
    ledger.observe(
        search.name,
        [
            {
                "mark": "mark://velorian",
                "title": "Velorian Thrum",
                "stockpulse": 2,
                "tone": "amber",
                "cadence": "triple",
            },
            {
                "mark": "mark://dusk",
                "title": "Dusk Lattice",
                "stockpulse": 7,
                "tone": "indigo",
                "cadence": "single",
            },
        ],
        registry.result_schema(search.name),
    )
    card = DecisionCard(
        task_intent=["Bind the Velorian Thrum with triple cadence"]
    )
    grounded = ledger.ground_task_constraints(card)
    assert {constraint.value for constraint in grounded} >= {
        "Velorian Thrum",
        "triple",
    }
    assert [candidate.candidate_id for candidate in ledger.shortlist(card)] == [
        "mark://velorian"
    ]
    meta = registry.meta[commit.name]
    assert not ledger.validate_write(
        commit.name,
        {
            "selected_marks": ["mark://velorian"],
            "keeper": "keeper://9",
        },
        card,
        tool_meta=meta,
    )

"""Acceptance tests for trace-derived, capability-level architecture repairs."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from agent.adapt_agent import ADAPTAgent
from agent.decision import Candidate, CandidateLedger, DecisionCard, TaskSpec
from agent.intent import DesiredOutcome, completion_contract
from agent.memory.adapt_memory import ADAPTMemory
from agent.memory.facts import fact_from_signal
from agent.memory.signals import Signal
from agent.runtime import (
    RuntimePhase,
    TaskRuntime,
    ToolOutcomeNormalizer,
    ToolRegistry,
)
from agent.runtime.argument_binding import ArgumentBindingResolver
from agent.runtime.contracts import ToolContractCompiler
from agent.runtime.grounding import TaskRelevanceMatrix
from agent.runtime.ranking import CandidateRanker
from agent.runtime.tool_errors import ToolErrorLedger
from agent.runtime.tools import ToolMeta, ToolRole
from agent.vitabench_runner import (
    EvaluationIntegrityError,
    _assert_simulation_evaluation_integrity,
    _configure_loopback_no_proxy,
    _install_evaluation_fail_fast,
    external_model_config_fingerprint,
    implementation_fingerprint,
)
from agent.vitabench_bootstrap import configure_adapt_model_config
from vita.data_model.message import AssistantMessage, ToolCall, UserMessage
from vita.data_model.simulation import RewardInfo


class _Tool:
    def __init__(self, name: str):
        self.name = name


def test_search_prompt_defers_historical_entities_but_keeps_current_answer():
    agent = ADAPTAgent(
        tools=[],
        domain_policy="time={time}",
        memory=ADAPTMemory(language="chinese"),
        user_profile={"user_id": "synthetic-user"},
        time="2026-08-26 12:00:00",
        language="chinese",
        enable_lessons=False,
    )
    agent.set_current_instruction("寻找一种虚构构件")
    agent.decision_card = DecisionCard(
        must=["接口完整"],
        prefer=["历史商户锚点", "当前回答锚点"],
        evidence=["历史商户锚点 <- order"],
        task_intent=["寻找一种虚构构件"],
        preference_source_types={
            "历史商户锚点": ("order",),
            "当前回答锚点": ("current_user_answer",),
        },
    )
    agent.runtime.phase = RuntimePhase.SEARCH
    search_prompt = agent.system_prompt
    assert "接口完整" in search_prompt
    assert "当前回答锚点" in search_prompt
    assert "历史商户锚点" not in search_prompt

    agent.runtime.phase = RuntimePhase.SELECT
    assert "历史商户锚点" in agent.system_prompt


def test_open_world_negative_clauses_project_to_live_candidate_attributes():
    memory = ADAPTMemory(language="chinese")
    memory.update(
        [
            {
                "date": "2026-01-01",
                "behavior": [],
                "dialogue": [
                    {"role": "user", "content": "苍穹材质就算了，之前用过不太喜欢。"},
                    {
                        "role": "user",
                        "content": "超巨型规格要放很久，一点儿也不好。",
                    },
                ],
            }
        ]
    )
    candidates = [
        Candidate(
            "node-a",
            "glyph",
            "构件甲",
            "vendor=苍穹, shape=超巨型",
            "scan",
            attributes={"vendor": "苍穹", "shape": "超巨型"},
        ),
        Candidate(
            "node-b",
            "glyph",
            "构件乙",
            "vendor=远潮, shape=轻巧型",
            "scan",
            attributes={"vendor": "远潮", "shape": "轻巧型"},
        ),
    ]
    card = memory.compile_task("寻找一种虚构构件")
    stats = memory.apply_candidate_grounding(card, candidates)
    assert {"苍穹", "超巨型"}.issubset(card.avoid)
    assert stats["grounded_negative"] >= 2
    assert [item.candidate_id for item in CandidateRanker().rank(candidates, card)] == [
        "node-b"
    ]


def test_quantity_unit_unigram_does_not_become_task_relevance():
    candidates = [
        Candidate("node-large", "glyph", "星雾液大瓶装", "", "scan"),
        Candidate("node-calm", "glyph", "星雾液清爽型", "", "scan"),
    ]
    matrix = TaskRelevanceMatrix("买两瓶星雾液", candidates)
    assert matrix.row(candidates[0]).score == matrix.row(candidates[1]).score


def test_leaf_attribute_preference_precedes_parent_merchant_identity():
    candidates = [
        Candidate(
            "node-parent",
            "glyph",
            "基础构件",
            "merchant_name=苍穹集市, feature=基础",
            "scan",
            attributes={"merchant_name": "苍穹集市", "feature": "基础"},
        ),
        Candidate(
            "node-leaf",
            "glyph",
            "静音构件",
            "merchant_name=远潮集市, feature=静音",
            "scan",
            attributes={"merchant_name": "远潮集市", "feature": "静音"},
        ),
    ]
    card = DecisionCard(
        task_intent=["选择虚构构件"],
        preference_pool=["苍穹集市", "静音"],
        preference_weights={"苍穹集市": 0.99, "静音": 0.5},
        preference_source_types={
            "苍穹集市": ("order",),
            "静音": ("conversation",),
        },
    )
    assert CandidateRanker().rank(candidates, card)[0].candidate_id == "node-leaf"


def test_exact_attribute_value_aligns_across_schema_keys():
    candidates = [
        Candidate(
            "node-field",
            "glyph",
            "构件甲",
            "audience=夜行者",
            "scan",
            attributes={"audience": "夜行者"},
        ),
        Candidate(
            "node-tag",
            "glyph",
            "构件乙",
            "tag=夜行者",
            "scan",
            attributes={"tag": "夜行者"},
        ),
    ]
    counts = CandidateRanker.preference_match_counts(
        candidates,
        DecisionCard(preference_pool=["夜行者"]),
    )
    assert counts == {"node-field": 1, "node-tag": 1}


def test_evaluator_failure_is_never_cached_as_agent_reward_zero():
    class Subtask:
        subtask_id = "visible-subtask"

    class Orchestrator:
        def _evaluate_subtask(self, subtask, result):
            return RewardInfo(
                reward=0.0,
                info={"note": "Evaluation error: Error code: 502"},
            )

    orchestrator = Orchestrator()
    _install_evaluation_fail_fast(orchestrator)
    with __import__("pytest").raises(EvaluationIntegrityError):
        orchestrator._evaluate_subtask(Subtask(), {})


def test_successful_evaluation_passes_integrity_gate():
    class Simulation:
        reward_info = RewardInfo(reward=0.0, info={"note": "valid low score"})
        subtask_results = []

    _assert_simulation_evaluation_integrity(Simulation())


def test_local_model_endpoints_bypass_system_proxy_without_losing_existing_rules():
    import os

    with patch.dict(os.environ, {"NO_PROXY": "internal.example"}, clear=False):
        _configure_loopback_no_proxy()
        entries = set(os.environ["NO_PROXY"].split(","))
        assert entries == {
            "internal.example",
            "localhost",
            "127.0.0.1",
            "::1",
        }
        assert os.environ["no_proxy"] == os.environ["NO_PROXY"]


def test_external_model_overlay_selection_preserves_explicit_override(tmp_path):
    import os

    explicit = tmp_path / "models.yaml"
    explicit.write_text("default: {}\nmodels: []\n", encoding="utf-8")
    with patch.dict(
        os.environ, {"VITA_MODEL_CONFIG_PATH": str(explicit)}, clear=False
    ):
        assert configure_adapt_model_config() == explicit.resolve()
        assert Path(os.environ["VITA_MODEL_CONFIG_PATH"]) == explicit.resolve()


def test_normalized_business_failure_never_becomes_successful_create():
    for content in (
        "No available rooms at the moment",
        "The flight does not have the specified seat on the specified date",
        {"status": "failed", "order_id": "O-bad"},
    ):
        outcome = ToolOutcomeNormalizer.normalize(
            tool_name="create_synthetic_order",
            tool_role="create",
            content=content,
        )
        assert not outcome.ok
        assert outcome.effect.value == "no_change"

    success = ToolOutcomeNormalizer.normalize(
        tool_name="create_synthetic_order",
        tool_role="create",
        content={"status": "success", "error": None, "order_id": "O-good"},
    )
    assert success.ok


def test_empty_state_query_is_observed_and_reports_without_retry_loop():
    outcome = ToolOutcomeNormalizer.normalize(
        tool_name="get_orders",
        tool_role="state_read",
        content="No delivery orders available",
    )
    assert outcome.ok
    runtime = TaskRuntime.begin(TaskSpec.compile("查询一下我的订单状态"))
    runtime.observe_workflow_state(has_state=False)
    assert runtime.phase == RuntimePhase.REPORT


def test_negated_transaction_remains_information_only():
    assert (
        completion_contract("不要帮我下单，只推荐几个候选").desired_outcome
        == DesiredOutcome.INFORM
    )
    assert (
        completion_contract("不用预订，看看有哪些").desired_outcome
        == DesiredOutcome.INFORM
    )


def test_party_size_and_profile_alias_bind_to_action_schema_not_candidate():
    class Params:
        @classmethod
        def model_json_schema(cls):
            return {
                "required": ["shop_id", "customer_count", "address"],
                "properties": {
                    "shop_id": {"x-adapt-entity": "shop"},
                    "customer_count": {"type": "integer"},
                    "address": {"type": "string"},
                },
            }

    tool = _Tool("instore_book")
    tool.params = Params
    tool.info = {"adapt_role": "create"}
    registry = ToolRegistry()
    registry.rebuild([tool])
    spec = TaskSpec.compile("帮我订6个人的位置，送到家里")
    contract = registry.contract("instore_book")
    bound = ArgumentBindingResolver.bind(
        contract,
        spec,
        spec.resolved_slots,
        {"常住住址": "虚构市星河路8号"},
    )
    assert bound["customer_count"] == 6
    assert bound["address"] == "虚构市星河路8号"
    party = next(item for item in spec.must if item.kind == "party_size")
    assert party.target.value == "argument"


def test_per_tool_missing_arguments_do_not_block_selected_execution_plan():
    registry = ToolRegistry()
    registry.meta = {
        "activate_glyph": ToolMeta(
            name="activate_glyph",
            role=ToolRole.CREATE,
            required_arguments={"glyph_ref"},
            id_arguments={"glyph_ref": "glyph"},
            argument_schemas={"glyph_ref": {}},
            semantic_text="activate resonance glyph",
        ),
        "archive_glyph": ToolMeta(
            name="archive_glyph",
            role=ToolRole.CREATE,
            required_arguments={"glyph_ref", "archive_mode"},
            id_arguments={"glyph_ref": "glyph"},
            question_arguments={"archive_mode": "Which archive mode?"},
            argument_schemas={
                "glyph_ref": {},
                "archive_mode": {
                    "x-adapt-source": "user_required",
                    "x-adapt-question-text": "Which archive mode?",
                },
            },
            semantic_text="archive glyph",
        ),
    }
    registry.contracts = ToolContractCompiler.compile(registry.meta.values())
    ledger = CandidateLedger()
    ledger.candidates["glyph::1"] = Candidate(
        "glyph::1", "glyph", "Aurora", "resonance glyph", "scan"
    )
    runtime = TaskRuntime.begin(TaskSpec.compile("activate resonance glyph"))
    runtime.authorization.create_authorized = True
    runtime.authorization.choice_delegated = True
    decision = registry.candidate_decision(
        ledger,
        DecisionCard(task_intent=["activate resonance glyph"]),
        runtime=runtime,
    )
    assert decision.selected is not None
    assert decision.selected.create_tool == "activate_glyph"
    assert decision.missing_arguments == ()
    assert decision.next_phase == RuntimePhase.READY_TO_CREATE


def test_ready_create_exposes_and_freezes_only_selected_plan():
    registry = ToolRegistry()
    registry.tools = [_Tool("create_alpha"), _Tool("create_beta")]
    registry.meta = {
        name: ToolMeta(name, ToolRole.CREATE, {"item_id"}, {"item_id": "item"})
        for name in ("create_alpha", "create_beta")
    }
    registry.contracts = ToolContractCompiler.compile(registry.meta.values())
    runtime = TaskRuntime.begin(TaskSpec.compile("帮我下单"))
    runtime.phase = RuntimePhase.READY_TO_CREATE
    runtime.authorization.create_authorized = True
    runtime.planned_create_tool = "create_beta"
    runtime.planned_create_arguments = {"item_id": "item::chosen"}
    assert [tool.name for tool in registry.allowed_tools(runtime, CandidateLedger())] == [
        "create_beta"
    ]

    agent = object.__new__(ADAPTAgent)
    agent.tool_registry = registry
    agent.runtime = runtime
    agent.task_spec = runtime.spec
    agent.decision_card = DecisionCard()
    agent.user_profile = {}
    agent.tool_errors = ToolErrorLedger()
    agent.ledger = CandidateLedger()
    prepared = agent._prepare_tool_call(
        ToolCall(
            id="create",
            name="create_beta",
            arguments={"item_id": "item::model-choice"},
        )
    )
    assert prepared.arguments["item_id"] == "item::chosen"


def test_workflow_tools_are_filtered_and_unique_state_id_is_bound():
    registry = ToolRegistry()
    registry.tools = [_Tool("cancel_order"), _Tool("cancel_booking")]
    registry.meta = {
        "cancel_order": ToolMeta(
            "cancel_order", ToolRole.CANCEL, {"user_id", "order_id"},
            {"user_id": "user", "order_id": "order"},
            argument_schemas={"user_id": {}, "order_id": {}},
        ),
        "cancel_booking": ToolMeta(
            "cancel_booking", ToolRole.CANCEL, {"booking_id"},
            {"booking_id": "booking"},
            argument_schemas={"booking_id": {}},
        ),
    }
    registry.contracts = ToolContractCompiler.compile(registry.meta.values())
    ledger = CandidateLedger()
    ledger.state_ids["order"] = {"order::7"}
    runtime = TaskRuntime.begin(TaskSpec.compile("取消我的订单"))
    runtime.phase = RuntimePhase.READY_TO_WORKFLOW
    assert [tool.name for tool in registry.allowed_tools(runtime, ledger)] == [
        "cancel_order"
    ]

    agent = object.__new__(ADAPTAgent)
    agent.tool_registry = registry
    agent.runtime = runtime
    agent.task_spec = runtime.spec
    agent.decision_card = DecisionCard()
    agent.user_profile = {"user_id": "user::1"}
    agent.tool_errors = ToolErrorLedger()
    agent.ledger = ledger
    prepared = agent._prepare_tool_call(
        ToolCall(id="cancel", name="cancel_order", arguments={})
    )
    assert prepared.arguments == {"user_id": "user::1", "order_id": "order::7"}


def test_workflow_tool_family_follows_state_read_provenance():
    registry = ToolRegistry()
    registry.tools = [_Tool("cancel_train_order"), _Tool("cancel_hotel_order")]
    registry.meta = {
        name: ToolMeta(
            name,
            ToolRole.CANCEL,
            {"order_id"},
            {"order_id": "order"},
            argument_schemas={"order_id": {}},
            semantic_text=name,
        )
        for name in ("cancel_train_order", "cancel_hotel_order")
    }
    registry.contracts = ToolContractCompiler.compile(registry.meta.values())
    ledger = CandidateLedger()
    ledger.observe_state(
        "search_train_order", "TrainOrder(order_id=order::train7, status=paid)"
    )
    runtime = TaskRuntime.begin(TaskSpec.compile("取消我的火车订单"))
    runtime.phase = RuntimePhase.READY_TO_WORKFLOW
    assert [tool.name for tool in registry.allowed_tools(runtime, ledger)] == [
        "cancel_train_order"
    ]


def test_current_correction_replaces_scalar_entity_and_keeps_multiple_avoids():
    agent = ADAPTAgent(
        tools=[],
        domain_policy="time={time}",
        memory=ADAPTMemory(language="chinese"),
        user_profile={"user_id": "correction-user"},
        time="2026-08-26 12:00:00",
        language="chinese",
        enable_lessons=False,
    )
    agent.set_current_instruction("指定星云片，帮我下单送到家里")
    agent._observe_input(
        UserMessage(role="user", content="不对，我说的是极光片，不要红色")
    )
    agent._observe_input(UserMessage(role="user", content="另外不要蓝色"))
    entities = [item for item in agent.task_spec.must if item.kind == "entity"]
    assert [item.value for item in entities] == ["极光片"]
    assert {item.value for item in agent.task_spec.avoid} >= {"红色", "蓝色"}
    assert entities[0].source == "user_correction"


def test_drift_is_separate_across_categories_within_same_facet():
    from agent.memory.drift import DriftDetector

    detector = DriftDetector(drift_threshold=2)
    detector.observe(Signal("taste_preference", "少糖", 0.8, "2026-01-01", "order", "奶茶少糖"))
    detector.observe(Signal("taste_preference", "无糖", 0.8, "2026-01-02", "order", "咖啡无糖"))
    detector.observe(Signal("taste_preference", "无糖", 0.8, "2026-01-03", "order", "咖啡无糖"))
    assert detector.drift_summary() == []


def test_llm_preference_requires_user_turn_and_user_text_anchor():
    memory = ADAPTMemory(language="chinese")
    assistant_only = [{
        "date": "2026-01-01",
        "dialogue": [{"role": "assistant", "content": "你喜欢麻辣"}],
    }]
    assert memory._llm_extract_preferences(assistant_only, "fake", {}) == []

    response = AssistantMessage(
        role="assistant",
        content='[{"predicate":"likes_food","object":"麻辣","confidence":0.9}]',
    )
    user_dialogue = [{
        "date": "2026-01-01",
        "dialogue": [{"role": "user", "content": "我喜欢清淡"}],
    }]
    with patch("vita.utils.llm_utils.generate", return_value=response):
        assert memory._llm_extract_preferences(user_dialogue, "fake", {}) == []


def test_lifecycle_expiration_keeps_protected_safety_fact():
    memory = ADAPTMemory(language="chinese")
    safety = Signal(
        "avoids_food", "花生", 0.9, "2026-01-01", "complaint", "花生过敏"
    )
    product = Signal(
        "prefers_product", "星云片", 0.6, "2026-01-01", "search", "搜索星云片"
    )
    safety_event = memory.stream.add(safety)
    product_event = memory.stream.add(product)
    safety_fact = fact_from_signal(safety, str(safety_event.id))
    product_fact = fact_from_signal(product, str(product_event.id))
    memory._ingest_fact(safety_fact)
    memory._ingest_fact(product_fact)
    memory.stream.events = []
    memory._sync_fact_lifecycle()
    assert safety_fact.status == "active"
    stored_product = next(
        fact for fact in memory.facts if fact.value == product_fact.value
    )
    assert stored_product.status == "expired"


def test_runner_fingerprints_code_and_resolved_model_config():
    assert len(implementation_fingerprint()) == 20
    assert len(external_model_config_fingerprint() or "") == 20

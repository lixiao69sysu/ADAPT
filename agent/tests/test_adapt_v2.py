"""Non-regression and safety tests for the stock-first ADAPT V2."""

from __future__ import annotations

import inspect
import json
from types import SimpleNamespace

from vita.agent.personalization_agent import PersonalizationAgent
from vita.data_model.message import (
    AssistantMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from vita.data_model.simulation import RewardInfo
from vita.environment.tool import Tool
from vita.environment.toolkit import ToolType, is_tool
from vita.memory.rewrite_memory import RewriteMemory
from vita.orchestrator.personalization_orchestrator import PersonalizationOrchestrator

from agent.evaluation_integrity import IntegrityPersonalizationOrchestrator
from agent.trace_metrics import summarize
from agent.v2 import ADAPTV2, HybridMemory, V2FeatureFlags
from agent.v2.evaluation import ABLATION_MATRIX, promotion_report
from agent.v2.memory import EvidenceRecord, PreferenceBelief
from agent.v2.observation import ObservationStore
from agent.v2.planner import ModelPlanner
from agent.v2.transaction import OperationJournal, TransactionKernel
from agent.v2.workspace import DecisionWorkspace


def _agent(agent_type, *, flags=None, memory=None):
    kwargs = {
        "tools": [],
        "domain_policy": "Current time: {time}",
        "memory": memory or RewriteMemory(language="chinese"),
        "user_profile": {"user_id": "U1"},
        "llm": "test-model",
        "llm_args": {"temperature": 0.0, "seed": 42},
        "time": "2026-09-02 10:00:00",
        "enable_think": False,
        "language": "chinese",
    }
    if agent_type is ADAPTV2:
        kwargs["feature_flags"] = flags or V2FeatureFlags()
    return agent_type(**kwargs)


def test_v2_defaults_are_stock_equivalent_flags():
    flags = V2FeatureFlags()
    assert not any(flags.as_dict().values())
    assert not flags.changes_execution_request
    assert set(ABLATION_MATRIX) == {
        "stock_rewrite",
        "stock_hybrid",
        "v2_planner_rewrite",
        "v2_full",
        "stock_groundtruth",
    }


def test_v2_all_off_uses_field_equivalent_stock_request(monkeypatch):
    captured: list[dict] = []

    def fake_generate(**kwargs):
        captured.append(kwargs)
        return AssistantMessage(role="assistant", content="same response")

    monkeypatch.setattr("vita.agent.llm_agent.generate", fake_generate)
    stock = _agent(PersonalizationAgent)
    v2 = _agent(ADAPTV2)
    stock.set_current_instruction("帮我选择一个商品")
    v2.set_current_instruction("帮我选择一个商品")
    stock_response, _ = stock.generate_next_message(
        UserMessage(role="user", content="开始"), stock.get_init_state()
    )
    v2_response, _ = v2.generate_next_message(
        UserMessage(role="user", content="开始"), v2.get_init_state()
    )

    assert stock_response == v2_response
    assert len(captured) == 2
    first, second = captured
    assert first.keys() == second.keys()
    assert first["model"] == second["model"]
    assert first["tools"] == second["tools"]
    assert first["messages"] == second["messages"]
    assert first["enable_think"] == second["enable_think"]
    assert first["temperature"] == second["temperature"]
    assert first["seed"] == second["seed"]


def test_shadow_workspace_collects_without_changing_policy_request(monkeypatch):
    captured = []

    def fake_generate(**kwargs):
        captured.append(kwargs)
        return AssistantMessage(role="assistant", content="stock path")

    monkeypatch.setattr("vita.agent.llm_agent.generate", fake_generate)
    agent = _agent(
        ADAPTV2,
        flags=V2FeatureFlags(shadow_workspace=True),
    )
    agent.set_current_instruction("帮我选择")
    agent.generate_next_message(
        UserMessage(role="user", content="开始"), agent.get_init_state()
    )
    assert len(captured) == 1
    assert len(captured[0]["messages"]) == 2
    assert agent.shadow_events


def test_shadow_transaction_records_rejection_without_changing_output(monkeypatch):
    proposed = AssistantMessage(
        role="assistant",
        tool_calls=[
            ToolCall(
                id="shadow-bad",
                name="create_order",
                arguments={
                    "store_id": "S1",
                    "product_ids": ["UNOBSERVED"],
                    "delivery_date": "2026-09-10",
                },
            )
        ],
    )
    monkeypatch.setattr("vita.agent.llm_agent.generate", lambda **_: proposed)
    agent = _agent(
        ADAPTV2,
        flags=V2FeatureFlags(shadow_transaction=True),
    )
    agent.update_tools([Tool(func=create_order)])
    agent.set_current_instruction("请在2026-09-10送达")
    result, _ = agent.generate_next_message(
        UserMessage(role="user", content="开始"), agent.get_init_state()
    )
    assert result is proposed
    assert agent.shadow_events[-1]["transaction_shadow"]["problems"]
    assert not agent.operation_journal.records


def test_active_workspace_is_advisory_and_keeps_all_stock_tools(monkeypatch):
    captured = []

    def fake_generate(**kwargs):
        captured.append(kwargs)
        return AssistantMessage(role="assistant", content="model decides")

    monkeypatch.setattr("agent.v2.executor.generate", fake_generate)
    agent = _agent(
        ADAPTV2,
        flags=V2FeatureFlags(decision_workspace=True),
    )
    tools = [Tool(func=create_order), Tool(func=pay_order)]
    agent.update_tools(tools)
    agent.set_current_instruction("帮我选择")
    agent.generate_next_message(
        UserMessage(role="user", content="开始"), agent.get_init_state()
    )
    assert captured[0]["tools"] == tools
    assert sum(isinstance(item, UserMessage) for item in captured[0]["messages"]) == 1
    assert "Decision workspace" in captured[0]["messages"][1].content


def test_hybrid_memory_keeps_rewrite_and_adds_traceable_soft_evidence():
    memory = HybridMemory(language="chinese")
    memory.update(
        [
            {
                "type": "order",
                "timestamp": "2026-08-01 10:00:00",
                "content": {"product_name": "经典奶茶"},
            }
        ],
        llm=None,
    )
    assert "经典奶茶" in memory.read()
    rendered = memory.render_evidence(query="奶茶")
    assert "evidence=" in rendered
    belief = next(iter(memory.belief_store.beliefs.values()))
    assert belief.evidence_ids
    assert belief.evidence_ids[0] in memory.belief_store.evidence


def test_belief_conflict_is_preserved_and_marked_not_deleted():
    memory = HybridMemory(language="chinese")
    old_evidence = EvidenceRecord(
        evidence_id="old-evidence",
        observed_at="2026-01-01",
        source_type="conversation",
        content="old",
    )
    new_evidence = EvidenceRecord(
        evidence_id="new-evidence",
        observed_at="2026-02-01",
        source_type="conversation",
        content="new",
    )
    memory.belief_store.evidence.update(
        {old_evidence.evidence_id: old_evidence, new_evidence.evidence_id: new_evidence}
    )
    memory.belief_store.add(
        PreferenceBelief(
            belief_id="old",
            statement="prefers A",
            first_seen="2026-01-01",
            last_seen="2026-01-01",
            evidence_ids=["old-evidence"],
        )
    )
    memory.belief_store.add(
        PreferenceBelief(
            belief_id="new",
            statement="prefers B now",
            first_seen="2026-02-01",
            last_seen="2026-02-01",
            evidence_ids=["new-evidence"],
            conflicts_with=["old"],
        )
    )
    assert memory.belief_store.beliefs["old"].status == "contradicted"
    assert "old" in memory.belief_store.beliefs


def test_observation_store_binds_exact_ids_parent_and_zero_inventory():
    store = ObservationStore()
    created = store.observe(
        "search",
        json.dumps(
            {
                "items": [
                    {"store_id": "S1", "product_id": "P1", "quantity": 0},
                    {"store_id": "S1", "product_id": "P2", "quantity": 4},
                ]
            }
        ),
        turn_id=1,
    )
    assert created
    assert store.contains("P1") and store.contains("S1")
    assert store.latest("P1").inventory_state == "unavailable"
    assert store.co_observed("S1", "P2")
    assert not store.contains("invented")


def test_planner_removes_unobserved_candidate_ids(monkeypatch):
    response = AssistantMessage(
        role="assistant",
        content=json.dumps(
            {
                "goal": "choose",
                "candidate_assessment": [
                    {"candidate_id": "P1", "assessment": "observed"},
                    {"candidate_id": "FAKE", "assessment": "invented"},
                ],
                "proposed_next_action": "search",
                "confidence": 0.8,
            }
        ),
    )
    monkeypatch.setattr("agent.v2.planner.generate", lambda **_: response)
    observations = ObservationStore()
    observations.observe("search", '{"product_id":"P1","quantity":3}', 1)
    plan = ModelPlanner().plan(
        DecisionWorkspace(current_instruction="choose"),
        model="test-model",
        llm_args={},
        observations=observations,
    )
    assert [item.candidate_id for item in plan.candidate_assessment] == ["P1"]
    assert any("FAKE" in warning for warning in plan.warnings)


@is_tool(ToolType.WRITE)
def create_order(
    store_id: str, product_ids: list[str], delivery_date: str
) -> str:
    """Create an order for observed products."""
    return "ok"


@is_tool(ToolType.WRITE)
def pay_order(order_id: str) -> str:
    """Pay an observed order."""
    return "ok"


def _kernel():
    observations = ObservationStore()
    observations.observe(
        "search",
        {
            "items": [
                {"store_id": "S1", "product_id": "P1", "quantity": 0},
                {"store_id": "S1", "product_id": "P2", "quantity": 3},
                {"store_id": "S2", "product_id": "P3", "quantity": 3},
            ]
        },
        1,
    )
    journal = OperationJournal()
    kernel = TransactionKernel(observations, journal)
    kernel.configure_tools([Tool(func=create_order), Tool(func=pay_order)])
    kernel.begin_instruction("请在2026-09-10送达", {})
    return kernel, journal


def test_transaction_kernel_enforces_snapshot_inventory_parent_date_and_idempotency():
    kernel, journal = _kernel()
    invalid = ToolCall(
        id="bad",
        name="create_order",
        arguments={
            "store_id": "S9",
            "product_ids": ["P1"],
            "delivery_date": "2026-09-11",
        },
    )
    result = kernel.validate(invalid, turn_id=1, commit=False)
    assert not result.valid
    assert any("not in the current candidate snapshot" in item for item in result.problems)
    assert any("zero inventory" in item for item in result.problems)
    assert any("explicit user date" in item for item in result.problems)

    mismatched_parent = ToolCall(
        id="mismatch",
        name="create_order",
        arguments={
            "store_id": "S2",
            "product_ids": ["P2"],
            "delivery_date": "2026-09-10",
        },
    )
    mismatch_result = kernel.validate(mismatched_parent, turn_id=1, commit=False)
    assert any("parent-child pair" in item for item in mismatch_result.problems)

    valid = ToolCall(
        id="good",
        name="create_order",
        arguments={
            "store_id": "S1",
            "product_ids": ["P2"],
            "delivery_date": "2026-09-10",
        },
    )
    accepted = kernel.validate(valid, turn_id=1, commit=True)
    assert accepted.valid
    assert journal.records[-1].status == "executing"
    duplicate = kernel.validate(valid, turn_id=1, commit=False)
    assert not duplicate.valid
    assert any("duplicate operation" in item for item in duplicate.problems)


def test_payment_is_bound_to_observed_order_and_later_turn():
    kernel, journal = _kernel()
    create = ToolCall(
        id="create",
        name="create_order",
        arguments={
            "store_id": "S1",
            "product_ids": ["P2"],
            "delivery_date": "2026-09-10",
        },
    )
    kernel.validate(create, turn_id=1, commit=True)
    kernel.observe_result(
        ToolMessage(
            id="create",
            name="create_order",
            role="tool",
            content='{"order_id":"O1","status":"unpaid"}',
        ),
        turn_id=1,
    )
    assert journal.records[-1].status == "succeeded"
    same_turn = kernel.validate(
        ToolCall(id="pay1", name="pay_order", arguments={"order_id": "O1"}),
        turn_id=1,
        commit=False,
    )
    assert not same_turn.valid
    later = kernel.validate(
        ToolCall(id="pay2", name="pay_order", arguments={"order_id": "O1"}),
        turn_id=2,
        commit=True,
    )
    assert later.valid
    assert journal.records[-1].authorization_evidence_turn == 2


def test_current_turn_explicit_date_supersedes_initial_instruction():
    kernel, _ = _kernel()
    kernel.observe_user_turn("改成2026-09-12送达", {})
    corrected = ToolCall(
        id="corrected",
        name="create_order",
        arguments={
            "store_id": "S1",
            "product_ids": ["P2"],
            "delivery_date": "2026-09-12",
        },
    )
    assert kernel.validate(corrected, turn_id=2, commit=False).valid


def test_agent_transaction_rejection_is_returned_to_model_for_correction(monkeypatch):
    responses = [
        AssistantMessage(
            role="assistant",
            tool_calls=[
                ToolCall(
                    id="bad-call",
                    name="create_order",
                    arguments={
                        "store_id": "S1",
                        "product_ids": ["P9"],
                        "delivery_date": "2026-09-10",
                    },
                )
            ],
        ),
        AssistantMessage(
            role="assistant",
            tool_calls=[
                ToolCall(
                    id="good-call",
                    name="create_order",
                    arguments={
                        "store_id": "S1",
                        "product_ids": ["P2"],
                        "delivery_date": "2026-09-10",
                    },
                )
            ],
        ),
    ]
    seen_requests = []

    def fake_generate(**kwargs):
        seen_requests.append(kwargs)
        return responses.pop(0)

    monkeypatch.setattr("agent.v2.executor.generate", fake_generate)
    agent = _agent(
        ADAPTV2,
        flags=V2FeatureFlags(transaction_enforcement=True),
    )
    agent.update_tools([Tool(func=create_order)])
    agent.set_current_instruction("请在2026-09-10送达")
    _ = agent.system_prompt  # synchronize the inherited instruction hook
    agent.observations.observe(
        "search",
        {"store_id": "S1", "product_id": "P2", "quantity": 2},
        1,
    )
    result, state = agent.generate_next_message(
        UserMessage(role="user", content="确认"), agent.get_init_state()
    )
    assert result.tool_calls[0].arguments["product_ids"] == ["P2"]
    assert len(seen_requests) == 2
    assert any(
        isinstance(item, ToolMessage) and item.error for item in state.messages
    )
    assert agent.operation_journal.records[-1].status == "executing"


def test_evaluator_retry_accepts_genuine_zero_and_tracks_attempts(monkeypatch):
    responses = [
        RewardInfo(reward=0.0, info={"note": "Evaluation error: HTTP 502"}),
        RewardInfo(reward=0.0, info={"note": "Evaluation error: timeout"}),
        RewardInfo(reward=0.0, info={"note": "genuine rubric failure"}),
    ]

    def fake_evaluate(self, subtask, result):
        return responses.pop(0)

    monkeypatch.setattr(PersonalizationOrchestrator, "_evaluate_subtask", fake_evaluate)
    orchestrator = IntegrityPersonalizationOrchestrator(
        task=SimpleNamespace(subtasks=[]),
        agent=object(),
        user=object(),
        evaluator_retries=2,
        evaluator_retry_backoff_seconds=0,
    )
    reward = orchestrator._evaluate_subtask(
        SimpleNamespace(subtask_id="s1"), {}
    )
    assert reward.reward == 0.0
    assert orchestrator.evaluation_records == [
        {
            "subtask_id": "s1",
            "evaluation_status": "ok",
            "evaluation_attempts": 3,
            "evaluator_error": None,
        }
    ]


def test_trace_metrics_excludes_evaluator_failure(tmp_path):
    path = tmp_path / "checkpoint.json"
    path.write_text(
        json.dumps(
            {
                "info": {"agent_kind": "stock", "cohort": "dev"},
                "simulations": [
                    {
                        "task_id": "good",
                        "trial": 0,
                        "seed": 42,
                        "evaluation_status": "ok",
                        "reward_info": {"reward": 1.0},
                        "messages": [],
                    },
                    {
                        "task_id": "bad-evaluator",
                        "trial": 0,
                        "seed": 42,
                        "evaluation_status": "evaluation_failed",
                        "reward_info": {"reward": 0.0},
                        "messages": [],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    metrics = summarize(path)
    assert metrics["avg_reward"] == 1.0
    assert metrics["num_scoreable"] == 1
    assert metrics["evaluation_failed"] == 1


def test_promotion_gate_requires_dev_blind_and_split_improvements():
    baseline = {
        "avg_reward": 0.30,
        "personalize_reward": 0.40,
        "proactive_reward": 0.10,
        "evaluation_failed": 0,
        "tool_errors": 1,
        "simulation_scores": {"a": 0.0, "b": 1.0},
    }
    candidate = {
        "avg_reward": 0.34,
        "personalize_reward": 0.40,
        "proactive_reward": 0.16,
        "evaluation_failed": 0,
        "tool_errors": 0,
        "simulation_scores": {"a": 1.0, "b": 1.0},
    }
    report = promotion_report(
        baseline,
        candidate,
        blind_baseline=baseline,
        blind_candidate=candidate,
    )
    assert report["promote"]
    assert all(report["checks"].values())


def test_v2_does_not_import_v1_policy_components():
    import agent.v2.agent as module

    source = inspect.getsource(module)
    for forbidden in (
        "TaskSpec",
        "DecisionCard",
        "CandidateRanker",
        "QuestionGate",
        "TaskRuntime",
    ):
        assert forbidden not in source

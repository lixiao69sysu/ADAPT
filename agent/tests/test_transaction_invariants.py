"""Transaction and irreversible-effect invariants."""

from __future__ import annotations

import pytest
from unittest.mock import patch

from agent.adapt_agent import ADAPTAgent
from agent.decision import DecisionCard, TaskSpec
from agent.memory.adapt_memory import ADAPTMemory
from agent.runtime import (
    ActionTransaction,
    OperationJournal,
    RuntimePhase,
    TaskRuntime,
    ToolEffect,
    ToolErrorLedger,
    ToolOutcomeNormalizer,
    ToolRegistry,
)
from vita.agent.llm_agent import LLMAgentState
from vita.data_model.message import (
    AssistantMessage,
    SystemMessage,
    ToolCall,
    UserMessage,
)


def test_prepare_copies_and_normalizes_without_mutating_model_proposal():
    agent = object.__new__(ADAPTAgent)
    agent.tool_registry = ToolRegistry()
    agent.tool_errors = ToolErrorLedger()
    agent.decision_card = DecisionCard()
    proposal = AssistantMessage(
        role="assistant",
        tool_calls=[
            ToolCall(
                id="s1",
                name="search_fictional_catalog",
                arguments={"keywords": ["violet", "violet", "glass"]},
            )
        ],
    )
    transaction = ActionTransaction.prepare(proposal, agent._prepare_tool_call)
    assert proposal.tool_calls[0].arguments["keywords"] == [
        "violet",
        "violet",
        "glass",
    ]
    assert transaction.prepared.tool_calls[0].arguments["keywords"] == [
        "violet",
        "glass",
    ]


def test_rejected_transaction_is_ephemeral_and_cannot_emit_or_commit():
    messages = []
    committed = []
    proposal = AssistantMessage(role="assistant", content="invalid")
    transaction = ActionTransaction.prepare(proposal)
    assert not transaction.validate(lambda _: ["synthetic rejection"])
    with pytest.raises(ValueError):
        transaction.emit(messages)
    with pytest.raises(ValueError):
        transaction.commit(lambda message: committed.append(message))
    assert messages == []
    assert committed == []


def test_agent_replan_does_not_persist_rejected_assistant_or_correction():
    agent = ADAPTAgent(
        tools=[],
        domain_policy="time={time}",
        memory=ADAPTMemory(language="chinese"),
        user_profile={"user_id": "transaction-test"},
        time="2026-08-26 12:00:00",
        language="chinese",
        enable_lessons=False,
    )
    agent.set_current_instruction("请帮我购买一个虚构对象")
    agent._replan_limit = 0
    state = LLMAgentState(
        system_messages=[SystemMessage(role="system", content=agent.system_prompt)],
        messages=[],
    )
    rejected = AssistantMessage(role="assistant", content="已下单。")
    with patch("agent.adapt_agent.generate", return_value=rejected):
        _, state = agent.generate_next_message(
            UserMessage(role="user", content="请执行"), state
        )
    contents = [message.content or "" for message in state.messages]
    assert all("已下单" not in content for content in contents)
    assert all("ADAPT preflight rejected" not in content for content in contents)


def test_emit_then_commit_happens_exactly_once():
    messages = []
    committed = []
    transaction = ActionTransaction.prepare(
        AssistantMessage(role="assistant", content="visible")
    )
    assert transaction.validate(lambda _: [])
    transaction.emit(messages)
    transaction.emit(messages)
    transaction.commit(lambda message: committed.append(message.content))
    transaction.commit(lambda message: committed.append("duplicate"))
    assert len(messages) == 1
    assert committed == ["visible"]


def test_operation_journal_covers_every_irreversible_role_and_failed_signature():
    for role in ("create", "pay", "cancel", "modify"):
        journal = OperationJournal()
        arguments = {"opaque_ref": "X-1"}
        journal.register("failed", role, f"{role}_opaque", arguments)
        journal.observe_result("failed", f"{role}_opaque", role, True)
        assert journal.validate(role, f"{role}_opaque", arguments)
        corrected = {"opaque_ref": "X-2"}
        assert journal.validate(role, f"{role}_opaque", corrected) == []
        journal.register("success", role, f"{role}_opaque", corrected)
        journal.observe_result("success", f"{role}_opaque", role, False)
        assert journal.validate(role)
        journal.begin_new_epoch()
        assert journal.validate(role) == []


def test_structured_tool_outcome_drives_create_and_pay_transitions():
    runtime = TaskRuntime.begin(TaskSpec.compile("请执行一个虚构交易"))
    create = ToolOutcomeNormalizer.normalize(
        tool_name="forge",
        tool_role="create",
        content={"workflow_id": "W-7", "status": "pending_payment"},
    )
    assert create.effect == ToolEffect.CREATED_PENDING_PAYMENT
    runtime.observe_tool_outcome("forge", create)
    assert runtime.phase == RuntimePhase.READY_TO_PAY

    pay = ToolOutcomeNormalizer.normalize(
        tool_name="settle",
        tool_role="pay",
        content={"workflow_id": "W-7", "status": "paid"},
    )
    assert pay.effect == ToolEffect.PAID
    runtime.observe_tool_outcome("settle", pay)
    assert runtime.phase == RuntimePhase.DONE


def test_tool_error_outcome_never_claims_an_effect():
    outcome = ToolOutcomeNormalizer.normalize(
        tool_name="forge",
        tool_role="create",
        content={"workflow_id": "W-bad", "status": "pending_payment"},
        error=True,
    )
    assert not outcome.ok
    assert outcome.effect == ToolEffect.NO_CHANGE

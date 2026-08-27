"""Regression gates for VitaBench-compatible ADAPT state rollback."""

from types import MethodType

from agent.adapt_agent import ADAPTAgent
from agent.decision import Candidate
from agent.memory.adapt_memory import ADAPTMemory
from agent.runtime import ADAPTAgentState
from vita.data_model.message import AssistantMessage, UserMessage


def _agent() -> ADAPTAgent:
    agent = ADAPTAgent(
        tools=[],
        domain_policy="time={time}",
        memory=ADAPTMemory(language="chinese"),
        user_profile={"user_id": "rollback-user"},
        time="2026-08-27 12:00:00",
        language="chinese",
        enable_lessons=False,
    )
    agent.set_current_instruction("帮我找一个对象")
    return agent


def test_init_state_contains_complete_framework_snapshot():
    agent = _agent()
    state = agent.get_init_state()

    assert isinstance(state, ADAPTAgentState)
    for required in ("runtime", "ledger", "operations", "lineage", "responses"):
        assert required in state.framework_state


def test_retry_restores_ledger_runtime_and_journal_from_caller_state():
    agent = _agent()
    original = agent.get_init_state()

    def fake_generate(self, message, state):
        assert "LEAK" not in self.ledger.candidates
        assert not self.operations.records()
        self.ledger.candidates["Q-1"] = Candidate(
            "Q-1", "quasar", "Blue Quasar", "", "search_quasar"
        )
        self.runtime.validation_failures += 1
        self.operations.register(
            "call-1", "create", "create_quasar", {"quasar_id": "Q-1"}
        )
        self.operations.observe_result("call-1", "create_quasar", "create", False)
        state.messages.append(AssistantMessage(role="assistant", content="ok"))
        return state.messages[-1], state

    agent._generate_next_message_impl = MethodType(fake_generate, agent)

    # Simulate process-local leakage from an invalid first response.
    agent.ledger.candidates["LEAK"] = Candidate(
        "LEAK", "quasar", "Leaked", "", "search_quasar"
    )
    first_message, first = agent.generate_next_message(
        UserMessage(role="user", content="go"), original
    )
    second_message, second = agent.generate_next_message(
        UserMessage(role="user", content="go"), original
    )

    assert first_message == second_message
    assert first.framework_state["runtime"].validation_failures == 1
    assert second.framework_state["runtime"].validation_failures == 1
    assert set(first.framework_state["ledger"].candidates) == {"Q-1"}
    assert set(second.framework_state["ledger"].candidates) == {"Q-1"}
    assert len(first.framework_state["operations"].records()) == 1
    assert len(second.framework_state["operations"].records()) == 1


def test_checkpoint_deepcopy_isolated_from_live_agent_mutation():
    agent = _agent()
    checkpoint = agent.get_init_state()
    agent.runtime.validation_failures = 7
    agent.ledger.candidates["LEAK"] = Candidate(
        "LEAK", "quasar", "Leaked", "", "search_quasar"
    )

    assert checkpoint.framework_state["runtime"].validation_failures == 0
    assert "LEAK" not in checkpoint.framework_state["ledger"].candidates

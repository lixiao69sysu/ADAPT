"""Counterexample gate for the proactive loop wiring (E-069).

The stock loop left three transitions open: nothing recorded that a suggested
question was actually sent, a reply never reached the question state, and a reply
was stored as its literal text. These tests pin the closed behaviour, plus the
invariant that matters most: the observer must be a pure observer. It may not
modify the model's message, block a tool call, or preempt a turn -- the failures
that made a controller net-negative (E-042, E-048, E-049).

Zero-model: no API call, no evaluator, no benchmark data.
"""

from __future__ import annotations

from types import SimpleNamespace

from agent.memory.adapt_memory import ADAPTMemory
from agent.memory.proactive import (
    PendingQuestion,
    Proposal,
    ProactiveEngine,
    QuestionContext,
    resolve_answer,
)
from agent.adapt_agent import AdaptAgent, asked_the_question


def _agent(enable_proactive_loop: bool = True) -> AdaptAgent:
    return AdaptAgent(
        tools=[],
        domain_policy="{time}",
        memory=ADAPTMemory(),
        user_profile={},
        llm=None,
        llm_args={},
        time="2026-03-01 10:00:00",
        language="chinese",
        enable_proactive_loop=enable_proactive_loop,
    )


def test_adapt_agent_defaults_to_a_pass_through(monkeypatch):
    """With the loop off, `--agent adapt` must behave exactly like stock.

    That is what makes the two arms comparable: the only remaining variable is
    the memory backend. The observers must not read or write memory at all.
    """
    from types import SimpleNamespace

    from vita.agent.personalization_agent import PersonalizationAgent

    calls: list = []

    def fake(self, message, state):  # noqa: ANN001
        calls.append(message)
        return "assistant", state

    monkeypatch.setattr(PersonalizationAgent, "generate_next_message", fake)

    agent = _agent(enable_proactive_loop=False)
    agent.set_current_instruction("帮我买张出行票")
    memory = agent.memory
    facts_before = len(memory.facts)
    budget_before = memory.proactive.asked_this_subtask

    out, _state = agent.generate_next_message(
        SimpleNamespace(role="user", content="坐高铁吧"), None
    )

    assert out == "assistant" and len(calls) == 1
    assert len(memory.facts) == facts_before, "the pass-through must not write facts"
    assert memory.proactive.asked_this_subtask == budget_before
    assert memory.proactive.pending is None
    assert agent.loop_events["enabled"] is False
    assert agent.questions_committed == 0
    assert agent.answers_linked == 0


def test_adapt_agent_loop_is_off_unless_asked_for():
    assert _agent(enable_proactive_loop=False).enable_proactive_loop is False
    assert _agent(enable_proactive_loop=True).enable_proactive_loop is True


# ---------------------------------------------------------------------------
# Answer resolution: the reply must become a slot value
# ---------------------------------------------------------------------------


def test_affirmative_resolves_to_the_confirmed_value_not_the_word_yes():
    pending = PendingQuestion(
        question="您之前提到无糖，这次还是这样吗？",
        slot="sweetness",
        value="无糖",
        is_confirmation=True,
    )
    for reply in ("是的", "对", "嗯，还是一样", "好"):
        assert resolve_answer(pending, reply) == ("sweetness", "无糖")


def test_negative_to_a_confirmation_yields_no_value():
    pending = PendingQuestion(
        question="您之前提到无糖，这次还是这样吗？",
        slot="sweetness",
        value="无糖",
        is_confirmation=True,
    )
    for reply in ("不是", "不用了", "不要"):
        assert resolve_answer(pending, reply) == ("sweetness", "")


def test_negative_is_checked_before_affirmative():
    """'不是' contains '是'; the negative reading must win."""
    pending = PendingQuestion(
        question="还是这样吗？", slot="sweetness", value="无糖", is_confirmation=True
    )
    assert resolve_answer(pending, "不是") == ("sweetness", "")


def test_open_question_takes_the_reply_as_the_value():
    pending = PendingQuestion(question="您希望什么时间呢？", slot="time")
    assert resolve_answer(pending, "下午三点") == ("time", "下午三点")


def test_bare_token_to_an_open_question_yields_no_value():
    pending = PendingQuestion(question="您希望什么时间呢？", slot="time")
    assert resolve_answer(pending, "不用了") == ("time", "")


def test_no_pending_question_resolves_to_nothing():
    assert resolve_answer(None, "是的") == ("", "")


def test_delegation_is_not_a_value():
    """Observed in the first proactive smoke (E-069).

    "随便，你看着办吧。" answered "您想用哪种方式？" and was stored as the value of
    the ``transport`` slot. Handing the decision back is a state change, not
    evidence about the user.
    """
    pending = PendingQuestion(question="这次出行您想用哪种方式？", slot="transport")
    for reply in ("随便", "随便，你看着办吧。", "都行", "听你的", "无所谓", "你决定吧"):
        assert resolve_answer(pending, reply) == ("transport", ""), reply


def test_delegation_mixed_with_a_value_keeps_the_value():
    """Counterexample: '随便，就二等座吧' does state something."""
    pending = PendingQuestion(question="您想选哪个座位类型？", slot="seat")
    slot, value = resolve_answer(pending, "随便，就二等座吧")
    assert slot == "seat"
    assert value, "a reply that names a real option must not be discarded"


def test_delegation_does_not_close_a_confirmation_either():
    pending = PendingQuestion(
        question="您之前提到无糖，这次还是这样吗？",
        slot="sweetness",
        value="无糖",
        is_confirmation=True,
    )
    assert resolve_answer(pending, "随便吧") == ("sweetness", "")


def test_delegation_creates_no_fact_in_memory():
    memory = ADAPTMemory()
    memory.begin_subtask("想去昆明玩，你帮我定个这周六的票吧。")
    memory.commit_question("这次出行您想用哪种方式？", slot="transport")
    memory.record_user_answer("随便，你看着办吧。")
    assert len(memory.facts) == 0


# ---------------------------------------------------------------------------
# Memory layer: value, not reply text
# ---------------------------------------------------------------------------


def test_memory_stores_the_resolved_value_and_dimension():
    memory = ADAPTMemory()
    memory.begin_subtask("帮我点杯奶茶")
    memory.commit_question(
        "您之前提到无糖，这次还是这样吗？",
        slot="sweetness",
        value="无糖",
        is_confirmation=True,
    )
    assert memory.record_user_answer("是的") is True
    stored = [(fact.value, fact.dimension) for fact in memory.facts]
    assert ("无糖", "sweetness") in stored
    assert not any(value == "是的" for value, _ in stored)


def test_memory_invents_nothing_for_an_unresolvable_reply():
    memory = ADAPTMemory()
    memory.begin_subtask("帮我点杯奶茶")
    memory.commit_question("您希望什么时间呢？", slot="time")
    memory.record_user_answer("不用了")
    assert len(memory.facts) == 0


def test_answer_without_a_pending_question_is_refused():
    memory = ADAPTMemory()
    memory.begin_subtask("帮我订个酒店")
    assert memory.record_user_answer("帮我订个酒店") is False
    assert len(memory.facts) == 0


def test_proposal_carries_the_slot():
    proposal = ADAPTMemory().propose("帮我预约理发")
    assert isinstance(proposal, Proposal)
    assert proposal.slot == "time"


def test_propose_question_stays_pure_and_does_not_spend_budget():
    memory = ADAPTMemory()
    memory.begin_subtask("帮我订个酒店")
    memory.propose_question("帮我订个酒店")
    memory.propose_question("帮我订个酒店")
    assert memory.proactive.asked_this_subtask == 0
    assert memory.proactive.pending is None


# ---------------------------------------------------------------------------
# Did the model actually ask it?
# ---------------------------------------------------------------------------


_PROPOSED = "这次出行您想用哪种方式？比如高铁、飞机或汽车。"


def test_verbatim_and_paraphrases_count_as_asking():
    assert asked_the_question(_PROPOSED, _PROPOSED, slot="transport")
    assert asked_the_question(
        _PROPOSED, "这次出行您想用哪种方式呢？高铁、飞机还是汽车？", slot="transport"
    )
    assert asked_the_question(
        _PROPOSED, "请问您想坐高铁还是飞机出行？", slot="transport"
    )


def test_merely_mentioning_the_topic_is_not_asking():
    assert not asked_the_question(_PROPOSED, "好的，我帮您查一下高铁票。", slot="transport")
    assert not asked_the_question(
        _PROPOSED, "高铁和飞机都可以，我看看。", slot="transport"
    )


def test_another_slots_question_is_not_this_question():
    assert not asked_the_question(_PROPOSED, "您想在哪个城市呢？", slot="transport")


def test_empty_message_is_not_asking():
    assert not asked_the_question(_PROPOSED, "", slot="transport")


# ---------------------------------------------------------------------------
# The observer closes the loop
# ---------------------------------------------------------------------------


def test_outbound_question_is_committed_only_when_asked():
    agent = _agent()
    agent.set_current_instruction("帮我买张出行票")

    agent._observe_outbound(SimpleNamespace(content="好的，我先帮您看看。"))
    assert agent.memory.proactive.asked_this_subtask == 0

    agent._observe_outbound(
        SimpleNamespace(content="请问您想坐高铁还是飞机出行？")
    )
    assert agent.memory.proactive.asked_this_subtask == 1
    assert agent.memory.proactive.pending.slot == "transport"


def test_inbound_reply_is_linked_and_resolved():
    agent = _agent()
    agent.set_current_instruction("帮我订个酒店")
    agent.memory.commit_question(
        "您想在哪个城市呢？", slot="city"
    )
    agent._observe_inbound(SimpleNamespace(role="user", content="贵阳"))
    assert agent.answers_linked == 1
    assert any(fact.value == "贵阳" for fact in agent.memory.facts)


def test_instruction_turn_is_not_treated_as_an_answer():
    """The first user turn is the task, not a reply to anything."""
    agent = _agent()
    agent.set_current_instruction("帮我订个酒店")
    agent._observe_inbound(SimpleNamespace(role="user", content="帮我订个酒店"))
    assert agent.answers_linked == 0
    assert len(agent.memory.facts) == 0


def test_observer_never_modifies_the_model_message():
    """The whole point: observation, not control."""
    agent = _agent()
    agent.set_current_instruction("帮我买张出行票")
    original = "请问您想坐高铁还是飞机出行？"
    message = SimpleNamespace(content=original)
    before = message.content
    agent._observe_outbound(message)
    assert message.content == before == original


def test_observer_never_touches_tool_messages():
    agent = _agent()
    agent.set_current_instruction("帮我买张出行票")
    tool_message = SimpleNamespace(role="tool", content="StoreProduct(id=S1_P1)")
    agent._observe_inbound(tool_message)
    assert agent.answers_linked == 0
    assert len(agent.memory.facts) == 0


def test_loop_events_are_exposed_for_attribution():
    agent = _agent()
    assert agent.loop_events == {
        "enabled": True,
        "questions_committed": 0,
        "answers_linked": 0,
        "answers_resolved_to_a_value": 0,
        "candidate_evidence_enabled": False,
        "tool_results_annotated": 0,
        "candidate_constraint_pairs_resolved": 0,
        "task_state_enabled": False,
        "task_states_annotated": 0,
    }


# ---------------------------------------------------------------------------
# The engine still refuses to spend budget without a sent question
# ---------------------------------------------------------------------------


def test_engine_budget_unchanged_by_proposing():
    engine = ProactiveEngine(max_questions=1)
    context = QuestionContext(unknown_slots=("time",))
    assert engine.propose(context=context) is not None
    engine.asked_this_subtask == 0
    engine.commit_question("您希望什么时间呢？", slot="time")
    assert engine.propose(context=context) is None


# ---------------------------------------------------------------------------
# End to end: ask -> link -> close the gap -> do not re-ask
# ---------------------------------------------------------------------------


def _context_for(instruction: str, memory: ADAPTMemory) -> QuestionContext:
    from agent.decision import TaskSpec as _TaskSpec
    from agent.memory.slots import resolve_preference_slots

    spec = _TaskSpec.compile(instruction)
    return QuestionContext(
        instruction=instruction,
        domain=spec.domain,
        facet=spec.facet,
        action=spec.action,
        unknown_slots=tuple(spec.unknown_slots or ()),
        known_slots=dict(resolve_preference_slots(spec, memory.facts) or {}),
    )


def test_a_recorded_answer_closes_the_gap_and_stops_the_reask():
    """The whole loop, verified without a model.

    This is the "更新本轮状态 -> 重新选择" step: the stored answer must feed back
    into the gap test, otherwise the agent asks the same thing forever.
    """
    instruction = "想去昆明玩，你帮我定个这周六的票吧。"
    memory = ADAPTMemory()
    memory.begin_subtask(instruction)

    proposal = memory.propose(instruction)
    assert proposal is not None and proposal.slot == "transport"

    memory.commit_question(
        proposal.question,
        slot=proposal.slot,
        value=proposal.value,
        is_confirmation=proposal.is_confirmation,
    )
    memory.record_user_answer("坐高铁吧")

    assert memory.facts, "the answer must reach the fact store"
    context = _context_for(instruction, memory)
    assert "transport" not in ProactiveEngine().open_gaps(context)
    assert ProactiveEngine().propose(context=context) is None


def test_a_delegated_answer_leaves_the_gap_open():
    """Counterexample: delegation must not close the gap as if it were a value."""
    instruction = "想去昆明玩，你帮我定个这周六的票吧。"
    memory = ADAPTMemory()
    memory.begin_subtask(instruction)
    proposal = memory.propose(instruction)
    memory.commit_question(proposal.question, slot=proposal.slot)
    memory.record_user_answer("随便，你看着办吧。")

    assert not memory.facts
    context = _context_for(instruction, memory)
    assert "transport" in ProactiveEngine().open_gaps(context)

"""Counterexample gate for the candidate-evidence mechanism (E-087).

A **second** mechanism lives behind ``enable_candidate_evidence`` (default off).
When a tool result arrives it reports, per candidate, which of the current
instruction's constraints are satisfied / violated / unknown -- three-valued,
with polarity carried by the constraint, so a prohibition is never read as a
positive.

The invariants these tests pin, in the order the task states them:

1. with the switch off the class is the verified pass-through and nothing is
   annotated;
2. a printed conflict is reported and an omitted attribute is not;
3. the environment's own message object is never modified (byte-identical);
4. no candidates or no constraints -> nothing appended;
5. the appended text respects its character bound.

Zero-model: no API call, no evaluator, no benchmark data. Candidate records are
synthetic strings in the same textual shape the vendored tools print.
"""

from __future__ import annotations

from vita.data_model.message import MultiToolMessage, ToolMessage

from agent.adapt_agent import AdaptAgent, _MAX_EVIDENCE_CHARS
from agent.memory.adapt_memory import ADAPTMemory

# The constraint is compiled by the real compiler, not hand-built: this
# instruction yields a hard ``forbid`` for 花生 plus a hard ``require`` for 奶茶.
INSTRUCTION = "帮我点杯奶茶，不要花生"

# The exact record shape the vendored delivery tools print. The first candidate
# prints 花生碎, which extends the forbidden 花生 and is therefore a violation;
# the second merely prints a different topping and is honestly unknown.
CONFLICTING = (
    "StoreProduct(store_name=Shop, store_id=S1_S00001, product_name=Tea, "
    "product_id=S1_P00001, attributes=topping:花生碎, quantity=8, price=19.9, "
    "tags=['奶茶'])"
)
CLEAN = (
    "StoreProduct(store_name=Shop, store_id=S1_S00002, product_name=Tea, "
    "product_id=S1_P00002, attributes=topping:椰果, quantity=8, price=19.9, "
    "tags=['奶茶'])"
)
NO_CANDIDATES = "No products found for this keyword."

# An instruction that compiles to no constraints at all (verified below).
NO_CONSTRAINT_INSTRUCTION = "推荐几家电玩城吧，周末想去玩"


def _agent(
    *, candidate_evidence: bool = True, proactive_loop: bool = False
) -> AdaptAgent:
    agent = AdaptAgent(
        tools=[],
        domain_policy="{time}",
        memory=ADAPTMemory(),
        user_profile={},
        llm=None,
        llm_args={},
        time="2026-03-01 10:00:00",
        language="chinese",
        enable_proactive_loop=proactive_loop,
        enable_candidate_evidence=candidate_evidence,
    )
    agent.set_current_instruction(INSTRUCTION)
    return agent


def _tool_message(content: str, message_id: str = "t1") -> ToolMessage:
    return ToolMessage(
        id=message_id,
        name="delivery_product_search_recommand",
        role="tool",
        content=content,
    )


def _appended(content: str, original: str) -> str:
    assert content.startswith(original), "the environment's text must not be re-rendered"
    return content[len(original) :]


def test_the_probe_instruction_really_compiles_to_a_prohibition():
    """Heuristic drift guard: the assertions below depend on a real forbid."""
    from agent.decision import TaskSpec
    from agent.runtime.correspondence import ConstraintPolarity, constraints_from_card

    compiled = constraints_from_card(ADAPTMemory().compile_task(INSTRUCTION))
    assert any(
        item.polarity == ConstraintPolarity.FORBID and item.value == "花生"
        for item in compiled
    ), compiled
    assert TaskSpec.compile(INSTRUCTION).action == "commit"
    assert constraints_from_card(
        ADAPTMemory().compile_task(NO_CONSTRAINT_INSTRUCTION)
    ) == []


# ---------------------------------------------------------------------------
# 1. off by default, and off means pass-through
# ---------------------------------------------------------------------------


def test_candidate_evidence_is_off_unless_asked_for():
    assert _agent(candidate_evidence=False).enable_candidate_evidence is False
    assert _agent(candidate_evidence=True).enable_candidate_evidence is True


def test_switch_off_annotates_nothing_and_stays_a_pass_through(monkeypatch):
    """With the switch off nothing is read, written or appended."""
    from vita.agent.personalization_agent import PersonalizationAgent

    calls: list = []

    def fake(self, message, state):  # noqa: ANN001
        calls.append(message)
        return "assistant", state

    monkeypatch.setattr(PersonalizationAgent, "generate_next_message", fake)

    agent = _agent(candidate_evidence=False)
    message = _tool_message(CONFLICTING)
    out, _state = agent.generate_next_message(message, None)

    assert out == "assistant" and len(calls) == 1
    assert calls[0] is message, "the pass-through must forward the same object"
    assert message.content == CONFLICTING
    assert agent.tool_results_annotated == 0
    assert agent.candidate_constraint_pairs_resolved == 0
    assert agent.loop_events["candidate_evidence_enabled"] is False


def test_adding_the_second_switch_leaves_the_proactive_path_untouched(monkeypatch):
    """Proactive-loop-only behaviour must be bit-for-bit what it was."""
    from vita.agent.personalization_agent import PersonalizationAgent

    seen: list = []

    def fake(self, message, state):  # noqa: ANN001
        seen.append(message)
        return "assistant", state

    monkeypatch.setattr(PersonalizationAgent, "generate_next_message", fake)

    agent = _agent(candidate_evidence=False, proactive_loop=True)
    message = _tool_message(CONFLICTING)
    agent.generate_next_message(message, None)

    assert seen[0] is message
    assert message.content == CONFLICTING
    assert agent.tool_results_annotated == 0


# ---------------------------------------------------------------------------
# 2. conflict vs. omission
# ---------------------------------------------------------------------------


def test_a_printed_forbidden_attribute_is_reported_as_a_conflict():
    agent = _agent()
    original = _tool_message(CONFLICTING)
    annotated = agent._annotate_tool_evidence(original)

    assert annotated is not original
    appended = _appended(annotated.content, CONFLICTING)
    assert "忌花生=冲突" in appended
    assert "花生碎" in appended
    assert agent.tool_results_annotated == 1
    assert agent.candidate_constraint_pairs_resolved > 0


def test_an_omitted_attribute_is_unknown_and_never_a_conflict():
    """Absence of evidence is not evidence of absence (the module's core rule)."""
    agent = _agent()
    original = _tool_message(CLEAN)
    annotated = agent._annotate_tool_evidence(original)

    appended = _appended(annotated.content, CLEAN)
    assert "忌花生=未知" in appended
    assert "冲突" not in appended


# ---------------------------------------------------------------------------
# 3. the environment's message is never modified
# ---------------------------------------------------------------------------


def test_the_original_message_is_byte_identical_after_annotation():
    agent = _agent()
    message = _tool_message(CONFLICTING)
    before = message.content

    annotated = agent._annotate_tool_evidence(message)

    assert annotated is not message
    assert message.content == before == CONFLICTING
    assert annotated.content != before


def test_the_model_sees_the_block_while_the_trajectory_stays_clean(monkeypatch):
    """The whole point of the deepcopy: append to the copy, not to the record."""
    from vita.agent.personalization_agent import PersonalizationAgent

    seen: list = []

    def fake(self, message, state):  # noqa: ANN001
        seen.append(message)
        return "assistant", state

    monkeypatch.setattr(PersonalizationAgent, "generate_next_message", fake)

    agent = _agent()
    message = _tool_message(CONFLICTING)
    agent.generate_next_message(message, None)

    assert len(seen) == 1
    assert "忌花生=冲突" in seen[0].content
    assert message.content == CONFLICTING, "the saved trajectory must be untouched"


def test_both_delivery_shapes_are_annotated():
    """A single call arrives bare; only a multi-call round is wrapped (E-053)."""
    agent = _agent()
    bare = _tool_message(CONFLICTING)
    assert agent._annotate_tool_evidence(bare) is not bare

    other = _agent()
    multi = MultiToolMessage(
        role="tool",
        tool_messages=[_tool_message(CONFLICTING, "t1"), _tool_message(CLEAN, "t2")],
    )
    annotated = other._annotate_tool_evidence(multi)

    assert annotated is not multi
    assert other.tool_results_annotated == 2
    assert multi.tool_messages[0].content == CONFLICTING
    assert multi.tool_messages[1].content == CLEAN
    assert "忌花生=冲突" in annotated.tool_messages[0].content
    assert "忌花生=未知" in annotated.tool_messages[1].content


# ---------------------------------------------------------------------------
# 4. nothing to report -> nothing appended
# ---------------------------------------------------------------------------


def test_a_result_with_no_parseable_candidate_appends_nothing():
    agent = _agent()
    message = _tool_message(NO_CANDIDATES)
    assert agent._annotate_tool_evidence(message) is message
    assert agent.tool_results_annotated == 0
    assert agent.candidate_constraint_pairs_resolved == 0


def test_an_instruction_with_no_constraints_appends_nothing():
    agent = _agent()
    agent.set_current_instruction(NO_CONSTRAINT_INSTRUCTION)
    message = _tool_message(CONFLICTING)
    assert agent._annotate_tool_evidence(message) is message
    assert agent.tool_results_annotated == 0


def test_a_non_tool_message_is_never_annotated():
    from types import SimpleNamespace

    agent = _agent()
    user_message = SimpleNamespace(role="user", content="就来一杯奶茶吧")
    assert agent._annotate_tool_evidence(user_message) is user_message


def test_a_backend_without_a_compiler_goes_inert_rather_than_guessing():
    """No instruction compiler -> no constraints -> no observation, not a guess."""
    agent = AdaptAgent(
        tools=[],
        domain_policy="{time}",
        memory=object(),
        user_profile={},
        llm=None,
        llm_args={},
        time="2026-03-01 10:00:00",
        language="chinese",
        enable_candidate_evidence=True,
    )
    agent.set_current_instruction(INSTRUCTION)
    message = _tool_message(CONFLICTING)
    assert agent._annotate_tool_evidence(message) is message
    assert agent.tool_results_annotated == 0


# ---------------------------------------------------------------------------
# 5. the bound
# ---------------------------------------------------------------------------


def test_the_appended_block_respects_the_character_bound():
    agent = _agent()
    rows = "\n".join(
        "StoreProduct(store_name=Shop, store_id=S1_S{i:05d}, product_name=Tea, "
        "product_id=S1_P{i:05d}, attributes=topping:花生碎, quantity=8, "
        "price=19.9, tags=['奶茶'])".format(i=index)
        for index in range(40)
    )
    message = _tool_message(rows)
    annotated = agent._annotate_tool_evidence(message)

    appended = _appended(annotated.content, rows)
    assert appended, "a long result with real conflicts must still be annotated"
    assert len(appended) <= _MAX_EVIDENCE_CHARS


def test_the_bound_is_a_named_constant():
    assert isinstance(_MAX_EVIDENCE_CHARS, int)
    assert 0 < _MAX_EVIDENCE_CHARS <= 2000


# ---------------------------------------------------------------------------
# bookkeeping is exposed and never breaks a run
# ---------------------------------------------------------------------------


def test_annotation_failure_is_swallowed(monkeypatch):
    """Bookkeeping must never be able to fail a run."""
    agent = _agent()
    monkeypatch.setattr(
        AdaptAgent,
        "_current_constraints",
        lambda self: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    message = _tool_message(CONFLICTING)
    assert agent._annotate_tool_evidence(message) is message


def test_loop_events_report_the_second_mechanism():
    agent = _agent()
    events = agent.loop_events
    assert events["candidate_evidence_enabled"] is True
    assert events["tool_results_annotated"] == 0
    assert events["candidate_constraint_pairs_resolved"] == 0


# ---------------------------------------------------------------------------
# runner threading: the flags reach the agent and the checkpoint
# ---------------------------------------------------------------------------


def test_run_stock_builds_the_adapt_agent_with_both_switches(monkeypatch):
    """The runner's parameter chain must forward both switches, not drop one."""
    from types import SimpleNamespace

    import agent.vitabench_runner as runner

    captured: dict = {}

    class FakeAgent:
        loop_events = {"enabled": False}

        def __init__(self, **kwargs):
            captured.update(kwargs)

    class FakeOrchestrator:
        def __init__(self, **kwargs):
            pass

        def run(self):
            return SimpleNamespace(states={})

    monkeypatch.setattr(runner, "AdaptAgent", FakeAgent)
    monkeypatch.setattr(runner, "ADAPTMemory", lambda **kwargs: object())
    monkeypatch.setattr(runner, "PersonalizationUser", lambda **kwargs: object())
    monkeypatch.setattr(
        runner,
        "get_prompts",
        lambda language: SimpleNamespace(personalization_agent_system_prompt="{time}"),
    )
    monkeypatch.setattr(
        runner, "IntegrityPersonalizationOrchestrator", FakeOrchestrator
    )

    task = SimpleNamespace(
        id="U1",
        user_profile={"user_id": "U1"},
        subtasks=[SimpleNamespace(environment={"time": "2026-01-01 00:00:00"})],
    )
    runner.run_stock_personalization_task(
        task,
        agent_kind="adapt",
        llm_agent="agent-model",
        llm_user="user-model",
        memory_type="adapt",
        agent_context_guard=False,
        enable_proactive_loop=True,
        enable_candidate_evidence=True,
    )

    assert captured["enable_proactive_loop"] is True
    assert captured["enable_candidate_evidence"] is True


def test_run_stock_defaults_both_switches_off(monkeypatch):
    from types import SimpleNamespace

    import agent.vitabench_runner as runner

    captured: dict = {}

    class FakeAgent:
        loop_events = {"enabled": False}

        def __init__(self, **kwargs):
            captured.update(kwargs)

    class FakeOrchestrator:
        def __init__(self, **kwargs):
            pass

        def run(self):
            return SimpleNamespace(states={})

    monkeypatch.setattr(runner, "AdaptAgent", FakeAgent)
    monkeypatch.setattr(runner, "ADAPTMemory", lambda **kwargs: object())
    monkeypatch.setattr(runner, "PersonalizationUser", lambda **kwargs: object())
    monkeypatch.setattr(
        runner,
        "get_prompts",
        lambda language: SimpleNamespace(personalization_agent_system_prompt="{time}"),
    )
    monkeypatch.setattr(
        runner, "IntegrityPersonalizationOrchestrator", FakeOrchestrator
    )

    task = SimpleNamespace(
        id="U1",
        user_profile={"user_id": "U1"},
        subtasks=[SimpleNamespace(environment={"time": "2026-01-01 00:00:00"})],
    )
    runner.run_stock_personalization_task(
        task,
        agent_kind="adapt",
        llm_agent="agent-model",
        llm_user="user-model",
        memory_type="adapt",
        agent_context_guard=False,
    )

    assert captured["enable_proactive_loop"] is False
    assert captured["enable_candidate_evidence"] is False


def test_the_second_hop_forwards_both_switches(monkeypatch):
    import agent.vitabench_runner as runner

    captured: dict = {}

    def fake_run_stock(task, **kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(runner, "run_stock_personalization_task", fake_run_stock)
    runner._run_one_simulation(
        object(),
        agent_kind="adapt",
        llm_agent="agent-model",
        llm_user="user-model",
        llm_evaluator=None,
        evaluator_args=None,
        max_steps=1,
        seed=1,
        language="chinese",
        evaluator_retries=0,
        evaluator_retry_backoff_seconds=0.0,
        memory_type="adapt",
        debug_path=None,
        agent_context_guard=False,
        enable_proactive_loop=True,
        enable_candidate_evidence=True,
    )

    assert captured["enable_proactive_loop"] is True
    assert captured["enable_candidate_evidence"] is True


def test_run_selected_records_both_switches_in_the_checkpoint(monkeypatch, tmp_path):
    import agent.vitabench_runner as runner

    monkeypatch.setattr(runner, "get_tasks", lambda language: [])
    checkpoint = runner.run_selected(
        agent_kind="adapt",
        cohort="dev",
        task_ids=None,
        subtask_ids=None,
        save_to=tmp_path / "checkpoint.json",
        llm_agent="agent-model",
        llm_user="user-model",
        llm_evaluator=None,
        max_steps=1,
        seed=1,
        num_trials=1,
        language="chinese",
        memory_type="adapt",
        enable_proactive_loop=True,
        enable_candidate_evidence=True,
    )

    assert checkpoint["info"]["adapt_agent"] == {
        "proactive_loop": True,
        "candidate_evidence": True,
        "task_state": False,
    }

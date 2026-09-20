"""Counterexample gate for the task-state mechanism (E-091).

A **third** mechanism lives behind ``enable_task_state`` (default off). E-089
attributed 108 of the 283 failing baseline runs (38.2%) to two adjacent
mechanisms with one root cause -- ``planning_defect`` (66 runs: searched,
presented options, never attempted a write) and ``over_asking`` (42 runs: asked
while every required slot was already settled). The agent carries no
representation of what this subtask still needs and what it already has.

When a tool result arrives the mechanism appends one bounded, factual line
stating the instruction's required slots and their settled/open state, how many
candidates the tool results have actually printed, and whether a write or a
question has happened yet.

The invariants these tests pin, in the order the task states them:

1. with the switch off the class is the verified pass-through and nothing is
   annotated;
2. the block names the required slots and their state, consistent with
   ``TaskSpec.compile``;
3. the block contains **no imperative and no recommendation** -- asserted against
   a forbidden-verb list so a future edit cannot quietly turn it into a
   directive;
4. the environment's own message object is never modified (byte-identical);
5. the appended text respects its named character bound;
6. "write attempted" appears only after a commit call was actually observed;
7. a result with nothing to report appends nothing;
8. the runner forwards the flag and records it in the checkpoint ``info``.

Zero-model: no API call, no evaluator, no benchmark data. Candidate records are
synthetic strings in the same textual shape the vendored tools print.
"""

from __future__ import annotations

from types import SimpleNamespace

from vita.data_model.message import ToolMessage

from agent.adapt_agent import (
    AdaptAgent,
    _MAX_TASK_STATE_CHARS,
    render_task_state_block,
    task_state_slots,
)
from agent.decision import TaskSpec
from agent.memory.adapt_memory import ADAPTMemory

# The compiler turns this into required_slots ["product", "address"], of which
# only "address" is unstated (verified by the drift guard below).
INSTRUCTION = "帮我点杯奶茶"

# The exact record shape the vendored delivery tools print: one store row and
# one product row.
CANDIDATES = (
    "StoreProduct(store_name=Shop, store_id=S1_S00001, product_name=Tea, "
    "product_id=S1_P00001, attributes=topping:椰果, quantity=8, price=19.9, "
    "tags=['奶茶'])"
)
NO_CANDIDATES = "No products found for this keyword."

# An instruction the compiler finds no requirement in and that is not a commit
# action, so it declares no required slots at all.
NO_REQUIREMENT_INSTRUCTION = "推荐几家电玩城吧，周末想去玩"

# The words that would make the block a directive. The repository's retired
# controller (E-042, E-048, E-049, E-050) is exactly what these forbid.
FORBIDDEN_WORDS = (
    "请",
    "建议",
    "应该",
    "必须",
    "需要您",
    "现在可以",
    "you should",
    "should",
    "must",
)


def _agent(*, task_state: bool = True, proactive_loop: bool = False) -> AdaptAgent:
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
        enable_task_state=task_state,
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


def test_the_probe_instruction_really_declares_slots():
    """Heuristic drift guard: the assertions below depend on a real slot set."""
    spec = TaskSpec.compile(INSTRUCTION)
    assert spec.action == "commit"
    assert spec.required_slots == ["product", "address"]
    assert spec.unknown_slots == ["address"]
    assert TaskSpec.compile(NO_REQUIREMENT_INSTRUCTION).required_slots == []


# ---------------------------------------------------------------------------
# 1. off by default, and off means pass-through
# ---------------------------------------------------------------------------


def test_task_state_is_off_unless_asked_for():
    assert _agent(task_state=False).enable_task_state is False
    assert _agent(task_state=True).enable_task_state is True


def test_switch_off_annotates_nothing_and_stays_a_pass_through(monkeypatch):
    """With the switch off nothing is read, written or appended."""
    from vita.agent.personalization_agent import PersonalizationAgent

    calls: list = []

    def fake(self, message, state):  # noqa: ANN001
        calls.append(message)
        return "assistant", state

    monkeypatch.setattr(PersonalizationAgent, "generate_next_message", fake)

    agent = _agent(task_state=False)
    message = _tool_message(CANDIDATES)
    out, _state = agent.generate_next_message(message, None)

    assert out == "assistant" and len(calls) == 1
    assert calls[0] is message, "the pass-through must forward the same object"
    assert message.content == CANDIDATES
    assert agent.task_states_annotated == 0
    assert agent.write_attempted is False
    assert agent.loop_events["task_state_enabled"] is False


def test_the_third_switch_leaves_the_other_two_off(monkeypatch):
    """Adding the third switch must not quietly enable the first two."""
    from vita.agent.personalization_agent import PersonalizationAgent

    seen: list = []

    def fake(self, message, state):  # noqa: ANN001
        seen.append(message)
        return "assistant", state

    monkeypatch.setattr(PersonalizationAgent, "generate_next_message", fake)

    agent = _agent(task_state=True)
    message = _tool_message(CANDIDATES)
    agent.generate_next_message(message, None)

    assert seen[0] is not message, "the task-state copy reaches the model"
    assert message.content == CANDIDATES
    assert agent.tool_results_annotated == 0, "evidence stays off"
    assert agent.questions_committed == 0, "the proactive loop stays off"


# ---------------------------------------------------------------------------
# 2. the block reports the compiler's own slots
# ---------------------------------------------------------------------------


def test_the_block_is_consistent_with_the_compiler():
    """Every required slot appears, with the state the compiler implies."""
    spec = TaskSpec.compile(INSTRUCTION)
    slots = task_state_slots(INSTRUCTION)
    assert [slot for slot, _settled in slots] == list(spec.required_slots)
    for slot, settled in slots:
        expected = (slot not in spec.unknown_slots) or bool(
            spec.resolved_slots.get(slot)
        )
        assert settled is expected, slot

    agent = _agent()
    annotated = agent._annotate_task_state(_tool_message(NO_CANDIDATES))
    appended = _appended(annotated.content, NO_CANDIDATES)

    assert "【本任务状态】" in appended
    assert "product=已定" in appended, appended
    assert "address=待定" in appended, appended
    assert agent.task_states_annotated == 1


def test_a_slot_resolved_from_structured_memory_is_reported_as_settled():
    """Memory-supplied slots are settled without any new lexicon."""
    assert dict(task_state_slots("帮我订个酒店")) == {
        "city": False,
        "date": False,
        "room_type": False,
    }
    resolved = dict(task_state_slots("帮我订个酒店", {"room_type": "大床房"}))
    assert resolved["room_type"] is True
    assert resolved["city"] is False


def test_the_block_counts_the_ids_the_tool_results_actually_printed():
    agent = _agent()
    annotated = agent._annotate_task_state(_tool_message(CANDIDATES))
    appended = _appended(annotated.content, CANDIDATES)

    assert "已观察候选：1 商家 / 1 商品" in appended, appended
    assert "已发起写入：否" in appended
    assert "已提问：否" in appended


# ---------------------------------------------------------------------------
# 3. the block is never a directive
# ---------------------------------------------------------------------------


def test_the_block_contains_no_imperative_or_recommendation():
    """The one property that separates this observer from the retired controller.

    Rendered in several states -- nothing observed, candidates observed, a write
    already attempted, a question already asked -- so the guard covers every
    branch of the renderer rather than one happy path.
    """
    blocks = [
        render_task_state_block(task_state_slots(INSTRUCTION)),
        render_task_state_block(
            task_state_slots(INSTRUCTION),
            candidate_counts={"商家": 3, "商品": 12},
        ),
        render_task_state_block(
            task_state_slots(NO_REQUIREMENT_INSTRUCTION),
            candidate_counts={"商品": 4},
            write_attempted=True,
            question_asked=True,
        ),
        render_task_state_block(
            task_state_slots("帮我订个酒店", {"room_type": "大床房"}),
            write_attempted=True,
        ),
    ]
    for block in blocks:
        assert block, "each state must render something"
        for word in FORBIDDEN_WORDS:
            assert word not in block, f"{word!r} makes the block a directive: {block}"

    # ... and the same guard on the text the model actually receives.
    agent = _agent()
    annotated = agent._annotate_task_state(_tool_message(CANDIDATES))
    for word in FORBIDDEN_WORDS:
        assert word not in annotated.content


# ---------------------------------------------------------------------------
# 4. the environment's message is never modified
# ---------------------------------------------------------------------------


def test_the_original_message_is_byte_identical_after_annotation():
    agent = _agent()
    message = _tool_message(CANDIDATES)
    before = message.content

    annotated = agent._annotate_task_state(message)

    assert annotated is not message
    assert message.content == before == CANDIDATES
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
    message = _tool_message(CANDIDATES)
    agent.generate_next_message(message, None)

    assert len(seen) == 1
    assert "【本任务状态】" in seen[0].content
    assert message.content == CANDIDATES, "the saved trajectory must be untouched"


# ---------------------------------------------------------------------------
# 5. the bound
# ---------------------------------------------------------------------------


def test_the_appended_block_respects_the_character_bound():
    agent = _agent()
    annotated = agent._annotate_task_state(_tool_message(CANDIDATES))
    appended = _appended(annotated.content, CANDIDATES)
    assert appended
    assert len(appended) <= _MAX_TASK_STATE_CHARS


def test_the_renderer_drops_whole_fields_rather_than_cutting_one():
    """A truncated token would be a malformed claim, not a shorter one."""
    slots = [(f"field_{index:02d}", index % 2 == 0) for index in range(80)]
    block = render_task_state_block(slots)

    assert block
    assert len(block) <= _MAX_TASK_STATE_CHARS
    assert block.endswith("已提问：否"), "the status tail is never cut"
    assert "已定" in block and "待定" in block
    assert not block.endswith("=")


def test_the_bound_is_a_named_constant():
    assert isinstance(_MAX_TASK_STATE_CHARS, int)
    assert 0 < _MAX_TASK_STATE_CHARS <= 2000


# ---------------------------------------------------------------------------
# 6. the write flag is an observation, not a prediction
# ---------------------------------------------------------------------------


def test_write_attempted_is_reported_only_after_a_write_call_was_observed():
    agent = _agent()
    agent._observe_task_state_outbound(
        SimpleNamespace(
            content=None,
            tool_calls=[SimpleNamespace(name="delivery_product_search_recommand")],
        )
    )
    assert agent.write_attempted is False
    first = agent._annotate_task_state(_tool_message(CANDIDATES))
    assert "已发起写入：否" in first.content

    agent._observe_task_state_outbound(
        SimpleNamespace(
            content=None,
            tool_calls=[SimpleNamespace(name="create_delivery_order")],
        )
    )
    assert agent.write_attempted is True
    second = agent._annotate_task_state(_tool_message(CANDIDATES, "t2"))
    assert "已发起写入：是" in second.content


def test_write_state_resets_with_the_next_subtask():
    agent = _agent()
    agent._observe_task_state_outbound(
        SimpleNamespace(content=None, tool_calls=[SimpleNamespace(name="create_delivery_order")])
    )
    assert agent.write_attempted is True
    agent.set_current_instruction("帮我订个酒店")
    assert agent.write_attempted is False
    assert agent._task_state_ledger is not None
    assert agent._task_state_ledger.candidates == {}


def test_a_question_already_sent_is_reported():
    agent = _agent()
    assert agent._question_was_asked() is False
    agent._observe_task_state_outbound(
        SimpleNamespace(content="请问您想送到哪里呢？", tool_calls=None)
    )
    assert agent._question_was_asked() is True
    annotated = agent._annotate_task_state(_tool_message(CANDIDATES, "t3"))
    assert "已提问：是" in annotated.content


def test_the_proactive_engines_own_count_is_honoured():
    """A committed question counts even if this mechanism observed no turn."""
    agent = _agent(proactive_loop=True)
    agent.memory.proactive.commit_question("您希望什么时间呢？", slot="time")
    assert agent.questions_observed == 0
    assert agent._question_was_asked() is True


# ---------------------------------------------------------------------------
# 7. nothing to report -> nothing appended
# ---------------------------------------------------------------------------


def test_a_result_with_nothing_to_report_appends_nothing():
    agent = AdaptAgent(
        tools=[],
        domain_policy="{time}",
        memory=ADAPTMemory(),
        user_profile={},
        llm=None,
        llm_args={},
        time="2026-03-01 10:00:00",
        language="chinese",
        enable_task_state=True,
    )
    agent.set_current_instruction(NO_REQUIREMENT_INSTRUCTION)
    message = _tool_message(NO_CANDIDATES)
    assert agent._annotate_task_state(message) is message
    assert agent.task_states_annotated == 0


def test_without_an_instruction_nothing_is_reported():
    agent = AdaptAgent(
        tools=[],
        domain_policy="{time}",
        memory=ADAPTMemory(),
        user_profile={},
        llm=None,
        llm_args={},
        time="2026-03-01 10:00:00",
        language="chinese",
        enable_task_state=True,
    )
    message = _tool_message(CANDIDATES)
    assert agent._annotate_task_state(message) is message
    assert agent.task_states_annotated == 0


def test_a_non_tool_message_is_never_annotated():
    agent = _agent()
    user_message = SimpleNamespace(role="user", content="就来一杯奶茶吧")
    assert agent._annotate_task_state(user_message) is user_message
    assert agent.task_states_annotated == 0


def test_a_backend_without_slot_resolution_goes_inert_rather_than_guessing():
    """No structured facts -> compiler slots only, and no invented settlement."""
    agent = AdaptAgent(
        tools=[],
        domain_policy="{time}",
        memory=object(),
        user_profile={},
        llm=None,
        llm_args={},
        time="2026-03-01 10:00:00",
        language="chinese",
        enable_task_state=True,
    )
    agent.set_current_instruction(INSTRUCTION)
    annotated = agent._annotate_task_state(_tool_message(NO_CANDIDATES))
    assert "product=已定" in annotated.content
    assert "address=待定" in annotated.content


def test_annotation_failure_is_swallowed(monkeypatch):
    """Bookkeeping must never be able to fail a run."""
    agent = _agent()
    monkeypatch.setattr(
        AdaptAgent,
        "_task_state_block",
        lambda self: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    message = _tool_message(CANDIDATES)
    assert agent._annotate_task_state(message) is message


def test_loop_events_report_the_third_mechanism():
    agent = _agent()
    events = agent.loop_events
    assert events["task_state_enabled"] is True
    assert events["task_states_annotated"] == 0


# ---------------------------------------------------------------------------
# 8. runner threading: the flag reaches the agent and the checkpoint
# ---------------------------------------------------------------------------


def _stub_runner(monkeypatch, captured: dict):
    import agent.vitabench_runner as runner

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
    monkeypatch.setattr(runner, "VitaBenchADAPTMemory", lambda **kwargs: object())
    monkeypatch.setattr(runner, "PersonalizationUser", lambda **kwargs: object())
    monkeypatch.setattr(
        runner,
        "get_prompts",
        lambda language: SimpleNamespace(personalization_agent_system_prompt="{time}"),
    )
    monkeypatch.setattr(
        runner, "IntegrityPersonalizationOrchestrator", FakeOrchestrator
    )
    return runner


def test_run_stock_forwards_the_task_state_switch(monkeypatch):
    captured: dict = {}
    runner = _stub_runner(monkeypatch, captured)

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
        enable_task_state=True,
    )

    assert captured["enable_task_state"] is True


def test_run_stock_defaults_the_task_state_switch_off(monkeypatch):
    captured: dict = {}
    runner = _stub_runner(monkeypatch, captured)

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

    assert captured["enable_task_state"] is False


def test_the_second_hop_forwards_the_task_state_switch(monkeypatch):
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
        enable_task_state=True,
    )

    assert captured["enable_task_state"] is True


def test_the_cli_flag_exists_and_defaults_off():
    import agent.vitabench_runner as runner

    parser = runner.build_parser()
    assert parser.parse_args(
        ["--save-to", "x.json", "--agent-llm", "a", "--user-llm", "u"]
    ).task_state is False
    assert parser.parse_args(
        [
            "--save-to",
            "x.json",
            "--agent-llm",
            "a",
            "--user-llm",
            "u",
            "--task-state",
        ]
    ).task_state is True


def test_run_selected_records_the_switch_in_the_checkpoint(monkeypatch, tmp_path):
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
        enable_task_state=True,
    )

    assert checkpoint["info"]["adapt_agent"]["task_state"] is True

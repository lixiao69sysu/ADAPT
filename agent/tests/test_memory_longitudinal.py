"""Longitudinal behaviour conformance suite for the ADAPT memory layer.

Each test is a small *preference timeline*: a sequence of user turns across dates,
followed by an assertion about what the memory believes and what it renders. The
point is not to cover lines but to pin the behaviours a long-horizon
personalization memory must not get wrong, so that a regression shows up as a
named scenario rather than as a score move.

The behaviours, and where each came from:

* polarity        -- a durable negative is never rendered as a preference (E-059)
* completeness    -- every hard condition renders; none is dropped by position (E-060)
* clause boundary -- one clause cannot swallow the next (E-061)
* multi-valued    -- avoids/allergies accumulate rather than collapsing to one value
* scope isolation -- a hotel preference must not surface in a food task
* correction      -- a current-session answer replaces the remembered value (E-069)
* delegation      -- handing the decision back is not a preference value (E-070)
* gap closure     -- a recorded answer closes the gap, so it is not re-asked (E-069)

Zero-model: no API call, no evaluator, no benchmark data. Run it and read the test
names as the specification:

    python -m pytest agent/tests/test_memory_longitudinal.py -v
"""

from __future__ import annotations

from agent.decision import DecisionCard
from agent.memory.adapt_memory import ADAPTMemory


def _memory(*turns: tuple[str, str]) -> ADAPTMemory:
    """Build a memory from ``(date, user_utterance)`` turns, in order."""
    memory = ADAPTMemory()
    for date, text in turns:
        memory.update([
            {
                "date": date,
                "behavior": [],
                "dialogue": [{"role": "user", "content": text}],
            }
        ])
    return memory


def _facts(memory: ADAPTMemory, needle: str) -> list[tuple[str, str, str]]:
    return [
        (fact.value, fact.polarity, fact.dimension)
        for fact in memory.facts
        if needle in fact.value
    ]


# ---------------------------------------------------------------------------
# Polarity
# ---------------------------------------------------------------------------


def test_allergy_is_stored_as_a_durable_negative_not_a_preference():
    memory = _memory(("2026-03-01", "我对花生过敏。"))
    assert _facts(memory, "花生") == [("花生", "negative", "safety")]
    rendered = memory.read()
    assert "花生" in rendered
    assert "AVOID:" in rendered
    assert "PREFER:" not in rendered


def test_a_preference_and_an_allergy_in_one_sentence_stay_separate():
    """One clause used to swallow the next, hiding the allergy (E-061)."""
    memory = _memory(("2026-03-01", "我喜欢吃香菜，但是我对花生过敏。"))
    assert _facts(memory, "花生") == [("花生", "negative", "safety")]
    assert _facts(memory, "香菜") == [("香菜", "positive", "like")]
    for fact in memory.facts:
        assert "但是" not in fact.value


def test_a_dislike_is_never_rendered_under_prefer():
    memory = _memory(("2026-03-01", "我不喜欢吃香菜。"))
    rendered = memory.read()
    assert "香菜" in rendered
    assert "AVOID:" in rendered
    assert "PREFER:" not in rendered


# ---------------------------------------------------------------------------
# Multi-valued sets
# ---------------------------------------------------------------------------


def test_aversions_accumulate_rather_than_collapsing_to_one_value():
    memory = _memory(
        ("2026-03-01", "我对花生过敏。"),
        ("2026-03-05", "我也不吃香菜。"),
    )
    negatives = {
        fact.value for fact in memory.facts if fact.polarity == "negative"
    }
    assert {"花生", "香菜"} <= negatives, negatives


# ---------------------------------------------------------------------------
# Completeness of hard conditions
# ---------------------------------------------------------------------------


def test_every_hard_condition_renders_regardless_of_position():
    taboos = ["花生", "香菜", "内脏"]
    musts = ["category=咖啡", "size=大杯", "temperature=热饮", "sweetness=无糖"]
    rendered = DecisionCard(must=list(musts), avoid=list(taboos)).render()
    for value in [*taboos, *musts]:
        assert value in rendered, f"{value} was dropped"


# ---------------------------------------------------------------------------
# Scope isolation
# ---------------------------------------------------------------------------


def test_a_hotel_preference_does_not_surface_in_a_food_task():
    memory = _memory(
        ("2026-03-01", "帮我订个酒店，要大床房。"),
    )
    card = memory.compile_task("帮我点个外卖")
    assert "大床房" not in card.prefer
    assert "大床房" not in card.render()


def test_a_food_taste_preference_does_not_surface_in_a_hotel_task():
    memory = _memory(("2026-03-01", "我爱吃麻辣火锅。"))
    card = memory.compile_task("帮我订个酒店")
    assert "麻辣" not in card.render()


# ---------------------------------------------------------------------------
# Current instruction and current correction outrank history
# ---------------------------------------------------------------------------


def test_the_current_instruction_outranks_a_remembered_value():
    """`resolve_preference_slots` must not keep a slot the instruction resolved."""
    from agent.decision import TaskSpec

    memory = _memory(("2026-03-01", "我喜欢喝热饮，以后都点热的。"))
    spec = TaskSpec.compile("我想喝低咖啡因的咖啡")
    assert spec.resolved_slots.get("caffeine") == "低咖啡因"
    # History must not re-supply a slot the current instruction already fixed.
    assert "caffeine" not in memory.resolve_task_slots("我想喝低咖啡因的咖啡")


def test_a_current_answer_replaces_the_remembered_value():
    memory = ADAPTMemory()
    memory.begin_subtask("帮我点杯奶茶")
    memory.commit_question(
        "您之前提到无糖，这次还是这样吗？",
        slot="sweetness",
        value="无糖",
        is_confirmation=True,
    )
    memory.record_user_answer("不是，这次少糖")
    stored = {(fact.value, fact.dimension) for fact in memory.facts}
    assert ("无糖", "sweetness") not in stored
    assert any(dimension == "sweetness" for _value, dimension in stored)


# ---------------------------------------------------------------------------
# Delegation, and gap closure
# ---------------------------------------------------------------------------


def test_delegation_stores_no_preference_value():
    """Observed in the first proactive smoke: "随便，你看着办吧。" became a value."""
    memory = ADAPTMemory()
    memory.begin_subtask("想去昆明玩，你帮我定个这周六的票吧。")
    memory.commit_question("这次出行您想用哪种方式？", slot="transport")
    memory.record_user_answer("随便，你看着办吧。")
    assert len(memory.facts) == 0


def test_a_recorded_answer_closes_the_gap_so_it_is_not_asked_again():
    instruction = "想去昆明玩，你帮我定个这周六的票吧。"
    memory = ADAPTMemory()
    memory.begin_subtask(instruction)
    proposal = memory.propose(instruction)
    assert proposal is not None and proposal.slot == "transport"
    memory.commit_question(proposal.question, slot=proposal.slot)
    memory.record_user_answer("坐高铁吧")
    assert memory.propose(instruction) is None, "the answered gap must stay closed"


def test_a_delegated_answer_leaves_the_gap_open():
    """Delegation is not an answer, so the gap survives it.

    The engine deliberately will not repeat an identical question (one identical
    question may be counted only once), so the observable is that the
    gap is still open and a *different* slot question is still available — not
    that the same question comes back.
    """
    from agent.memory.proactive import QuestionContext

    instruction = "想去昆明玩，你帮我定个这周六的票吧。"
    memory = ADAPTMemory()
    memory.begin_subtask(instruction)
    proposal = memory.propose(instruction)
    memory.commit_question(proposal.question, slot=proposal.slot)
    memory.record_user_answer("随便，你看着办吧。")

    assert not memory.facts, "delegation must store no value"
    from agent.decision import TaskSpec

    task = TaskSpec.compile(instruction)
    context = QuestionContext(
        instruction=instruction,
        domain=task.domain,
        facet=task.facet,
        action=task.action,
        unknown_slots=tuple(task.unknown_slots or ()),
        known_slots={},
    )
    engine = memory.proactive
    assert "transport" in engine.open_gaps(context)
    engine.reset_subtask()
    assert engine.propose(context=context) is not None


# ---------------------------------------------------------------------------
# Long-horizon hygiene
# ---------------------------------------------------------------------------


def test_repeated_reads_do_not_change_memory_state():
    """read() is a pure function; a long run must not drift just by looking."""
    memory = _memory(("2026-03-01", "我对花生过敏。"), ("2026-03-05", "我爱吃香菜。"))
    before = [(f.value, f.status, f.confidence) for f in memory.facts]
    budget = memory.proactive.asked_this_subtask
    for _ in range(5):
        memory.read()
        memory.read("帮我推荐点吃的")
    after = [(f.value, f.status, f.confidence) for f in memory.facts]
    assert before == after
    assert memory.proactive.asked_this_subtask == budget


def test_a_later_turn_does_not_erase_an_earlier_durable_negative():
    memory = _memory(
        ("2026-03-01", "我对花生过敏。"),
        ("2026-03-10", "今天想吃点甜的，帮我点个蛋糕。"),
        ("2026-03-20", "再帮我买杯奶茶。"),
    )
    assert _facts(memory, "花生") == [("花生", "negative", "safety")]
    assert "花生" in memory.read()


def test_duplicate_interactions_are_not_ingested_twice():
    """The same interaction object must not inflate the timeline."""
    interaction = {
        "date": "2026-03-01",
        "behavior": [],
        "dialogue": [{"role": "user", "content": "我对花生过敏。"}],
    }
    memory = ADAPTMemory()
    memory.update([interaction])
    first = len(memory.facts)
    memory.update([interaction])
    assert len(memory.facts) == first

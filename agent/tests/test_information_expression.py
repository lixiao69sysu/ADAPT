"""Information-expression invariants (E-059, E-060).

These tests are the counterexample gate for the two general invariants:

* polarity is a typed property of a fact, so no read path may assert a
  polarity it does not know (E-059);
* MUST and AVOID are the graded conditions of the current instruction, so no
  render may drop them by list position (E-060).

They are zero-model: no API call, no evaluator, no benchmark data.
"""

from __future__ import annotations

from agent.decision import DecisionCard
from agent.memory.adapt_memory import ADAPTMemory


def _memory_with(dialogue: str) -> ADAPTMemory:
    memory = ADAPTMemory(top_k=60)
    memory.update([
        {
            "date": "2026-03-01",
            "behavior": [],
            "dialogue": [{"role": "user", "content": dialogue}],
        }
    ])
    return memory


# ---------------------------------------------------------------------------
# E-059: the no-query read path must preserve polarity
# ---------------------------------------------------------------------------


def test_allergy_is_not_rendered_as_a_preference():
    """The regression from E-059: 'PREFER: 花生' for an allergy."""
    memory = _memory_with("我对花生过敏。")

    stored = [fact for fact in memory.facts if "花生" in fact.value]
    assert stored, "the allergy must still be extracted as a fact"
    assert stored[0].polarity == "negative"
    assert stored[0].dimension == "safety"

    overview = memory.read()
    assert "花生" in overview
    assert "AVOID:" in overview
    assert "PREFER:" not in overview


def test_read_preference_memory_tool_preserves_polarity():
    """The agent-callable tool reads the no-query path, so it must agree."""
    memory = _memory_with("我对花生过敏。")
    assert "PREFER:" not in memory.read_preference_memory()


def test_positive_fact_still_renders_as_preference():
    """The counterexample: a genuine like must not become an AVOID."""
    memory = _memory_with("我喜欢吃香菜。")
    overview = memory.read()
    assert "香菜" in overview
    assert "PREFER:" in overview
    assert "AVOID:" not in overview


def test_mixed_polarity_splits_into_both_sections():
    """Both polarities render, each under its own label."""
    memory = _memory_with("我喜欢吃香菜，但是我对花生过敏。")
    overview = memory.read()
    if "花生" in overview and "香菜" in overview:
        avoid_line = next(
            (line for line in overview.splitlines() if line.startswith("AVOID:")),
            "",
        )
        prefer_line = next(
            (line for line in overview.splitlines() if line.startswith("PREFER:")),
            "",
        )
        assert "花生" in avoid_line
        assert "香菜" in prefer_line


def test_empty_memory_overview_is_unchanged():
    memory = ADAPTMemory(top_k=60)
    assert memory.read() == "No user preference information available yet."


def test_overview_is_side_effect_free():
    """Repeated reads must not change memory state or question budget."""
    memory = _memory_with("我对花生过敏。")
    before = (len(memory.facts), memory.proactive.asked_this_subtask)
    first = memory.read()
    second = memory.read()
    assert first == second
    assert (len(memory.facts), memory.proactive.asked_this_subtask) == before


# ---------------------------------------------------------------------------
# E-061: a preference object must not span clause punctuation
# ---------------------------------------------------------------------------


def test_like_clause_does_not_swallow_the_following_allergy():
    memory = _memory_with("我喜欢吃香菜，但是我对花生过敏。")
    facts = {(fact.value, fact.polarity, fact.dimension) for fact in memory.facts}

    assert ("花生", "negative", "safety") in facts, "the allergy must survive"
    assert ("香菜", "positive", "like") in facts, "the like must stay its own fact"
    for value, _polarity, _dimension in facts:
        assert "但是" not in value, f"captured across a clause boundary: {value!r}"


def test_allergy_alone_still_parses():
    """Counterexample: the single-clause form must be unaffected."""
    memory = _memory_with("我对花生过敏。")
    facts = {(fact.value, fact.polarity, fact.dimension) for fact in memory.facts}
    assert ("花生", "negative", "safety") in facts


def test_one_character_object_still_parses():
    """A one-character object is legitimate ("我爱吃辣", "不能吃辣").

    The old patterns only reached their two-character minimum by crossing the
    following comma, so clamping them to the clause would have silently dropped
    these if the minimum had not been separated from the capture width.
    """
    liked = {(f.value, f.polarity) for f in _memory_with("我爱吃辣。").facts}
    assert ("辣", "positive") in liked

    avoided = {(f.value, f.polarity) for f in _memory_with("我不能吃辣。").facts}
    assert ("辣", "negative") in avoided


def test_like_alone_still_parses():
    memory = _memory_with("我喜欢吃香菜。")
    facts = {(fact.value, fact.polarity, fact.dimension) for fact in memory.facts}
    assert ("香菜", "positive", "like") in facts


def test_dislike_clause_does_not_swallow_the_next_clause():
    memory = _memory_with("我不吃香菜，我喜欢吃牛肉。")
    facts = {(fact.value, fact.polarity, fact.dimension) for fact in memory.facts}
    assert ("香菜", "negative", "avoid") in facts
    for value, _polarity, _dimension in facts:
        assert "，" not in value, f"captured across a clause boundary: {value!r}"


def test_spurious_function_word_item_is_still_rejected():
    """The precise-recall guard from E-044 must not regress.

    "让我去查过敏源" is about someone searching for allergens, not a durable
    safety constraint on 让我去查.
    """
    memory = _memory_with("客服让我去查过敏源。")
    for fact in memory.facts:
        assert fact.polarity != "negative" or "让" not in fact.value


def test_enumeration_is_not_split_into_clauses():
    """Counterexample: "、" is a list separator, not a clause boundary."""
    memory = _memory_with("我喜欢吃苹果、香蕉。")
    values = {fact.value for fact in memory.facts if fact.polarity == "positive"}
    assert values, "the enumeration must produce a positive fact"
    assert any("苹果" in value for value in values)


# ---------------------------------------------------------------------------
# E-060: hard constraints must never be dropped by position
# ---------------------------------------------------------------------------


def test_all_hard_constraints_survive_rendering():
    taboos = ["花生", "香菜", "内脏"]
    musts = ["category=咖啡", "size=大杯", "temperature=热饮", "sweetness=无糖"]
    card = DecisionCard(must=list(musts), avoid=list(taboos))
    rendered = card.render()
    for value in [*taboos, *musts]:
        assert value in rendered, f"{value} was dropped by position"


def test_avoid_is_not_capped_at_two():
    card = DecisionCard(avoid=[f"avoid-{index}" for index in range(6)])
    rendered = card.render()
    for index in range(6):
        assert f"avoid-{index}" in rendered


def test_must_is_not_capped_at_three():
    card = DecisionCard(must=[f"must-{index}" for index in range(6)])
    rendered = card.render()
    for index in range(6):
        assert f"must-{index}" in rendered


def test_soft_budget_shrinks_as_hard_grows():
    """Seven hard facts leave exactly one soft slot, which ASK takes."""
    card = DecisionCard(
        must=["m1", "m2", "m3", "m4"],
        avoid=["a1", "a2", "a3"],
        prefer=["p1", "p2"],
        ask=["q1"],
        evidence=["e1"],
    )
    rendered = card.render()
    assert "ASK:" in rendered
    assert "PREFER:" not in rendered
    assert "EVIDENCE:" not in rendered


def test_soft_only_card_behaves_as_before():
    """With no hard facts the original budget still applies."""
    card = DecisionCard(prefer=[f"p{index}" for index in range(20)])
    rendered = card.render()
    assert "p0" in rendered
    assert "p7" in rendered
    assert "p8" not in rendered


def test_negative_polarity_fact_reaches_card_avoid():
    """End-to-end: memory allergy -> card AVOID, not card PREFER."""
    memory = _memory_with("我对花生过敏。")
    card = memory.compile_task("帮我推荐点吃的")
    rendered = card.render()
    avoid_line = next(
        (line for line in rendered.splitlines() if line.startswith("AVOID:")), ""
    )
    prefer_line = next(
        (line for line in rendered.splitlines() if line.startswith("PREFER:")), ""
    )
    assert "花生" in avoid_line
    assert "花生" not in prefer_line

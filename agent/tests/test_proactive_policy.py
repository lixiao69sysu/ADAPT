"""Counterexample gate for the gap-driven proactive policy (E-068).

The policy exists to ask about a *declared, user-only, still-unknown* gap, and
to do nothing otherwise. These tests pin the invariants that the previous
keyword policy violated:

* no declared gap -> no question (a book request must not get a food question);
* the question comes from the slot, not from a domain topic table;
* a slot the memory already answered is never re-asked;
* the policy never reads rendered memory prose, so a remembered dislike cannot be
  echoed back as a preference (the E-059 polarity invariant, one layer up);
* the budget is spent only when a question was actually sent.

Zero-model: no API call, no evaluator, no benchmark data.
"""

from __future__ import annotations

from agent.decision import TaskSpec
from agent.memory.proactive import (
    ProactiveEngine,
    QuestionContext,
    SLOT_QUESTIONS,
)


def _context(instruction: str, **overrides) -> QuestionContext:
    spec = TaskSpec.compile(instruction)
    base = QuestionContext(
        instruction=instruction,
        domain=spec.domain,
        facet=spec.facet,
        action=spec.action,
        unknown_slots=tuple(spec.unknown_slots or ()),
        resolved_slots=dict(spec.resolved_slots or {}),
        known_slots={},
    )
    return base.__class__(**{**base.__dict__, **overrides}) if overrides else base


def _ask(instruction: str, **overrides) -> str:
    return ProactiveEngine().propose_question(
        context=_context(instruction, **overrides)
    ) or ""


# ---------------------------------------------------------------------------
# No gap -> no question
# ---------------------------------------------------------------------------


def test_book_request_without_a_declared_gap_asks_nothing():
    assert _ask("帮我推荐一本书") == ""


def test_a_stated_gap_produces_a_question():
    question = _ask("帮我订个酒店")
    assert question, "a hotel request with no city/date is a declared gap"


def test_generic_shop_recommendation_asks_nothing_off_domain():
    question = _ask("小美同学没去过梦幻城堡。帮我推荐家店")
    for marker in ("清淡", "麻辣", "烧烤", "口味", "几个人", "包间"):
        assert marker not in question


# ---------------------------------------------------------------------------
# Slot-driven, not topic-driven
# ---------------------------------------------------------------------------


def test_question_comes_from_the_slot_table():
    context = _context("帮我买张出行票")
    assert "transport" in context.unknown_slots
    question = _ask("帮我买张出行票")
    assert question == SLOT_QUESTIONS["transport"]


def test_haircut_asks_about_time_not_headcount():
    question = _ask("帮我预约理发")
    assert "时间" in question
    for marker in ("几个人", "包间"):
        assert marker not in question


def test_resolved_slot_is_not_asked():
    """A slot the instruction already resolved must not be asked."""
    context = _context("买张去上海的高铁票")
    assert "transport" not in context.unknown_slots
    assert "destination" not in context.unknown_slots
    question = ProactiveEngine().propose_question(context=context) or ""
    assert "想用哪种方式" not in question
    assert "要去哪里" not in question


def test_known_slot_from_memory_is_not_asked():
    """A gap memory already fills is closed before it can be asked."""
    context = _context("帮我订个酒店", known_slots={"city": "贵阳"})
    gaps = ProactiveEngine().open_gaps(context)
    assert "city" not in gaps


def test_no_gap_context_asks_nothing():
    engine = ProactiveEngine()
    assert engine.propose_question(context=QuestionContext()) is None


# ---------------------------------------------------------------------------
# Tool-findable / context-resolvable slots are never asked
# ---------------------------------------------------------------------------


def test_tool_findable_slots_are_not_asked():
    context = QuestionContext(unknown_slots=("product", "shop_or_service", "store"))
    assert ProactiveEngine().open_gaps(context) == []


def test_context_resolvable_address_is_not_asked():
    """Every benchmark profile carries an address, so asking is redundant."""
    context = QuestionContext(unknown_slots=("address",))
    assert ProactiveEngine().open_gaps(context) == []


# ---------------------------------------------------------------------------
# Polarity: the policy must not assert what it cannot know
# ---------------------------------------------------------------------------


def test_policy_ignores_rendered_memory_prose():
    """The echoed-dislike defect came from substring-matching memory text.

    Passing contradictory prose must not change the proposal: only the
    structured context is read.
    """
    context = _context("帮我买张出行票")
    engine = ProactiveEngine()
    without = engine.propose_question(context=context)
    with_prose = engine.propose_question(
        "帮我买张出行票",
        "【饮食/消费偏好】\n  不喜欢/忌口：高铁\n  常选：飞机",
        "ota",
        context=context,
    )
    assert without == with_prose
    assert without is not None
    assert "偏好高铁" not in without


def test_no_question_asserts_a_remembered_preference():
    """No slot question may claim the user previously preferred something."""
    for slot, question in SLOT_QUESTIONS.items():
        for marker in ("您之前偏好", "您平时喜欢"):
            assert marker not in question, f"{slot} asserts an unverified preference"


# ---------------------------------------------------------------------------
# Budget and answer association
# ---------------------------------------------------------------------------


def test_budget_is_spent_only_on_commit():
    engine = ProactiveEngine(max_questions=2)
    context = _context("帮我订个酒店")
    question = engine.propose_question(context=context)
    assert engine.asked_this_subtask == 0, "proposing must not spend budget"
    assert engine.commit_question(question) is True
    assert engine.asked_this_subtask == 1
    assert engine.pending_question == question


def test_identical_question_is_counted_once():
    engine = ProactiveEngine(max_questions=2)
    assert engine.commit_question("问一句") is True
    assert engine.commit_question("问一句") is False
    assert engine.asked_this_subtask == 1


def test_budget_cap_is_respected():
    engine = ProactiveEngine(max_questions=1)
    context = _context("帮我订个酒店")
    first = engine.propose_question(context=context)
    assert first
    engine.commit_question(first)
    assert engine.propose_question(context=context) is None


def test_reset_clears_pending_and_budget():
    engine = ProactiveEngine(max_questions=2)
    engine.commit_question("问一句")
    engine.reset_subtask()
    assert engine.pending_question is None
    assert engine.asked_this_subtask == 0
    assert engine.asked_questions == set()


# ---------------------------------------------------------------------------
# Ticket routing (the E-041 recurrence)
# ---------------------------------------------------------------------------


def test_bare_ticket_phrase_routes_to_travel():
    spec = TaskSpec.compile("下周6号要去逛迪斯尼了，帮我买去迪的票")
    assert spec.domain == "ota"
    assert "transport" in spec.unknown_slots


def test_romance_slang_is_not_a_ticket():
    spec = TaskSpec.compile("晚上给我点个双人餐外卖，在家和男票一起吃！")
    assert spec.domain != "ota"
    assert "transport" not in (spec.unknown_slots or ())


def test_unrelated_ticket_nouns_are_not_travel():
    for text in ("帮我开张发票", "看看今天的股票", "买张彩票"):
        spec = TaskSpec.compile(text)
        assert spec.domain != "ota", f"{text!r} must not route to ota"

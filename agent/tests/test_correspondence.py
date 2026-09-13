"""Counterexample gate for the three-valued constraint correspondence (E-064).

The invariant under test: a constraint's state relative to a candidate is one of
{satisfied, violated, unknown}, and the *constraint's* polarity -- never the field
that happens to carry it -- decides which states are reachable.

The specific failures this file must keep dead:

* a prohibition being reported as satisfied, or entering a positive score
  (the E-064 A2 polarity inversion);
* "violated" and "unknown" collapsing into one value (A1);
* durable negatives being silently dropped from the view (A3);
* a requirement being called violated on the strength of a missing attribute.

Zero-model: no API call, no evaluator, no benchmark data.
"""

from __future__ import annotations

from agent.decision import (
    Candidate,
    Constraint,
    ConstraintOperator,
    ConstraintTarget,
    DecisionCard,
)
from agent.runtime.correspondence import (
    CandidateConstraint,
    ConstraintPolarity,
    ConstraintStatus,
    apply_current_correction,
    build_vocabulary,
    constraints_from_card,
    correspondence_for_candidate,
    render_correspondence,
    render_correspondence_block,
    violated_constraints,
)


def _candidate(candidate_id: str, **attributes: str) -> Candidate:
    return Candidate(
        candidate_id=candidate_id,
        entity_type="product",
        name=f"item-{candidate_id}",
        raw=f"item-{candidate_id}",
        tool_name="delivery_product_search_recommand",
        attributes=dict(attributes),
    )


def _status(candidate: Candidate, constraint: CandidateConstraint) -> ConstraintStatus:
    return correspondence_for_candidate(candidate, [constraint])[0].status


# ---------------------------------------------------------------------------
# A1: violated and unknown must be distinct
# ---------------------------------------------------------------------------


def test_violated_and_unknown_are_distinct():
    satisfies = _candidate("S1_P00001", temperature="热饮")
    violates = _candidate("S1_P00002", temperature="冰饮")
    unknown = _candidate("S1_P00003", size="大杯")

    require = CandidateConstraint.require("热饮", attribute_key="temperature")
    assert _status(satisfies, require) is ConstraintStatus.SATISFIED
    assert _status(violates, require) is ConstraintStatus.VIOLATED
    assert _status(unknown, require) is ConstraintStatus.UNKNOWN


def test_missing_attribute_is_unknown_not_violated():
    """Absence of evidence is not evidence of absence."""
    bare = _candidate("S1_P00004", size="大杯")
    require = CandidateConstraint.require("热饮", attribute_key="temperature")
    assert _status(bare, require) is ConstraintStatus.UNKNOWN


# ---------------------------------------------------------------------------
# A2: a prohibition must never become a positive match
# ---------------------------------------------------------------------------


def test_prohibition_match_is_violated_never_satisfied():
    forbidden = _candidate("S1_P00011", topping="花生碎")
    forbid = CandidateConstraint.forbid("花生碎")
    assert _status(forbidden, forbid) is ConstraintStatus.VIOLATED
    assert _status(forbidden, forbid) is not ConstraintStatus.SATISFIED


def test_prohibition_absence_is_unknown_never_satisfied():
    """Not finding the forbidden item does not prove the candidate is clean."""
    clean = _candidate("S1_P00012", topping="奶盖")
    forbid = CandidateConstraint.forbid("花生碎")
    assert _status(clean, forbid) is ConstraintStatus.UNKNOWN
    assert _status(clean, forbid) is not ConstraintStatus.SATISFIED


def test_prohibition_never_reaches_a_positive_score():
    """The E-064 A2 inversion: the forbidden candidate must not outrank clean."""
    clean = _candidate("S1_P00013", topping="奶盖", temperature="热饮")
    forbidden = _candidate("S1_P00014", topping="花生碎", temperature="热饮")
    forbid = CandidateConstraint.forbid("花生碎")

    def satisfied_count(candidate: Candidate) -> int:
        return sum(
            1
            for item in correspondence_for_candidate(candidate, [forbid])
            if item.status is ConstraintStatus.SATISFIED
        )

    assert satisfied_count(forbidden) == 0
    assert satisfied_count(clean) == 0
    assert violated_constraints(forbidden, [forbid])
    assert not violated_constraints(clean, [forbid])


def test_constraints_from_card_keeps_avoid_as_forbid():
    """`avoid` must become FORBID, never REQUIRE (the A2 root cause)."""
    card = DecisionCard(avoid=["花生碎"], prefer=["热饮"])
    constraints = constraints_from_card(card)
    by_value = {c.value: c.polarity for c in constraints}
    assert by_value["花生碎"] is ConstraintPolarity.FORBID
    assert by_value["热饮"] is ConstraintPolarity.REQUIRE
    assert all(
        c.polarity is ConstraintPolarity.FORBID for c in constraints if c.value == "花生碎"
    )


def test_constraints_from_card_does_not_resurrect_a_forbidden_value_as_require():
    card = DecisionCard(avoid=["花生碎"], prefer=["花生碎"])
    constraints = constraints_from_card(card)
    forbidden = [c for c in constraints if c.value == "花生碎"]
    assert forbidden, "the value must appear"
    assert any(c.polarity is ConstraintPolarity.FORBID for c in forbidden)
    # If it also appears as a requirement, the polarity guard has failed.
    assert not all(c.polarity is ConstraintPolarity.REQUIRE for c in forbidden)


# ---------------------------------------------------------------------------
# A3: durable negatives must appear in the view
# ---------------------------------------------------------------------------


def test_durable_negative_appears_and_flags_the_violation():
    card = DecisionCard(avoid=["花生碎"])
    constraints = constraints_from_card(card)
    assert constraints, "a durable negative must not vanish from the view"

    forbidden = _candidate("S1_P00021", topping="花生碎")
    clean = _candidate("S1_P00022", topping="奶盖")
    assert _status(forbidden, constraints[0]) is ConstraintStatus.VIOLATED
    assert _status(clean, constraints[0]) is ConstraintStatus.UNKNOWN
    assert _status(forbidden, constraints[0]) != _status(clean, constraints[0])


# ---------------------------------------------------------------------------
# Counterexamples: the fix must not over-reach
# ---------------------------------------------------------------------------


def test_multi_valued_key_is_not_read_as_contradiction():
    """A key carrying several values cannot refute a requirement."""
    multi = _candidate("S1_P00031", topping="花生碎、奶盖")
    require = CandidateConstraint.require("椰果", attribute_key="topping")
    assert _status(multi, require) is ConstraintStatus.UNKNOWN


def test_single_valued_key_contradiction_is_violated():
    single = _candidate("S1_P00032", temperature="冰饮")
    require = CandidateConstraint.require("热饮", attribute_key="temperature")
    assert _status(single, require) is ConstraintStatus.VIOLATED


def test_one_character_constraint_is_evaluable():
    """E-061 keeps single-character objects, so they must not be auto-unknown."""
    spicy = _candidate("S1_P00033", taste="辣")
    fine = _candidate("S1_P00034", taste="清淡")
    forbid = CandidateConstraint.forbid("辣")
    assert _status(spicy, forbid) is ConstraintStatus.VIOLATED
    assert _status(fine, forbid) is ConstraintStatus.UNKNOWN


def test_underscore_and_numeric_values_are_unknown():
    """Technical fields must not become constraints."""
    candidate = _candidate("S1_P00035", product_id="S1_P00035")
    for bad in ("S1_P00035", "12345", ""):
        constraint = CandidateConstraint.require(bad)
        assert _status(candidate, constraint) is ConstraintStatus.UNKNOWN


def test_correspondence_is_pure():
    candidate = _candidate("S1_P00041", temperature="热饮")
    constraints = [CandidateConstraint.require("热饮", attribute_key="temperature")]
    first = correspondence_for_candidate(candidate, constraints)
    second = correspondence_for_candidate(candidate, constraints)
    assert first == second
    assert candidate.attributes == {"temperature": "热饮"}


# ---------------------------------------------------------------------------
# Rendering: bounded, non-directive
# ---------------------------------------------------------------------------


def test_render_reports_state_without_recommending():
    candidate = _candidate("S1_P00051", temperature="冰饮")
    constraints = [
        CandidateConstraint.require("热饮", attribute_key="temperature", hard=True),
        CandidateConstraint.forbid("花生碎", hard=True),
    ]
    rendered = render_correspondence(candidate, constraints)
    assert "S1_P00051" in rendered
    assert "冲突" in rendered
    assert "未知" in rendered
    assert "满足" not in rendered
    for word in ("推荐", "建议", "必须", "禁止选择"):
        assert word not in rendered


def test_soft_unknown_is_suppressed_but_hard_unknown_is_not():
    """A soft preference the candidate does not print carries no signal.

    Every relevant positive memory fact lands in ``card.prefer``, so rendering
    all of them buries the handful of real conflicts under ``UNKNOWN`` noise
    (E-066). Hard conditions are always rendered, whatever their state.
    """
    candidate = _candidate("S1_P00061", temperature="冰饮")
    soft = CandidateConstraint.require("常点某店", source="prefer", hard=False)
    hard = CandidateConstraint.require("无糖", source="instruction", hard=True)

    soft_only = render_correspondence(candidate, [soft])
    assert soft_only == "", "a soft UNKNOWN must not produce a line"

    with_hard = render_correspondence(candidate, [soft, hard])
    assert "无糖" in with_hard
    assert "常点某店" not in with_hard


def test_soft_satisfied_is_still_reported():
    """Suppression is for UNKNOWN only; a soft match is real information."""
    candidate = _candidate("S1_P00062", temperature="热饮")
    soft = CandidateConstraint.require("热饮", attribute_key="temperature", hard=False)
    rendered = render_correspondence(candidate, [soft])
    assert "热饮" in rendered
    assert "满足" in rendered


# ---------------------------------------------------------------------------
# Instruction-targeted requirements must be read from card.constraints
# (the E-066 correction: they were compiled all along and never read)
# ---------------------------------------------------------------------------


def test_candidate_targeted_requirements_are_extracted_as_hard():
    card = DecisionCard(
        must=["大床房"],
        constraints=[
            Constraint(
                "room_type",
                "大床房",
                ConstraintTarget.CANDIDATE,
                ConstraintOperator.CONTAINS,
                source="instruction",
            )
        ],
    )
    constraints = constraints_from_card(card)
    extracted = [c for c in constraints if c.value == "大床房"]
    assert extracted, "an instruction-stated candidate requirement must be read"
    assert extracted[0].polarity is ConstraintPolarity.REQUIRE
    assert extracted[0].hard is True


def test_excludes_constraint_is_extracted_as_hard_forbid():
    card = DecisionCard(
        constraints=[
            Constraint(
                "safety",
                "花生",
                ConstraintTarget.CANDIDATE,
                ConstraintOperator.EXCLUDES,
                source="instruction",
            )
        ]
    )
    constraints = constraints_from_card(card)
    assert any(
        c.value == "花生"
        and c.polarity is ConstraintPolarity.FORBID
        and c.hard is True
        for c in constraints
    )


def test_non_candidate_constraints_are_not_turned_into_candidate_requirements():
    """A workflow/argument constraint is not a property of a candidate."""
    card = DecisionCard(
        must=["authorization=create", "date=7号"],
        constraints=[
            Constraint(
                "authorization",
                "create",
                ConstraintTarget.WORKFLOW,
                ConstraintOperator.ALLOWS,
                source="instruction",
            ),
            Constraint(
                "date",
                "7号",
                ConstraintTarget.ARGUMENT,
                ConstraintOperator.EQUALS,
                source="instruction",
            ),
        ],
    )
    values = {c.value for c in constraints_from_card(card)}
    assert "create" not in values
    assert "7号" not in values


def test_instruction_requirement_beats_a_soft_remembered_preference():
    """Hard instruction conditions outrank soft remembered preferences."""
    card = DecisionCard(
        prefer=["热饮"],
        constraints=[
            Constraint(
                "temperature",
                "冰饮",
                ConstraintTarget.CANDIDATE,
                ConstraintOperator.CONTAINS,
                source="instruction",
            )
        ],
    )
    constraints = constraints_from_card(card)
    hard = [c for c in constraints if c.hard]
    assert [c.value for c in hard] == ["冰饮"]


# ---------------------------------------------------------------------------
# Candidate-induced vocabulary: how a keyless requirement becomes refutable
# ---------------------------------------------------------------------------


def test_vocabulary_is_induced_from_the_candidate_set():
    candidates = [
        _candidate("S1_P00101", temperature="热饮"),
        _candidate("S1_P00102", size="大杯"),
    ]
    vocabulary = build_vocabulary(candidates)
    assert vocabulary.get("热饮") == "temperature"
    assert vocabulary.get("大杯") == "size"


def test_keyless_requirement_becomes_refutable_with_the_vocabulary():
    """Without the vocabulary the answer is UNKNOWN; with it, VIOLATED.

    This is the mechanism that gives a requirement any power to refute: a
    remembered value does not say which field it belongs to, but another
    candidate in the same set does.
    """
    hot = _candidate("S1_P00111", temperature="热饮")
    iced = _candidate("S1_P00112", temperature="冰饮")
    keyless = CandidateConstraint.require("热饮", source="prefer")

    assert _status(iced, keyless) is ConstraintStatus.UNKNOWN

    vocabulary = build_vocabulary([hot, iced])
    assert (
        correspondence_for_candidate(iced, [keyless], vocabulary)[0].status
        is ConstraintStatus.VIOLATED
    )
    assert (
        correspondence_for_candidate(hot, [keyless], vocabulary)[0].status
        is ConstraintStatus.SATISFIED
    )


def test_vocabulary_does_not_refute_on_a_multi_valued_key():
    """The single-valued guard still applies when the key comes from the set."""
    multi = _candidate("S1_P00121", topping="花生碎、奶盖")
    other = _candidate("S1_P00122", topping="椰果")
    keyless = CandidateConstraint.require("椰果", source="prefer")
    vocabulary = build_vocabulary([multi, other])
    assert (
        correspondence_for_candidate(multi, [keyless], vocabulary)[0].status
        is ConstraintStatus.UNKNOWN
    )


def test_vocabulary_never_turns_a_forbid_into_satisfied():
    """The polarity rule is unaffected by the vocabulary mechanism."""
    forbidden = _candidate("S1_P00131", topping="花生碎")
    forbid = CandidateConstraint.forbid("花生碎", hard=True)
    vocabulary = build_vocabulary([forbidden])
    assert (
        correspondence_for_candidate(forbidden, [forbid], vocabulary)[0].status
        is ConstraintStatus.VIOLATED
    )


def test_render_is_bounded():
    candidate = _candidate("S1_P00052", temperature="热饮")
    constraints = [
        CandidateConstraint.require(f"value-{i}", attribute_key="k") for i in range(80)
    ]
    assert len(render_correspondence(candidate, constraints, max_chars=100)) <= 100


def test_render_block_is_bounded_and_skips_empty():
    candidates = [_candidate(f"S1_P0006{i}", temperature="热饮") for i in range(40)]
    constraints = [CandidateConstraint.require("热饮", attribute_key="temperature")]
    block = render_correspondence_block(candidates, constraints, max_chars=300)
    assert len(block) <= 300
    assert render_correspondence_block(candidates, [], max_chars=300) == ""
    assert render_correspondence_block([], constraints, max_chars=300) == ""


# ---------------------------------------------------------------------------
# Numeric ranges (the user's pre-registered validation set, item 4)
# ---------------------------------------------------------------------------


def test_numeric_bound_satisfied_and_violated():
    cheap = Candidate(
        candidate_id="S1_P00071", entity_type="product", name="a", raw="a",
        tool_name="t", attributes={"price": "18"}, price=18.0,
    )
    dear = Candidate(
        candidate_id="S1_P00072", entity_type="product", name="b", raw="b",
        tool_name="t", attributes={"price": "88"}, price=88.0,
    )
    budget = CandidateConstraint.at_most("预算", 30.0, attribute_key="price")
    assert _status(cheap, budget) is ConstraintStatus.SATISFIED
    assert _status(dear, budget) is ConstraintStatus.VIOLATED


def test_numeric_bound_without_a_printed_number_is_unknown():
    no_price = Candidate(
        candidate_id="S1_P00073", entity_type="product", name="c", raw="c",
        tool_name="t", attributes={"size": "大杯"},
    )
    budget = CandidateConstraint.at_most("预算", 30.0, attribute_key="price")
    assert _status(no_price, budget) is ConstraintStatus.UNKNOWN


def test_numeric_forbid_does_not_become_satisfied():
    """Absence of a printed number must never read as 'clean'."""
    candidate = Candidate(
        candidate_id="S1_P00074", entity_type="product", name="d", raw="d",
        tool_name="t", attributes={},
    )
    odd = CandidateConstraint(
        value="预算", polarity=ConstraintPolarity.FORBID, numeric_max=30.0,
        attribute_key="price",
    )
    assert _status(candidate, odd) is ConstraintStatus.UNKNOWN
    assert _status(candidate, odd) is not ConstraintStatus.SATISFIED


def test_numeric_min_bound():
    candidate = Candidate(
        candidate_id="S1_P00075", entity_type="product", name="e", raw="e",
        tool_name="t", attributes={"quantity": "3"},
    )
    at_least_five = CandidateConstraint.at_least("库存", 5.0, attribute_key="quantity")
    assert _status(candidate, at_least_five) is ConstraintStatus.VIOLATED


# ---------------------------------------------------------------------------
# Parent-child binding (item 5)
# ---------------------------------------------------------------------------


def _child(candidate_id: str, parents: list[str]) -> Candidate:
    return Candidate(
        candidate_id=candidate_id, entity_type="product", name="p", raw="p",
        tool_name="t", attributes={"size": "大杯"}, parent_ids=list(parents),
    )


def test_parent_binding_satisfied_and_violated():
    constraint = CandidateConstraint.under_parent("S1_S00001")
    assert _status(_child("S1_P00081", ["S1_S00001"]), constraint) is ConstraintStatus.SATISFIED
    assert _status(_child("S1_P00082", ["S1_S00002"]), constraint) is ConstraintStatus.VIOLATED


def test_parent_binding_without_observed_parent_is_unknown():
    """A candidate seen with no parent cannot confirm or refute the binding."""
    constraint = CandidateConstraint.under_parent("S1_S00001")
    assert _status(_child("S1_P00083", []), constraint) is ConstraintStatus.UNKNOWN


# ---------------------------------------------------------------------------
# Current correction (item 2) and source precedence
# ---------------------------------------------------------------------------


def test_current_correction_replaces_the_remembered_value():
    constraints = constraints_from_card(DecisionCard(prefer=["热饮"]))
    template = next(c for c in constraints if c.value == "热饮")
    updated = apply_current_correction(constraints, template, "冰饮")
    values = {c.value for c in updated}
    assert "冰饮" in values
    assert "热饮" not in values, "the remembered value must not sit beside the correction"
    corrected = next(c for c in updated if c.value == "冰饮")
    assert corrected.source == "correction"


def test_current_correction_changes_the_verdict():
    constraints = [
        CandidateConstraint.require("热饮", attribute_key="temperature", source="prefer")
    ]
    template = constraints[0]
    hot = _candidate("S1_P00091", temperature="热饮")
    iced = _candidate("S1_P00092", temperature="冰饮")

    assert _status(hot, template) is ConstraintStatus.SATISFIED

    updated = apply_current_correction(constraints, template, "冰饮")
    assert correspondence_for_candidate(hot, updated)[0].status is ConstraintStatus.VIOLATED
    assert correspondence_for_candidate(iced, updated)[0].status is ConstraintStatus.SATISFIED


def test_require_without_an_attribute_key_cannot_be_refuted():
    """Counterexample: with no key we do not know which field this is about.

    Reporting VIOLATED here would assert a contradiction the evidence does not
    support, so the honest answer stays UNKNOWN.
    """
    no_key = CandidateConstraint.require("热饮")
    assert _status(_candidate("S1_P00093", temperature="冰饮"), no_key) is ConstraintStatus.UNKNOWN


def test_source_precedence_prefers_the_current_instruction():
    """A higher-precedence source overturns a lower-precedence one."""
    card = DecisionCard(prefer=["热饮"])
    constraints = constraints_from_card(card)
    assert [c.source for c in constraints if c.value == "热饮"] == ["prefer"]

    # Simulate the instruction requiring the same value with higher precedence.
    merged = constraints + [
        CandidateConstraint.require("热饮", source="instruction")
    ]
    assert any(c.source == "instruction" for c in merged)


def test_apply_current_correction_with_empty_value_is_a_noop():
    constraints = constraints_from_card(DecisionCard(prefer=["热饮"]))
    template = constraints[0]
    assert apply_current_correction(constraints, template, "") == constraints


def test_apply_current_correction_can_add_a_new_condition():
    constraints = constraints_from_card(DecisionCard(prefer=["热饮"]))
    template = CandidateConstraint.require("无糖", attribute_key="sweetness")
    updated = apply_current_correction(constraints, template, "三分糖")
    assert {c.value for c in updated} == {"热饮", "三分糖"}

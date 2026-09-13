"""Zero-model reproduction: candidate/constraint correspondence is not three-valued.

E-062 pre-registered the alignment intervention as "show, for each observed
candidate, how its printed attributes stand against the current instruction and
memory constraints: satisfied / violated / unknown -- without choosing for the
model and without blocking a write."

Before implementing anything, this script establishes the two structural facts
that make that intervention non-trivial. Both are model-free: they construct
fictional candidates with fictional attributes and call the existing alignment
code directly.

A1  "violated" and "unknown" are the same value.
    ``CandidateAttributeMap.matches`` returns a boolean "does this candidate's
    text contain this preference value". A candidate whose printed attribute
    *contradicts* the constraint and a candidate whose relevant attribute was
    never printed both produce no match, so they receive identical alignment
    scores. The model is handed a ranking that cannot distinguish "known bad"
    from "not known".

A2  A negative instruction is atomized as a positive preference.
    ``DecisionCard.alignment_source_records`` feeds ``task_intent`` -- the raw
    current instruction -- into the positive matcher with the highest weight
    (3.0). When the instruction is a prohibition ("不要花生"), the prohibited
    value becomes a positive atom, so a candidate that *contains* the forbidden
    item scores *higher* on alignment. This is the same polarity inversion as
    E-059, one layer down.

Run:
    python scripts/alignment_correspondence_repro.py

Exit code 0. After the intervention lands, this script is the gate for flipping
the engineering-log entry: A1 and A2 are expected to report NOT REPRODUCED.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.decision import Candidate, DecisionCard  # noqa: E402
from agent.runtime.alignment import (  # noqa: E402
    CandidateAttributeMap,
    EvidenceAlignment,
)
from agent.runtime.correspondence import (  # noqa: E402
    CandidateConstraint,
    ConstraintStatus,
    apply_current_correction,
    constraints_from_card,
    correspondence_for_candidate,
    render_correspondence,
)


def _rule(title: str) -> None:
    print()
    print("=" * 74)
    print(title)
    print("=" * 74)


def _alignment(candidates: list[Candidate], card: DecisionCard) -> EvidenceAlignment:
    """Build alignment exactly the way the production ranker does."""
    return EvidenceAlignment(
        candidates,
        card.alignment_source_records(),
        legacy_matcher=lambda value, raw: value in raw,
    )


def repro_a1_violated_equals_unknown() -> bool:
    """A violating candidate and an unknown candidate must not score the same."""
    _rule("A1  'violated' is indistinguishable from 'unknown'")

    want = Candidate(
        candidate_id="S1_P00001",
        entity_type="product",
        name="奶茶甲",
        raw="奶茶甲",
        tool_name="delivery_product_search_recommand",
        attributes={"temperature": "热饮", "size": "大杯"},
    )
    violates = Candidate(
        candidate_id="S1_P00002",
        entity_type="product",
        name="奶茶乙",
        raw="奶茶乙",
        tool_name="delivery_product_search_recommand",
        attributes={"temperature": "冰饮", "size": "大杯"},
    )
    unknown = Candidate(
        candidate_id="S1_P00003",
        entity_type="product",
        name="奶茶丙",
        raw="奶茶丙",
        tool_name="delivery_product_search_recommand",
        attributes={"size": "大杯"},
    )

    card = DecisionCard(prefer=["热饮"], task_intent=[])
    alignment = _alignment([want, violates, unknown], card)

    print("constraint under test: 热饮 (positive)")
    for label, candidate in (("satisfies", want), ("violates", violates), ("unknown", unknown)):
        attributes = CandidateAttributeMap.from_candidate(candidate).attributes
        printed = ", ".join(f"{a.key}={a.value}" for a in attributes)
        print(
            f"  {label:<10} {candidate.candidate_id}  printed[{printed}]  "
            f"matches={tuple(a.value for a in alignment.matches(candidate))}  "
            f"score={alignment.score(candidate)}"
        )

    violation_score = alignment.score(violates)
    unknown_score = alignment.score(unknown)
    print()
    print(f"  score(violates) == score(unknown) : {violation_score == unknown_score}")
    print("  -> 'known bad' and 'not known' are the same value to the model")

    reproduced = violation_score == unknown_score
    print()
    print(f"A1 REPRODUCED: {reproduced}")
    return reproduced


def repro_a2_negative_instruction_scores_positive() -> bool:
    """A candidate containing the forbidden item must not score higher.

    The prohibition has to name the value the way the tools print it for the
    alignment machinery to activate at all -- "不要花生" induces no atom when the
    catalog says "花生碎", because atomization is driven by the observed
    candidate vocabulary. So the faithful form is "不要花生碎". That the
    machinery activates only on exact observed spellings is itself part of the
    finding, not a trick of the test.
    """
    _rule("A2  a prohibition in the current instruction becomes a positive match")

    clean = Candidate(
        candidate_id="S1_P00011",
        entity_type="product",
        name="奶盖茶",
        raw="奶盖茶",
        tool_name="delivery_product_search_recommand",
        attributes={"topping": "奶盖", "temperature": "热饮"},
    )
    forbidden = Candidate(
        candidate_id="S1_P00012",
        entity_type="product",
        name="花生奶盖茶",
        raw="花生奶盖茶",
        tool_name="delivery_product_search_recommand",
        attributes={"topping": "花生碎", "temperature": "热饮"},
    )

    card = DecisionCard(task_intent=["帮我点一杯奶茶，不要花生碎"])
    alignment = _alignment([clean, forbidden], card)

    print(f"instruction: {card.task_intent[0]}")
    print(
        "alignment atoms induced: "
        f"{sorted({(a.attribute_key, a.value, a.weight) for a in alignment.atoms})}"
    )
    print()
    for label, candidate in (("clean", clean), ("contains 花生碎", forbidden)):
        print(
            f"  {label:<18} {candidate.candidate_id}  "
            f"matches={tuple(a.value for a in alignment.matches(candidate))}  "
            f"score={alignment.score(candidate)}"
        )

    forbidden_score = alignment.score(forbidden)
    clean_score = alignment.score(clean)
    print()
    print(f"  score(contains forbidden) = {forbidden_score}")
    print(f"  score(clean)              = {clean_score}")
    print(f"  forbidden candidate scores higher : {forbidden_score > clean_score}")

    reproduced = forbidden_score > clean_score
    print()
    print(f"A2 REPRODUCED: {reproduced}")
    return reproduced


def repro_a3_negatives_absent_from_alignment() -> bool:
    """A durable negative constraint must appear in the alignment view at all."""
    _rule("A3  durable negatives are absent from the alignment view entirely")

    clean = Candidate(
        candidate_id="S1_P00021",
        entity_type="product",
        name="奶盖茶",
        raw="奶盖茶",
        tool_name="delivery_product_search_recommand",
        attributes={"topping": "奶盖"},
    )
    forbidden = Candidate(
        candidate_id="S1_P00022",
        entity_type="product",
        name="花生奶盖茶",
        raw="花生奶盖茶",
        tool_name="delivery_product_search_recommand",
        attributes={"topping": "花生碎"},
    )

    # This is the shape build_decision_card produces for an allergy: the value
    # lives in card.avoid and in card.constraints, not in the preference pool.
    card = DecisionCard(avoid=["花生碎"], task_intent=[])
    alignment = _alignment([clean, forbidden], card)

    print(f"card.avoid = {card.avoid}")
    print(
        "alignment atoms induced: "
        f"{sorted({(a.attribute_key, a.value) for a in alignment.atoms})}"
    )
    print(f"alignment.preferences fed in: {list(card.alignment_preferences())}")
    print()
    for label, candidate in (("clean", clean), ("contains 花生碎", forbidden)):
        print(
            f"  {label:<18} {candidate.candidate_id}  "
            f"matches={tuple(a.value for a in alignment.matches(candidate))}  "
            f"score={alignment.score(candidate)}"
        )

    absent = not alignment.atoms
    blind = alignment.score(forbidden) == alignment.score(clean)
    print()
    print(f"  negative constraint produces no atom : {absent}")
    print(f"  violating == clean in the view       : {blind}")

    reproduced = absent and blind
    print()
    print(f"A3 REPRODUCED: {reproduced}")
    return reproduced


def repro_b_three_valued_module() -> bool:
    """The replacement must answer all three cases correctly.

    Returns True when the new module is correct (i.e. nothing reproduces).
    """
    _rule("B   three-valued correspondence (agent/runtime/correspondence.py)")

    satisfies = Candidate(
        candidate_id="S1_P00001", entity_type="product", name="奶茶甲", raw="奶茶甲",
        tool_name="delivery_product_search_recommand",
        attributes={"temperature": "热饮", "size": "大杯"},
    )
    violates = Candidate(
        candidate_id="S1_P00002", entity_type="product", name="奶茶乙", raw="奶茶乙",
        tool_name="delivery_product_search_recommand",
        attributes={"temperature": "冰饮", "size": "大杯"},
    )
    unknown = Candidate(
        candidate_id="S1_P00003", entity_type="product", name="奶茶丙", raw="奶茶丙",
        tool_name="delivery_product_search_recommand",
        attributes={"size": "大杯"},
    )
    require = CandidateConstraint.require("热饮", attribute_key="temperature")

    print("B1  violated vs unknown are now distinct")
    for label, candidate in (("satisfies", satisfies), ("violates", violates), ("unknown", unknown)):
        status = correspondence_for_candidate(candidate, [require])[0].status
        print(f"    {label:<10} -> {status.value}")

    clean = Candidate(
        candidate_id="S1_P00011", entity_type="product", name="奶盖茶", raw="奶盖茶",
        tool_name="delivery_product_search_recommand",
        attributes={"topping": "奶盖", "temperature": "热饮"},
    )
    forbidden = Candidate(
        candidate_id="S1_P00012", entity_type="product", name="花生奶盖茶", raw="花生奶盖茶",
        tool_name="delivery_product_search_recommand",
        attributes={"topping": "花生碎", "temperature": "热饮"},
    )
    forbid = CandidateConstraint.forbid("花生碎")

    print()
    print("B2  a prohibition can only be violated or unknown, never satisfied")
    for label, candidate in (("clean", clean), ("contains 花生碎", forbidden)):
        status = correspondence_for_candidate(candidate, [forbid])[0].status
        print(f"    {label:<18} -> {status.value}")

    card = DecisionCard(avoid=["花生碎"])
    from_card = constraints_from_card(card)
    print()
    print("B3  a durable negative survives into the view")
    print(f"    card.avoid={card.avoid} -> constraints={[(c.value, c.polarity.value) for c in from_card]}")
    for label, candidate in (("clean", clean), ("contains 花生碎", forbidden)):
        status = correspondence_for_candidate(candidate, from_card)[0].status
        print(f"    {label:<18} -> {status.value}")

    print()
    print("    rendered view for the violating candidate:")
    print("      " + render_correspondence(forbidden, from_card + [require]))

    b1 = (
        correspondence_for_candidate(satisfies, [require])[0].status is ConstraintStatus.SATISFIED
        and correspondence_for_candidate(violates, [require])[0].status is ConstraintStatus.VIOLATED
        and correspondence_for_candidate(unknown, [require])[0].status is ConstraintStatus.UNKNOWN
    )
    b2 = all(
        correspondence_for_candidate(candidate, [forbid])[0].status
        is not ConstraintStatus.SATISFIED
        for candidate in (clean, forbidden)
    ) and (
        correspondence_for_candidate(forbidden, [forbid])[0].status is ConstraintStatus.VIOLATED
    )
    b3 = bool(from_card) and (
        correspondence_for_candidate(forbidden, from_card)[0].status
        is not correspondence_for_candidate(clean, from_card)[0].status
    )

    # --- the remaining items of the pre-registered validation set -----------
    print()
    print("B4  numeric range")
    cheap = Candidate(
        candidate_id="S1_P00021", entity_type="product", name="a", raw="a",
        tool_name="t", attributes={"price": "18"}, price=18.0,
    )
    dear = Candidate(
        candidate_id="S1_P00022", entity_type="product", name="b", raw="b",
        tool_name="t", attributes={"price": "88"}, price=88.0,
    )
    no_price = Candidate(
        candidate_id="S1_P00023", entity_type="product", name="c", raw="c",
        tool_name="t", attributes={"size": "大杯"},
    )
    budget = CandidateConstraint.at_most("预算", 30.0, attribute_key="price")
    numeric_states = {}
    for label, candidate in (("within budget", cheap), ("over budget", dear), ("no price printed", no_price)):
        state = correspondence_for_candidate(candidate, [budget])[0].status.value
        numeric_states[label] = state
        print(f"    {label:<18} -> {state}")

    print()
    print("B5  parent-child binding")
    parent = CandidateConstraint.under_parent("S1_S00001")
    child_ok = Candidate(
        candidate_id="S1_P00031", entity_type="product", name="d", raw="d",
        tool_name="t", attributes={"size": "大杯"}, parent_ids=["S1_S00001"],
    )
    child_other = Candidate(
        candidate_id="S1_P00032", entity_type="product", name="e", raw="e",
        tool_name="t", attributes={"size": "大杯"}, parent_ids=["S1_S00002"],
    )
    child_orphan = Candidate(
        candidate_id="S1_P00033", entity_type="product", name="f", raw="f",
        tool_name="t", attributes={"size": "大杯"}, parent_ids=[],
    )
    parent_states = {}
    for label, candidate in (("under target parent", child_ok), ("under other parent", child_other), ("no parent shown", child_orphan)):
        state = correspondence_for_candidate(candidate, [parent])[0].status.value
        parent_states[label] = state
        print(f"    {label:<20} -> {state}")

    print()
    print("B6  current correction replaces the remembered value")
    remembered = [
        CandidateConstraint.require("热饮", attribute_key="temperature", source="prefer")
    ]
    hot = Candidate(
        candidate_id="S1_P00041", entity_type="product", name="g", raw="g",
        tool_name="t", attributes={"temperature": "热饮"},
    )
    before = correspondence_for_candidate(hot, remembered)[0].status.value
    corrected = apply_current_correction(remembered, remembered[0], "冰饮")
    after = correspondence_for_candidate(hot, corrected)[0].status.value
    print(f"    remembered 需热饮 vs 候选热饮 -> {before}")
    print(f"    after correction to 冰饮      -> {after}   (constraints={[c.value for c in corrected]})")

    b4 = numeric_states["within budget"] == "satisfied" and numeric_states["over budget"] == "violated" and numeric_states["no price printed"] == "unknown"
    b5 = parent_states["under target parent"] == "satisfied" and parent_states["under other parent"] == "violated" and parent_states["no parent shown"] == "unknown"
    b6 = before == "satisfied" and after == "violated" and {c.value for c in corrected} == {"冰饮"}

    print()
    print(f"  B1 three-valued                        : {b1}")
    print(f"  B2 prohibition polarity preserved      : {b2}")
    print(f"  B3 durable negative present and usable : {b3}")
    print(f"  B4 numeric range                       : {b4}")
    print(f"  B5 parent-child binding                : {b5}")
    print(f"  B6 current correction wins             : {b6}")
    correct = b1 and b2 and b3 and b4 and b5 and b6
    print()
    print(f"B CORRECT (no defect reproduced): {correct}")
    return correct


def main() -> int:
    a1 = repro_a1_violated_equals_unknown()
    a2 = repro_a2_negative_instruction_scores_positive()
    a3 = repro_a3_negatives_absent_from_alignment()
    b = repro_b_three_valued_module()

    _rule("SUMMARY")
    print("legacy alignment machinery (no production call site):")
    print(f"  A1 violated==unknown in alignment      : {'REPRODUCED' if a1 else 'not reproduced'}")
    print(f"  A2 prohibition becomes a positive atom : {'REPRODUCED' if a2 else 'not reproduced'}")
    print(f"  A3 negatives absent from alignment     : {'REPRODUCED' if a3 else 'not reproduced'}")
    print()
    print("replacement (agent/runtime/correspondence.py):")
    print(f"  B  three-valued, polarity-preserving   : {'CORRECT' if b else 'DEFECTIVE'}")
    print()
    print("Wiring the legacy machinery back into any scored path would inject A2")
    print("into the ranking layer. The replacement must be used instead.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

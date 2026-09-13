"""Three-valued correspondence between a candidate and the user's constraints.

The design spine says a controller may never assert what it cannot know. The
existing alignment machinery collapses a constraint's status into a boolean
"does the candidate text contain this value", which makes *violated* and
*unknown* the same value, and -- because ``DecisionCard.alignment_source_records``
feeds the raw instruction into the positive matcher -- turns a prohibition into a
positive preference, so a candidate containing the forbidden item scores higher
(E-064, reproduced in ``scripts/alignment_correspondence_repro.py``).

This module is the replacement representation. It is deliberately narrow:

- It is a **pure function**. No model call, no memory mutation, no state.
- It **never chooses** and **never blocks** a write. It only reports, per
  constraint, which of three states a candidate is in.
- Polarity is a property of the *constraint*, never of the field that carries it.
  A ``forbid`` constraint can only ever be ``VIOLATED`` or ``UNKNOWN`` -- absence
  of evidence is not evidence of absence -- and it can never enter a positive
  score.
- A requirement is only ``VIOLATED`` when the candidate prints a *different*
  value for the *same* attribute key and that key holds exactly one value for
  this candidate. Otherwise the honest answer is ``UNKNOWN``.

Nothing here reads evaluator rewards, rubrics, target annotations or benchmark
ids, and the vocabulary is induced from the observed candidate itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Sequence

from agent.runtime.alignment import CandidateAttributeMap, _normalize


def _constraint_usable(value: str) -> bool:
    """Whether a constraint value can be evaluated at all.

    Deliberately weaker than ``alignment._usable``, which requires two
    characters. A one-character constraint is legitimate and common in Chinese
    ("辣", "冰"), and E-061 established that the parser must keep such objects.
    Matching stays conservative: equality, or an observed value extending the
    constraint value, so a single character cannot match by accident.
    """
    if "_" in (value or ""):
        return False
    normalized = _normalize(value)
    return bool(normalized) and not normalized.isdigit()


class ConstraintStatus(str, Enum):
    """What is known about one candidate with respect to one constraint."""

    SATISFIED = "satisfied"
    VIOLATED = "violated"
    UNKNOWN = "unknown"


class ConstraintPolarity(str, Enum):
    REQUIRE = "require"
    FORBID = "forbid"


# Precedence follows the repository's fixed decision order: the current
# instruction outranks a current-conversation correction, which outranks a
# durable negative, which outranks a stable preference. Used only to resolve two
# constraints that disagree about the same value -- never to invent a value.
SOURCE_PRECEDENCE: dict[str, int] = {
    "instruction": 5,
    "correction": 4,
    "constraint": 3,
    "avoid": 3,
    "memory": 2,
    "prefer": 1,
}


@dataclass(frozen=True)
class CandidateConstraint:
    """One user condition, with the polarity carried by the condition itself.

    Three relation types are supported, because they need different evidence:

    * textual (default) -- the value appears in the candidate's printed
      attributes;
    * numeric (``numeric_max`` / ``numeric_min``) -- requires a printed number
      for ``attribute_key``; without one the honest answer is UNKNOWN;
    * parent binding (``parent_must_include``) -- requires the candidate to have
      been observed as a child of that parent.
    """

    value: str
    polarity: ConstraintPolarity
    attribute_key: str = ""
    dimension: str = ""
    source: str = ""
    numeric_max: float | None = None
    numeric_min: float | None = None
    parent_must_include: str = ""
    hard: bool = False
    """Whether the condition is a graded requirement of the current instruction.

    Hard constraints come from the instruction (its prohibitions and its
    candidate-targeted requirements) and from durable safety facts. Soft ones
    come from remembered positive preferences, which the model may weigh or
    ignore. The distinction is carried, not flattened, because counting a soft
    preference as a requirement floods the view with ``UNKNOWN`` pairs and
    buries the handful of real conflicts (E-066).
    """

    @classmethod
    def require(cls, value: str, **kwargs: Any) -> "CandidateConstraint":
        return cls(value=value, polarity=ConstraintPolarity.REQUIRE, **kwargs)

    @classmethod
    def forbid(cls, value: str, **kwargs: Any) -> "CandidateConstraint":
        return cls(value=value, polarity=ConstraintPolarity.FORBID, **kwargs)

    @classmethod
    def at_most(cls, value: str, bound: float, **kwargs: Any) -> "CandidateConstraint":
        return cls(
            value=value,
            polarity=ConstraintPolarity.REQUIRE,
            numeric_max=bound,
            **kwargs,
        )

    @classmethod
    def at_least(cls, value: str, bound: float, **kwargs: Any) -> "CandidateConstraint":
        return cls(
            value=value,
            polarity=ConstraintPolarity.REQUIRE,
            numeric_min=bound,
            **kwargs,
        )

    @classmethod
    def under_parent(
        cls, parent_id: str, **kwargs: Any
    ) -> "CandidateConstraint":
        return cls(
            value=parent_id,
            polarity=ConstraintPolarity.REQUIRE,
            parent_must_include=parent_id,
            **kwargs,
        )


@dataclass(frozen=True)
class Correspondence:
    """The status of one constraint against one candidate."""

    constraint: CandidateConstraint
    status: ConstraintStatus
    observed_key: str = ""
    observed_value: str = ""


def _attributes(candidate: Any) -> CandidateAttributeMap:
    # min_chars=1: a one-character attribute is real evidence, and excluding it
    # would make a one-character constraint permanently unknowable.
    return CandidateAttributeMap.from_candidate(candidate, min_chars=1)


def _matching_attributes(
    attribute_map: CandidateAttributeMap, value: str
) -> list[Any]:
    """Attributes whose printed value is the constraint value.

    The rule matches ``CandidateAttributeMap.matches``: normalized equality, or
    the observed value extending the constraint value. It is duplicated here
    rather than called so that the polarity logic below cannot be reintroduced
    into the shared matcher by accident.
    """
    target = _normalize(value)
    if not target:
        return []
    matched = []
    for attribute in attribute_map.attributes:
        observed = _normalize(attribute.value)
        if observed == target or (len(target) >= 2 and observed.startswith(target)):
            matched.append(attribute)
    return matched


def _single_valued_keys(attribute_map: CandidateAttributeMap) -> set[str]:
    """Attribute keys this candidate prints exactly one distinct value for.

    Only for these can a *different* printed value be read as a contradiction.
    A key carrying several values (a topping list, a tag list) cannot: one
    printed value does not exclude the others.
    """
    seen: dict[str, set[str]] = {}
    for attribute in attribute_map.attributes:
        seen.setdefault(attribute.key, set()).add(_normalize(attribute.value))
    return {key for key, values in seen.items() if len(values) == 1}


def _numeric_attribute(candidate: Any, key: str) -> float | None:
    """A printed number for ``key``, or None when the candidate shows none.

    ``CandidateAttributeMap`` deliberately drops numeric values (they are not
    reusable textual evidence), so numeric constraints read the candidate's own
    fields directly. Price is carried as a first-class field, not an attribute.
    """
    attributes = getattr(candidate, "attributes", {}) or {}
    if key in {"price", "budget"}:
        price = getattr(candidate, "price", None)
        if price is not None:
            try:
                return float(price)
            except (TypeError, ValueError):
                pass
    raw = attributes.get(key)
    if raw is None:
        return None
    text = str(raw)
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _numeric_status(
    candidate: Any, constraint: CandidateConstraint
) -> ConstraintStatus | None:
    """Status for a numeric constraint, or None when it is not a numeric one."""
    if constraint.numeric_max is None and constraint.numeric_min is None:
        return None
    if constraint.polarity != ConstraintPolarity.REQUIRE:
        # A prohibition expressed as a numeric bound has no defined semantics
        # here: absence of a printed number would be read as "clean", which is
        # exactly the absent-evidence flaw this module exists to avoid.
        return ConstraintStatus.UNKNOWN
    observed = _numeric_attribute(candidate, constraint.attribute_key)
    if observed is None:
        return ConstraintStatus.UNKNOWN
    if constraint.numeric_max is not None and observed > constraint.numeric_max:
        return ConstraintStatus.VIOLATED
    if constraint.numeric_min is not None and observed < constraint.numeric_min:
        return ConstraintStatus.VIOLATED
    return ConstraintStatus.SATISFIED


def _parent_status(
    candidate: Any, constraint: CandidateConstraint
) -> ConstraintStatus | None:
    """Status for a parent-binding constraint, or None when it is not one."""
    if not constraint.parent_must_include:
        return None
    parents = [str(value) for value in (getattr(candidate, "parent_ids", []) or [])]
    if not parents:
        # The candidate was observed without a parent, so the binding cannot be
        # affirmed or refuted from what was printed.
        return ConstraintStatus.UNKNOWN
    if constraint.parent_must_include in parents:
        return ConstraintStatus.SATISFIED
    if constraint.polarity == ConstraintPolarity.REQUIRE:
        return ConstraintStatus.VIOLATED
    return ConstraintStatus.UNKNOWN


def build_vocabulary(candidates: Iterable[Any]) -> dict[str, str]:
    """Induce ``normalized value -> attribute key`` from the candidate set.

    This is how a constraint acquires an attribute key without a domain lexicon.
    A remembered value such as 热饮 may not say which field it belongs to, but if
    any candidate in the current set prints ``temperature=热饮``, the set itself
    tells us the field is ``temperature`` -- and then a candidate printing
    ``temperature=冰饮`` can be refuted instead of merely left UNKNOWN.

    The vocabulary is candidate-set local and open-world: no product schema, no
    benchmark id, no synonym table. First observation wins, deterministically.
    """
    vocabulary: dict[str, str] = {}
    for candidate in candidates:
        for attribute in _attributes(candidate).attributes:
            normalized = _normalize(attribute.value)
            if normalized and normalized not in vocabulary:
                vocabulary[normalized] = attribute.key
    return vocabulary


def correspondence_for_candidate(
    candidate: Any,
    constraints: Iterable[CandidateConstraint],
    vocabulary: dict[str, str] | None = None,
) -> list[Correspondence]:
    """Report each constraint's status against one candidate. Pure.

    ``vocabulary`` (see :func:`build_vocabulary`) lets a requirement borrow the
    attribute key its value was observed under elsewhere in the same candidate
    set, which is what makes refutation possible at all.
    """
    attribute_map = _attributes(candidate)
    single_valued = _single_valued_keys(attribute_map)
    results: list[Correspondence] = []

    for constraint in constraints:
        numeric = _numeric_status(candidate, constraint)
        if numeric is not None:
            observed = _numeric_attribute(candidate, constraint.attribute_key)
            results.append(
                Correspondence(
                    constraint,
                    numeric,
                    observed_key=constraint.attribute_key,
                    observed_value="" if observed is None else f"{observed:g}",
                )
            )
            continue

        parent = _parent_status(candidate, constraint)
        if parent is not None:
            results.append(Correspondence(constraint, parent))
            continue

        if not _constraint_usable(constraint.value):
            results.append(Correspondence(constraint, ConstraintStatus.UNKNOWN))
            continue

        matched = _matching_attributes(attribute_map, constraint.value)
        if matched:
            # A match on a requirement is satisfaction; a match on a
            # prohibition is a violation. The polarity decides, not the field.
            status = (
                ConstraintStatus.VIOLATED
                if constraint.polarity == ConstraintPolarity.FORBID
                else ConstraintStatus.SATISFIED
            )
            results.append(
                Correspondence(
                    constraint,
                    status,
                    observed_key=matched[0].key,
                    observed_value=matched[0].value,
                )
            )
            continue

        # No match. A requirement can only be refuted by a different value on
        # the same single-valued key; anything else is honestly unknown. The key
        # may come from the constraint itself or from the candidate set's own
        # induced vocabulary.
        if constraint.polarity == ConstraintPolarity.REQUIRE:
            key = constraint.attribute_key or (vocabulary or {}).get(
                _normalize(constraint.value), ""
            )
            if key and key in single_valued:
                others = [
                    attribute
                    for attribute in attribute_map.attributes
                    if attribute.key == key
                    and _normalize(attribute.value) != _normalize(constraint.value)
                ]
                if others:
                    results.append(
                        Correspondence(
                            constraint,
                            ConstraintStatus.VIOLATED,
                            observed_key=others[0].key,
                            observed_value=others[0].value,
                        )
                    )
                    continue

        results.append(Correspondence(constraint, ConstraintStatus.UNKNOWN))

    return results


def violated_constraints(
    candidate: Any,
    constraints: Iterable[CandidateConstraint],
    vocabulary: dict[str, str] | None = None,
) -> list[Correspondence]:
    """Only the violations, for callers that need to withhold an action."""
    return [
        item
        for item in correspondence_for_candidate(candidate, constraints, vocabulary)
        if item.status == ConstraintStatus.VIOLATED
    ]


def constraints_from_card(card: Any) -> list[CandidateConstraint]:
    """Build constraints from an existing DecisionCard without inventing any.

    Four sources, in the card's own vocabulary:

    * ``card.constraints`` with ``excludes`` -> a hard prohibition;
    * ``card.constraints`` that target a CANDIDATE with ``equals``/``contains``
      -> a hard requirement. These are the graded conditions the instruction
      actually states ("要大床的", "上午的高铁票"). Omitting them was the defect
      behind E-066's apparent 4% coverage: the conditions were compiled all
      along, into ``card.constraints``, and simply were not read here;
    * ``card.avoid`` -> a hard prohibition (durable negatives included);
    * ``card.prefer`` -> a **soft** requirement. ``build_decision_card`` appends
      every relevant decision-eligible positive fact here, so these are
      remembered preferences, not graded conditions.

    Explicit polarity is preserved: a forbidden value never becomes a
    requirement just because it appeared in the instruction text (the E-064 A2
    defect). ``hard`` is carried through so soft preferences can be counted
    separately from graded conditions.
    """
    constraints: list[CandidateConstraint] = []
    best: dict[tuple[str, str], int] = {}

    def add(
        value: str,
        polarity: ConstraintPolarity,
        source: str,
        *,
        hard: bool = False,
        attribute_key: str = "",
    ) -> None:
        text = (value or "").strip()
        if not text:
            return
        rank = SOURCE_PRECEDENCE.get(source, 0)
        # A value may be required or forbidden, but not both at once.
        for other in (ConstraintPolarity.REQUIRE, ConstraintPolarity.FORBID):
            key = (text, other.value)
            if key in best and best[key] >= rank:
                return
            if key in best and best[key] < rank:
                constraints[:] = [
                    item
                    for item in constraints
                    if not (item.value == text and item.polarity == other)
                ]
                del best[key]
        constraints.append(
            CandidateConstraint(
                value=text,
                polarity=polarity,
                attribute_key=attribute_key,
                source=source,
                hard=hard,
            )
        )
        best[(text, polarity.value)] = rank

    for constraint in getattr(card, "constraints", ()) or ():
        operator = str(
            getattr(
                getattr(constraint, "operator", ""),
                "value",
                getattr(constraint, "operator", ""),
            )
        )
        target = str(
            getattr(
                getattr(constraint, "target", ""),
                "value",
                getattr(constraint, "target", ""),
            )
        )
        value = str(getattr(constraint, "value", "") or "")
        if operator == "excludes":
            add(value, ConstraintPolarity.FORBID, "constraint", hard=True)
        elif target == "candidate" and operator in {"equals", "contains"}:
            # Carry the typed slot when the compiler recorded one: it is what
            # lets this requirement be refuted, not merely confirmed (E-067).
            add(
                value,
                ConstraintPolarity.REQUIRE,
                "instruction",
                hard=True,
                attribute_key=str(getattr(constraint, "attribute_key", "") or ""),
            )
    for value in getattr(card, "avoid", ()) or ():
        add(value, ConstraintPolarity.FORBID, "avoid", hard=True)
    for value in getattr(card, "prefer", ()) or ():
        add(value, ConstraintPolarity.REQUIRE, "prefer", hard=False)
    return constraints


def apply_current_correction(
    constraints: Sequence[CandidateConstraint],
    template: CandidateConstraint,
    new_value: str,
) -> list[CandidateConstraint]:
    """Return constraints with ``template`` replaced by a current correction.

    This is the "current correction" half of the binding contract: the same
    condition re-stated in the current conversation replaces the remembered
    value instead of sitting beside it. Purely a list rewrite -- it asserts
    nothing about the candidate.
    """
    text = (new_value or "").strip()
    if not text:
        return list(constraints)
    replaced = False
    out: list[CandidateConstraint] = []
    for item in constraints:
        if not replaced and item.value == template.value and item.polarity == template.polarity:
            out.append(
                CandidateConstraint(
                    value=text,
                    polarity=item.polarity,
                    attribute_key=item.attribute_key,
                    dimension=item.dimension,
                    source="correction",
                    numeric_max=item.numeric_max,
                    numeric_min=item.numeric_min,
                    parent_must_include=item.parent_must_include,
                    hard=item.hard,
                )
            )
            replaced = True
            continue
        out.append(item)
    if not replaced:
        out.append(
            CandidateConstraint(
                value=text,
                polarity=template.polarity,
                attribute_key=template.attribute_key,
                dimension=template.dimension,
                source="correction",
            )
        )
    return out


def render_correspondence(
    candidate: Any,
    constraints: Iterable[CandidateConstraint],
    *,
    vocabulary: dict[str, str] | None = None,
    max_chars: int = 240,
) -> str:
    """Render one candidate's correspondence as a bounded, non-directive line.

    The labels report state only. They do not recommend, rank or forbid: the
    model still decides, and nothing here blocks a write.
    """
    items = correspondence_for_candidate(candidate, constraints, vocabulary)
    labels = {
        ConstraintStatus.SATISFIED: "满足",
        ConstraintStatus.VIOLATED: "冲突",
        ConstraintStatus.UNKNOWN: "未知",
    }
    parts: list[str] = []
    for item in items:
        if not item.constraint.hard and item.status == ConstraintStatus.UNKNOWN:
            # A remembered soft preference the candidate does not print is not
            # worth a line: it is the majority of the output and it carries no
            # discriminating information (E-066).
            continue
        polarity = "需" if item.constraint.polarity == ConstraintPolarity.REQUIRE else "忌"
        prefix = "" if item.constraint.hard else "软"
        text = f"{prefix}{polarity}{item.constraint.value}={labels[item.status]}"
        if item.status == ConstraintStatus.VIOLATED and item.observed_value:
            text += f"(实见{item.observed_value})"
        parts.append(text)
    if not parts:
        return ""
    candidate_id = str(getattr(candidate, "candidate_id", ""))
    rendered = f"{candidate_id}: " + " | ".join(parts)
    return rendered[:max_chars]


def render_correspondence_block(
    candidates: Sequence[Any],
    constraints: Iterable[CandidateConstraint],
    *,
    vocabulary: dict[str, str] | None = None,
    max_candidates: int = 12,
    max_chars: int = 1200,
) -> str:
    """Render a bounded block of per-candidate correspondence lines."""
    constraint_list = list(constraints)
    if not constraint_list or not candidates:
        return ""
    if vocabulary is None:
        vocabulary = build_vocabulary(candidates)
    lines: list[str] = []
    for candidate in candidates[:max_candidates]:
        line = render_correspondence(
            candidate, constraint_list, vocabulary=vocabulary
        )
        if line:
            lines.append(line)
    if not lines:
        return ""
    block = "\n".join(lines)
    return block[:max_chars]

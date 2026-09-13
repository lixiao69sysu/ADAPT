"""Zero-model reproduction of two information-expression defects.

Both defects are independent of any model call, so this script is a complete
evidence unit: it needs no API key, no benchmark run and no network.

D1  The no-query read path asserts polarity it does not know.
    ``ADAPTMemory.read()`` with no query renders every active fact as
    ``PREFER: <value>`` regardless of ``fact.polarity``.  A memory whose only
    relevant entry is the durable safety fact "花生, negative" is therefore
    presented to the model as ``PREFER: 花生``.  ``read_preference_memory``
    (the agent-callable tool) calls exactly this path.

D2  Decision-card rendering drops hard constraints by position.
    ``DecisionCard.render()`` keeps at most the first 3 MUST and the first 2
    AVOID entries.  With three taboos and four MUST constraints the third taboo
    and the fourth hard condition never reach the model, and no later stage can
    recover them because they were never rendered.

Run:
    python scripts/information_expression_repro.py

Exit code 0 means both defects are present as described (the script's purpose
is to witness them).  After the fixes land, this script is expected to report
NOT REPRODUCED, and it is the gate for flipping the engineering-log entries.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.decision import (  # noqa: E402
    Constraint,
    ConstraintOperator,
    ConstraintTarget,
    DecisionCard,
)
from agent.memory.adapt_memory import ADAPTMemory  # noqa: E402


def _rule(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def repro_d1_polarity() -> bool:
    """A safety fact must not be rendered as a preference."""
    _rule("D1  no-query read path: negative fact rendered as PREFER")

    memory = ADAPTMemory(top_k=60)
    memory.update([
        {
            "date": "2026-03-01",
            "behavior": [],
            "dialogue": [{"role": "user", "content": "我对花生过敏。"}],
        }
    ])

    stored = [
        (fact.value, fact.polarity, fact.dimension, fact.status)
        for fact in memory.facts
        if "花生" in fact.value
    ]
    print("stored facts mentioning 花生 (value, polarity, dimension, status):")
    for row in stored:
        print(f"  {row}")

    no_query = memory.read()
    print()
    print("read() with NO query  ->  this is what read_preference_memory returns:")
    print("  " + repr(no_query))

    with_query = memory.read("帮我推荐点吃的")
    print()
    print("read('帮我推荐点吃的')  ->  the queried Decision Card path:")
    for line in with_query.splitlines():
        print("  " + line)

    negative_stored = any(row[1] == "negative" for row in stored)
    leaked_as_prefer = "PREFER" in no_query.upper() and "花生" in no_query
    withheld = "花生" not in with_query or "AVOID" in with_query.upper()

    print()
    print(f"  fact stored with polarity=negative : {negative_stored}")
    print(f"  no-query path labels it PREFER     : {leaked_as_prefer}")
    print(f"  queried card path withholds/AVOIDs : {withheld}")

    reproduced = negative_stored and leaked_as_prefer
    print()
    print(f"D1 REPRODUCED: {reproduced}")
    return reproduced


def repro_d2_truncation() -> bool:
    """The 3rd taboo and the 4th MUST must survive rendering."""
    _rule("D2  decision-card render: hard constraints dropped by position")

    taboos = ["花生", "香菜", "内脏"]
    musts = ["category=咖啡", "size=大杯", "temperature=热饮", "sweetness=无糖"]

    card = DecisionCard(
        must=list(musts),
        avoid=list(taboos),
        constraints=[
            *[
                Constraint(
                    "negative",
                    value,
                    ConstraintTarget.CANDIDATE,
                    ConstraintOperator.EXCLUDES,
                )
                for value in taboos
            ],
            *[
                Constraint(
                    "attribute",
                    value,
                    ConstraintTarget.CANDIDATE,
                    ConstraintOperator.EQUALS,
                )
                for value in musts
            ],
        ],
    )

    rendered = card.render()
    print("input taboos (3):", taboos)
    print("input MUST   (4):", musts)
    print()
    print("rendered card:")
    for line in rendered.splitlines():
        print("  " + line)

    lost_avoid = [value for value in taboos if value not in rendered]
    lost_must = [value for value in musts if value not in rendered]
    print()
    print(f"  taboos absent from render  : {lost_avoid}")
    print(f"  MUST absent from render    : {lost_must}")
    print(f"  card.constraints intact    : {len(card.constraints)} constraints retained in the object")

    reproduced = bool(lost_avoid or lost_must)
    print()
    print(f"D2 REPRODUCED: {reproduced}")
    return reproduced


def repro_d3_clause_crossing() -> bool:
    """A positive clause must not swallow the negative clause after it."""
    _rule("D3  dialogue parse: one clause swallowing the next")

    memory = ADAPTMemory(top_k=60)
    memory.update([
        {
            "date": "2026-03-01",
            "behavior": [],
            "dialogue": [
                {"role": "user", "content": "我喜欢吃香菜，但是我对花生过敏。"}
            ],
        }
    ])

    facts = [(fact.value, fact.polarity, fact.dimension) for fact in memory.facts]
    print("extracted facts (value, polarity, dimension):")
    for row in facts:
        print(f"  {row}")

    print()
    print("read() with NO query:")
    for line in memory.read().splitlines():
        print("  " + line)

    crossing = any(
        value == "香菜，但是我对花生过敏" or ("但是" in value) for value, _p, _d in facts
    )
    peanut_safety = any(
        value == "花生" and polarity == "negative" and dimension == "safety"
        for value, polarity, dimension in facts
    )
    coriander_like = any(
        value == "香菜" and polarity == "positive" for value, polarity, _d in facts
    )

    print()
    print(f"  an object spans the clause boundary : {crossing}")
    print(f"  花生 kept as negative/safety        : {peanut_safety}")
    print(f"  香菜 kept as its own positive like  : {coriander_like}")

    reproduced = crossing or not peanut_safety
    print()
    print(f"D3 REPRODUCED: {reproduced}")
    return reproduced


def main() -> int:
    d1 = repro_d1_polarity()
    d2 = repro_d2_truncation()
    d3 = repro_d3_clause_crossing()

    _rule("SUMMARY")
    print(f"D1 polarity loss in no-query read path : {'REPRODUCED' if d1 else 'not reproduced'}")
    print(f"D2 positional truncation of hard facts : {'REPRODUCED' if d2 else 'not reproduced'}")
    print(f"D3 clause-crossing preference object   : {'REPRODUCED' if d3 else 'not reproduced'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Probe: what structured context does the proactive policy already have?

Zero-model. For a handful of instructions that contain no dataset entity, print
the compiled TaskSpec fields plus the memory slots resolved from structured
facts. This decides whether a gap-driven question policy can be built from
information the compiler already produces, instead of from topic keywords.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.decision import TaskSpec, build_decision_card  # noqa: E402
from agent.memory.adapt_memory import ADAPTMemory  # noqa: E402
from agent.memory.slots import resolve_preference_slots  # noqa: E402

CASES = [
    ("帮我推荐一本书", None),
    ("帮我预约理发", None),
    ("帮我买张出行票", "我不喜欢高铁，我喜欢坐飞机。"),
    ("帮我订个酒店", None),
    ("帮我点个外卖", None),
    ("买张去上海的高铁票", None),
    ("推荐几家在中山广场的店", None),
    ("我想喝杯咖啡提神", None),
]


def main() -> int:
    for instruction, dialogue in CASES:
        memory = ADAPTMemory()
        if dialogue:
            memory.update([
                {
                    "date": "2026-03-01",
                    "behavior": [],
                    "dialogue": [{"role": "user", "content": dialogue}],
                }
            ])
        spec = TaskSpec.compile(instruction)
        card = build_decision_card(spec, memory.facts)
        known = resolve_preference_slots(spec, memory.facts)
        print("=" * 74)
        print(f"instruction     : {instruction}")
        if dialogue:
            print(f"memory dialogue : {dialogue}")
        print(f"  domain        : {spec.domain}")
        print(f"  facet         : {spec.facet}")
        print(f"  action        : {spec.action}")
        print(f"  required_slots: {spec.required_slots}")
        print(f"  unknown_slots : {spec.unknown_slots}")
        print(f"  known_slots   : {known}")
        print(f"  card.must     : {card.must}")
        print(f"  card.avoid    : {card.avoid}")
        print(f"  card.prefer   : {card.prefer[:6]}")
        print(f"  structured facts: "
              f"{[(f.value, f.polarity, f.dimension) for f in memory.facts]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

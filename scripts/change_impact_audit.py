"""Zero-model impact audit: where do this session's changes alter the prompt?

The concern this answers: a pile of prompt-affecting changes has accumulated in
the measured path (``--agent stock --memory-type adapt``), and none of them has a
measured effect. Before spending compute on an A/B, establish *where the change
can bite at all* and *how large the change is*. A subtask whose rendered card is
byte-identical before and after cannot be affected by these changes, no matter
how good they are.

Three change classes are simulated against the current code:

1. E-060 rendering -- the old card truncated to ``must[:3]`` / ``avoid[:2]``; the
   current one renders every hard condition. A card with more than three MUST or
   two AVOID entries used to reach the model incomplete.
2. E-076 removals -- 饭 / 外卖 / 酒店 / 汤 used to be emitted as ``category``
   constraints. Their absence is a prompt change (a hint the model used to get).
3. E-079 additions -- typed attribute constraints derived from the shared
   canonical markers are now emitted where the old compiler emitted none.

For each subtask definition it reports whether any of the three applies, and the
character size of the prompt difference. Reads only instructions and the compiler
itself; no model call.

Usage:
    python scripts/change_impact_audit.py
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent.vitabench_bootstrap as bootstrap  # noqa: E402

bootstrap.enable_vitabench_utf8()

from agent.decision import TaskSpec, build_decision_card  # noqa: E402

REMOVED_CATEGORIES = ("饭", "外卖", "酒店", "汤")


def _old_render(card: Any) -> str:
    """The pre-E-060 rendering rule, applied to the current card."""
    must = list(dict.fromkeys(card.must))[:3]
    avoid = list(dict.fromkeys(card.avoid))[:2]
    remaining = max(0, 8 - len(must) - len(avoid))
    ask = list(dict.fromkeys(card.ask))[:1] if card.ask and remaining else []
    prefer = list(dict.fromkeys(card.prefer))[: max(0, remaining - len(ask))]
    sections = []
    for title, values in (("AVOID", avoid), ("MUST", must), ("PREFER", prefer), ("ASK", ask)):
        if values:
            sections.append(f"{title}: " + " | ".join(values))
    return ("\n".join(sections) or "MUST: follow the current instruction")[:1200]


def analyse(checkpoint: dict | None, tasks_by_id: dict[str, Any]) -> dict[str, Any]:
    total = 0
    counts: collections.Counter = collections.Counter()
    chars = collections.Counter()
    examples: list[str] = []
    seen: set[str] = set()

    for task in tasks_by_id.values():
        for subtask in task.subtasks:
            instruction = subtask.instruction or ""
            if instruction in seen:
                continue
            seen.add(instruction)
            total += 1

            spec = TaskSpec.compile(instruction)
            card = build_decision_card(spec, [])

            new_text = card.render()
            old_text = _old_render(card)

            # E-076: category constraints that no longer appear.
            dropped = [c for c in REMOVED_CATEGORIES if c in instruction]
            # E-079: constraints that carry a typed slot key.
            typed = [
                c.value for c in spec.must if getattr(c, "attribute_key", "")
            ]

            touched = False
            if old_text != new_text:
                counts["rendering_changed"] += 1
                chars["rendering"] += abs(len(new_text) - len(old_text))
                dropped_from_render = [
                    value
                    for value in [*card.must, *card.avoid]
                    if value not in old_text
                ]
                chars["conditions_newly_visible"] += sum(
                    len(value) for value in dropped_from_render
                )
                touched = True
            if dropped:
                counts["lost_a_spurious_category"] += 1
                # Size the content removal, not just count it: the rendered MUST
                # line shrinks by the constraint value plus its separator.
                chars["removed_category_chars"] += sum(len(c) + 3 for c in dropped)
                touched = True
            if typed:
                counts["gained_a_typed_constraint"] += 1
                touched = True
            if touched:
                counts["any_prompt_change"] += 1
                if len(examples) < 8:
                    examples.append(
                        f"{instruction[:40]!r} "
                        f"render_diff={len(new_text) - len(old_text):+d} "
                        f"typed={typed[:2]} dropped_cat={dropped}"
                    )

    return {
        "unique_subtask_definitions": total,
        "definitions_whose_prompt_changed": counts["any_prompt_change"],
        "share": round(counts["any_prompt_change"] / total, 4) if total else 0.0,
        "by_change_class": {
            "rendering_no_longer_truncated": counts["rendering_changed"],
            "lost_a_spurious_category_constraint": counts["lost_a_spurious_category"],
            "gained_a_typed_constraint": counts["gained_a_typed_constraint"],
        },
        "prompt_char_delta": {
            "rendering_sections": chars["rendering"],
            "characters_of_conditions_newly_visible": chars[
                "conditions_newly_visible"
            ],
            "characters_of_category_constraints_removed": chars[
                "removed_category_chars"
            ],
        },
        "average_char_delta_per_changed_definition": round(
            (
                chars["removed_category_chars"]
                + chars["conditions_newly_visible"]
            )
            / counts["any_prompt_change"],
            1,
        )
        if counts["any_prompt_change"]
        else 0.0,
        "examples": examples,
        "note": (
            "A definition whose prompt is unchanged cannot be affected by these "
            "changes. This is reach, not effect: it says where a measurement "
            "could detect something, and how big the intervention is."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", default="data/simulations/stock_avg4_8u.json")
    parser.add_argument("--language", default="chinese")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    checkpoint = None
    if os.path.exists(args.checkpoint):
        with open(args.checkpoint, encoding="utf-8") as handle:
            checkpoint = json.load(handle)

    from agent.vitabench_runner import get_tasks

    tasks_by_id = {task.id: task for task in get_tasks(args.language)}
    report = analyse(checkpoint, tasks_by_id)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    for key, value in report.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

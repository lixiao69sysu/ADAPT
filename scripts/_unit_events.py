"""Per-subtask summary of the agent-visible debug sidecar (JSONL).

Usage: python scripts/_unit_events.py data/simulations/iso_R8.jsonl [E057330]
"""

from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    path = Path(sys.argv[1])
    if not path.is_absolute():
        path = ROOT / path
    task_filter = sys.argv[2] if len(sys.argv) > 2 else ""
    index = 0
    current: dict | None = None
    units: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        event = record.get("event")
        if event == "subtask_begin":
            index += 1
            current = {
                "index": index,
                "instruction": (record.get("instruction") or "")[:42],
                "domain": record.get("domain"),
                "facet": record.get("facet"),
                "action": record.get("action"),
                "events": collections.Counter(),
                "rejections": collections.Counter(),
                "dimensions": collections.Counter(),
                "writes": [],
                "settled": [],
                "tools": collections.Counter(),
            }
            units.append(current)
            continue
        if current is None:
            continue
        current["events"][event] += 1
        if event == "preflight_rejected":
            for problem in record.get("problems", []):
                current["rejections"][problem[:58]] += 1
        elif event == "question_committed":
            current["dimensions"][record.get("dimension") or "?"] += 1
        elif event == "tool_proposal":
            role = record.get("role")
            name = record.get("tool")
            if role == "create":
                current["writes"].append(name)
        elif event == "choice_state":
            current["settled"].append(record.get("source") or record.get("settled"))
        elif event == "tool_result":
            current["tools"][record.get("tool") or "?"] += 1

    if not units:
        print("no subtask_begin events found")
        return
    print(f"{path.name}: {len(units)} subtasks")
    for unit in units:
        head = (
            f"--- unit {unit['index']:>2} [{unit['domain']}/{unit['facet']} "
            f"action={unit['action']}] {unit['instruction']}"
        )
        print(head)
        if unit["events"]:
            print(
                "    events: "
                + ", ".join(
                    f"{name}={count}"
                    for name, count in sorted(unit["events"].items())
                    if name
                    in {
                        "preflight_rejected",
                        "question_committed",
                        "question_blocked_promoted",
                        "tool_result",
                        "lesson_recorded",
                        "shortlist_position_diverged",
                        "preference_leader_diverged",
                    }
                )
            )
        if unit["dimensions"]:
            print(f"    questions: {dict(unit['dimensions'])}")
        if unit["writes"]:
            print(f"    create proposals: {len(unit['writes'])}")
        if unit["tools"]:
            top = ", ".join(
                f"{name}={count}" for name, count in unit["tools"].most_common(4)
            )
            print(f"    tool results: {sum(unit['tools'].values())} ({top})")
        if unit["settled"]:
            print(f"    choice settled: {unit['settled']}")
        for problem, count in unit["rejections"].most_common(4):
            print(f"    reject x{count}: {problem}")


if __name__ == "__main__":
    main()

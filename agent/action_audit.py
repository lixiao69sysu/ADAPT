"""Zero-model audit of observable action-routing traces.

This audit compares the action/domain/facet recorded by an older runtime trace
with the current TaskSpec compiler.  It intentionally reads only ADAPT debug
events and emits aggregate counts; it never consumes evaluator or target data.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from agent.decision import TaskSpec


def audit(path: Path) -> dict:
    begins = []
    visible_failures: Counter[str] = Counter()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if event.get("event") == "subtask_begin":
            begins.append(event)
        elif event.get("event") == "lesson_recorded":
            visible_failures[str(event.get("failure_class", "unknown"))] += 1

    transitions: Counter[str] = Counter()
    routing_changes: Counter[str] = Counter()
    invalid_addresses = 0
    users = set()
    for event in begins:
        users.add(str(event.get("user_id", "")))
        old_action = str(event.get("action", ""))
        old_domain = str(event.get("domain", ""))
        old_facet = str(event.get("facet", ""))
        spec = TaskSpec.compile(str(event.get("instruction", "")))
        transitions[f"{old_action}->{spec.action}"] += 1
        if old_domain != spec.domain:
            routing_changes["domain"] += 1
        if old_facet != spec.facet:
            routing_changes["facet"] += 1
        for constraint in spec.must:
            if constraint.kind == "address" and constraint.value in {
                "就行",
                "公司来",
                "家里就行",
                "店里来",
            }:
                invalid_addresses += 1

    return {
        "source": str(path),
        "users": len(users),
        "subtasks": len(begins),
        "action_transitions": dict(sorted(transitions.items())),
        "routing_changes": dict(sorted(routing_changes.items())),
        "replayed_invalid_address_values": invalid_addresses,
        "observable_failure_events": dict(sorted(visible_failures.items())),
        "data_access": {
            "inputs": [
                "subtask_begin.instruction",
                "subtask_begin.domain/facet/action",
                "lesson_recorded.failure_class",
            ],
            "forbidden": [
                "reward",
                "rubric",
                "target_product_ids",
                "target/distraction markers",
            ],
            "model_calls": 0,
            "evaluator_calls": 0,
            "per_user_findings_emitted": False,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("trace", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    report = audit(args.trace)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()

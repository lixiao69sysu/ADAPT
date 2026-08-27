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
    user_events: Counter[str] = Counter()
    attribution_owners: Counter[str] = Counter()
    harness_event_conflicts = 0
    blocked_action_surfaces = 0
    create_consistent = 0
    create_checked = 0
    last_user_event = ""
    awaiting_payment_reply = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if event.get("event") == "subtask_begin":
            begins.append(event)
            awaiting_payment_reply = False
        elif event.get("event") == "payment_question":
            awaiting_payment_reply = True
        elif event.get("event") == "user_observation":
            last_user_event = str(
                event.get(
                    "user_event",
                    "inferred_payment_reply"
                    if awaiting_payment_reply
                    else "legacy_untyped",
                )
            )
            awaiting_payment_reply = False
            user_events[last_user_event] += 1
        elif event.get("event") == "lesson_recorded":
            failure_class = str(event.get("failure_class", "unknown"))
            visible_failures[failure_class] += 1
            if failure_class == "user_correction" and (
                last_user_event.startswith("payment_")
                or last_user_event == "inferred_payment_reply"
            ):
                harness_event_conflicts += 1
        elif event.get("event") == "failure_attributed":
            attribution_owners[str(event.get("owner", "unknown"))] += 1
        elif event.get("event") == "decision_surface":
            if (
                str(event.get("phase", ""))
                in {"ready_to_create", "ready_to_pay", "ready_to_workflow"}
                and int(event.get("allowed_tool_count", 0)) == 0
            ):
                blocked_action_surfaces += 1
        elif event.get("event") == "create_consistency":
            create_checked += 1
            create_consistent += int(bool(event.get("consistent", False)))

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
        "typed_user_events": dict(sorted(user_events.items())),
        "failure_attribution_owners": dict(sorted(attribution_owners.items())),
        "harness_event_conflicts": harness_event_conflicts,
        "blocked_action_surfaces": blocked_action_surfaces,
        "create_consistency": {
            "consistent": create_consistent,
            "checked": create_checked,
            "rate": (create_consistent / create_checked if create_checked else None),
        },
        "data_access": {
            "inputs": [
                "subtask_begin.instruction",
                "subtask_begin.domain/facet/action",
                "lesson_recorded.failure_class",
                "user_observation.user_event",
                "failure_attributed.owner",
                "decision_surface.phase/allowed_tool_count",
                "create_consistency.consistent",
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

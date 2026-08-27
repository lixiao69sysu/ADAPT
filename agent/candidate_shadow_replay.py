"""Zero-model candidate shadow replay over agent-visible simulation messages.

This module deliberately reads only current instructions, visible historical
interactions, user/assistant messages, tool calls, and tool results.  It never
consults evaluator output or hidden task annotations.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from agent.vitabench_bootstrap import enable_vitabench_utf8

enable_vitabench_utf8()

from agent.candidate_attribution_report import aggregate
from agent.decision import CandidateLedger, TaskSpec, is_search_tool
from agent.intent import accepts_visible_recommendation, selected_ordinal
from agent.memory.adapt_memory import ADAPTMemory
from agent.runtime import CandidateAttributionEngine
from agent.vitabench_runner import get_tasks


_NUMBERED_RECOMMENDATION = re.compile(r"^\s*([1-3])\.\s*(.+?)(?:（¥|$)")


def _visible_simulations(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    visible = []
    for simulation in payload.get("simulations", []):
        messages = []
        for message in simulation.get("messages", []):
            messages.append(
                {
                    "role": message.get("role"),
                    "content": message.get("content"),
                    "name": message.get("name"),
                    "error": bool(message.get("error", False)),
                    "tool_calls": [
                        {
                            "name": call.get("name"),
                            "arguments": call.get("arguments") or {},
                        }
                        for call in message.get("tool_calls") or []
                    ],
                }
            )
        visible.append(
            {"task_id": str(simulation.get("task_id", "")), "messages": messages}
        )
    return visible


def _segments(messages: list[dict], subtasks: list[Any]) -> list[tuple[Any, list[dict]]]:
    starts = []
    cursor = 0
    for subtask in subtasks:
        found = next(
            (
                index
                for index in range(cursor, len(messages))
                if messages[index].get("role") == "user"
                and str(messages[index].get("content") or "").strip()
                == str(subtask.instruction).strip()
            ),
            -1,
        )
        if found < 0:
            continue
        starts.append((subtask, found))
        cursor = found + 1
    return [
        (
            subtask,
            messages[start : starts[index + 1][1] if index + 1 < len(starts) else len(messages)],
        )
        for index, (subtask, start) in enumerate(starts)
    ]


def _candidate_observation_tool(name: str) -> bool:
    lowered = (name or "").casefold()
    if any(marker in lowered for marker in ("order_status", "booking_status")):
        return False
    return is_search_tool(lowered) or (
        lowered.startswith("get_") and lowered.endswith("_info")
    )


def _displayed_names(content: str) -> list[str]:
    names = []
    for line in (content or "").splitlines():
        match = _NUMBERED_RECOMMENDATION.match(line)
        if match:
            names.append(match.group(2).strip())
    return names


def _unique_name_ids(ledger: CandidateLedger, names: list[str]) -> tuple[str, ...]:
    resolved = []
    for name in names:
        matches = [
            candidate.candidate_id
            for candidate in ledger.candidates.values()
            if candidate.name == name
        ]
        resolved.append(matches[0] if len(matches) == 1 else "")
    return tuple(resolved)


def _call_candidate_ids(arguments: dict[str, Any], ledger: CandidateLedger) -> set[str]:
    observed = set(ledger.candidates)
    values = []
    for value in arguments.values():
        values.extend(value if isinstance(value, list) else [value])
    return {str(value) for value in values if str(value) in observed}


def replay(path: Path, *, language: str = "chinese") -> tuple[list[dict], dict]:
    tasks = {task.id: task for task in get_tasks(language)}
    events: list[dict] = []
    for simulation in _visible_simulations(path):
        task_id = simulation["task_id"]
        task = tasks.get(task_id)
        if task is None:
            continue
        memory = ADAPTMemory(language=language)
        for instruction_epoch, (subtask, messages) in enumerate(
            _segments(simulation["messages"], task.subtasks), 1
        ):
            memory.begin_subtask(subtask.instruction)
            memory.update(new_interactions=list(subtask.interactions), llm=None)
            spec = TaskSpec.compile(subtask.instruction, domain_hint=subtask.domain)
            card = memory.compile_task(subtask.instruction, spec=spec)
            ledger = CandidateLedger()
            displayed_ids: tuple[str, ...] = ()
            selected_id = ""
            create_proposals: list[set[str]] = []
            for message in messages:
                role = message.get("role")
                content = str(message.get("content") or "")
                if role == "tool" and not message.get("error"):
                    name = str(message.get("name") or "")
                    if _candidate_observation_tool(name):
                        ledger.observe(name, content)
                        memory.apply_candidate_grounding(
                            card,
                            [
                                candidate
                                for candidate in ledger.candidates.values()
                                if candidate.entity_type not in {"order", "unknown"}
                            ],
                        )
                elif role == "assistant":
                    names = _displayed_names(content)
                    if names:
                        displayed_ids = _unique_name_ids(ledger, names)
                    for call in message.get("tool_calls") or []:
                        call_name = str(call.get("name") or "")
                        if "create" not in call_name.casefold():
                            continue
                        proposed = _call_candidate_ids(
                            call.get("arguments") or {}, ledger
                        )
                        create_proposals.append(proposed)
                elif role == "user" and displayed_ids:
                    ordinal = selected_ordinal(content)
                    if not ordinal and accepts_visible_recommendation(content):
                        ordinal = 1
                    if 1 <= ordinal <= len(displayed_ids):
                        selected_id = displayed_ids[ordinal - 1]

            candidates = list(ledger.structural_leaf_candidates())
            comparison = CandidateAttributionEngine.compare(candidates, card)
            for policy, batch in comparison.items():
                events.append(
                    {
                        "event": "candidate_attribution",
                        "source": str(path),
                        "task_id": task_id,
                        "instruction_epoch": instruction_epoch,
                        "candidate_version": ledger.candidate_version,
                        "policy": policy,
                        "summary": batch.summary.as_dict(),
                        "top3": [record.as_dict() for record in batch.records[:3]],
                    }
                )
            current_records = comparison["current"].records
            expected_id = selected_id or (
                current_records[0].candidate_id if current_records else ""
            )
            selection_source = (
                "user_snapshot" if selected_id else "shadow_current_top1"
            )
            for proposed in create_proposals:
                if expected_id:
                    events.append(
                        {
                            "event": "create_consistency",
                            "source": str(path),
                            "task_id": task_id,
                            "instruction_epoch": instruction_epoch,
                            "selection_source": selection_source,
                            "selected_candidate_id": expected_id,
                            "consistent": expected_id in proposed,
                        }
                    )
    report = aggregate(events, sources=[str(path)])
    report["replay"] = {
        "mode": "zero_model_visible_messages_and_interactions",
        "subtasks": len(
            {
                (event.get("task_id"), event.get("instruction_epoch"))
                for event in events
                if event.get("event") == "candidate_attribution"
            }
        ),
        "forbidden_fields_read": [],
    }
    return events, report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("simulation", type=Path)
    parser.add_argument("--events-to", type=Path)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--language", default="chinese")
    args = parser.parse_args()
    events, report = replay(args.simulation, language=args.language)
    if args.events_to:
        args.events_to.parent.mkdir(parents=True, exist_ok=True)
        args.events_to.write_text(
            "".join(
                json.dumps(event, ensure_ascii=False, default=str) + "\n"
                for event in events
            ),
            encoding="utf-8",
        )
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()

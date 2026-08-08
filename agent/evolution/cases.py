"""Mine observable bad cases from unmodified VitaBench result JSON."""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any

from agent.evolution.models import BadCase, Diagnosis


_GREETING = "你好，请问需要什么服务？"
_SUBTASK_HEADER = re.compile(r"SUBTASK\s+(\d+)/\d+")


def load_results(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _split_subtasks(messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for message in messages:
        is_boundary = (
            message.get("role") == "assistant"
            and (message.get("content") or "").strip() == _GREETING
        )
        if is_boundary and current:
            chunks.append(current)
            current = []
        current.append(message)
    if current:
        chunks.append(current)
    return chunks


def _instruction(messages: list[dict[str, Any]]) -> str:
    for message in messages:
        if message.get("role") == "user" and message.get("content"):
            return str(message["content"])
    return ""


def _tool_calls(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    calls = []
    for message in messages:
        if message.get("role") == "assistant":
            calls.extend(message.get("tool_calls") or [])
    return calls


def _signature(call: dict[str, Any]) -> str:
    arguments = json.dumps(
        call.get("arguments") or {},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"{call.get('name', '')}:{arguments}"


def _paid(messages: list[dict[str, Any]]) -> bool:
    for message in messages:
        if message.get("role") != "tool":
            continue
        content = str(message.get("content") or "").lower()
        if "payment successful" in content or "支付成功" in content:
            return True
    return False


def _diagnose(
    *,
    reward: float,
    memory_snapshot: str,
    calls: list[dict[str, Any]],
    paid: bool,
    max_identical_calls: int,
    tool_error_count: int,
    skills: list[str],
) -> list[Diagnosis]:
    findings: list[Diagnosis] = []
    search_count = sum("search" in str(call.get("name", "")) for call in calls)
    if max_identical_calls >= 3:
        findings.append(
            Diagnosis(
                code="repeated_tool_call",
                evidence=f"same tool signature observed {max_identical_calls} times",
                confidence=1.0,
            )
        )
    if reward == 0 and paid and memory_snapshot and search_count:
        findings.append(
            Diagnosis(
                code="memory_retrieval",
                evidence=(
                    "task completed and paid but reward remained zero while a "
                    "non-empty memory snapshot and search trajectory were present"
                ),
                confidence=0.75,
            )
        )
    if reward == 0 and tool_error_count:
        findings.append(
            Diagnosis(
                code="tool_execution",
                evidence=f"{tool_error_count} public tool errors observed",
                confidence=0.9,
            )
        )
    if reward == 0 and "proactive" in skills:
        findings.append(
            Diagnosis(
                code="proactive_decision",
                evidence="failed subtask is labeled proactive by the evaluator",
                confidence=0.7,
            )
        )
    if reward == 0 and not findings:
        findings.append(
            Diagnosis(
                code="unclassified",
                evidence="reward is zero but no supported deterministic symptom matched",
                confidence=0.2,
            )
        )
    return findings


def mine_bad_cases(results: dict[str, Any]) -> list[BadCase]:
    """Return failed personalization subtasks with observable evidence only."""
    cases: list[BadCase] = []
    for simulation in results.get("simulations") or []:
        task_id = str(simulation.get("task_id") or simulation.get("id") or "unknown")
        reward_info = simulation.get("reward_info") or {}
        info = reward_info.get("info") or {}
        rewards = info.get("subtask_rewards") or {}
        skills_by_subtask = info.get("subtask_skill_tested") or {}
        chunks = _split_subtasks(simulation.get("messages") or [])
        snapshots = (simulation.get("states") or {}).get("memory_snapshots") or {}

        ordered = sorted(
            (
                (int(key.removeprefix("subtask_").removesuffix("_reward")), value)
                for key, value in rewards.items()
                if key.startswith("subtask_") and key.endswith("_reward")
            ),
            key=lambda item: item[0],
        )
        for index, raw_reward in ordered:
            reward = float(raw_reward)
            if reward > 0:
                continue
            messages = chunks[index] if index < len(chunks) else []
            calls = _tool_calls(messages)
            counts = Counter(_signature(call) for call in calls)
            max_identical = max(counts.values(), default=0)
            error_count = sum(
                bool(message.get("error"))
                for message in messages
                if message.get("role") == "tool"
            )
            skills = list(skills_by_subtask.get(f"subtask_{index}") or [])
            memory_snapshot = str(snapshots.get(f"subtask_{index}_memory") or "")
            paid = _paid(messages)
            diagnoses = _diagnose(
                reward=reward,
                memory_snapshot=memory_snapshot,
                calls=calls,
                paid=paid,
                max_identical_calls=max_identical,
                tool_error_count=error_count,
                skills=skills,
            )
            cases.append(
                BadCase(
                    case_id=f"{task_id}:subtask_{index}",
                    task_id=task_id,
                    subtask_index=index,
                    instruction=_instruction(messages),
                    reward=reward,
                    termination_reason=str(simulation.get("termination_reason") or ""),
                    skills=skills,
                    memory_snapshot=memory_snapshot,
                    tool_call_count=len(calls),
                    search_call_count=sum(
                        "search" in str(call.get("name", "")) for call in calls
                    ),
                    max_identical_calls=max_identical,
                    tool_error_count=error_count,
                    paid=paid,
                    diagnoses=diagnoses,
                )
            )
    return cases


def apply_diagnosis_hints(
    cases: list[BadCase], hints: dict[str, list[str]]
) -> list[BadCase]:
    """Attach reviewed log diagnoses that are absent from public result JSON."""
    supported = {"cross_entity_mismatch", "repeated_tool_call"}
    output = []
    for case in cases:
        diagnoses = list(case.diagnoses)
        existing = {item.code for item in diagnoses}
        for code in hints.get(case.case_id, []):
            if code not in supported:
                raise ValueError(f"unsupported diagnosis hint: {code}")
            if code not in existing:
                diagnoses.append(
                    Diagnosis(
                        code=code,
                        evidence="external Agent diagnostic evidence",
                        confidence=1.0,
                    )
                )
        output.append(replace(case, diagnoses=diagnoses))
    unknown_cases = set(hints) - {case.case_id for case in cases}
    if unknown_cases:
        raise ValueError(f"diagnosis hints reference unknown cases: {sorted(unknown_cases)}")
    return output


def mine_log_hints(path: Path, task_id: str) -> dict[str, list[str]]:
    """Recover rejected internal drafts that are intentionally absent from traces."""
    current_index: int | None = None
    found: dict[str, set[str]] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        header = _SUBTASK_HEADER.search(line)
        if header:
            current_index = int(header.group(1)) - 1
            continue
        if current_index is None or "ADAPT_TOOL_DRAFT_REJECTED" not in line:
            continue
        for code in ("repeated_tool_call", "cross_entity_mismatch"):
            if code in line:
                case_id = f"{task_id}:subtask_{current_index}"
                found.setdefault(case_id, set()).add(code)
    return {case_id: sorted(codes) for case_id, codes in sorted(found.items())}

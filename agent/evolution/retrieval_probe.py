"""Deterministic offline probes for retrieval strategy candidates."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent.memory.adapt_memory import ADAPTMemory
from agent.memory.stream import parse_timestamp


_GENERIC_BIGRAMS = {
    "帮我",
    "给我",
    "送到",
    "家里",
    "赶紧",
    "新的",
    "上次",
    "一个",
    "现在",
}


def _event_text(event) -> str:
    signal = event.signal
    if signal is None:
        return event.raw_text or ""
    return f"{signal.predicate} {signal.object} {signal.raw}"


def _expanded_query(query: str, changes: dict[str, Any]) -> str:
    additions = []
    for source, targets in (changes.get("product_type_expansions") or {}).items():
        if source in query:
            additions.extend(str(target) for target in targets)
    return " ".join([query, *additions])


def _query_centered_excerpt(text: str, query: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    chars = [character for character in query if "一" <= character <= "鿿"]
    grams = {
        chars[index] + chars[index + 1]
        for index in range(len(chars) - 1)
    } - _GENERIC_BIGRAMS
    positions = [
        position
        for gram in grams
        if (position := text.find(gram)) >= 0
    ]
    if not positions:
        return text[:limit]
    center = min(positions)
    start = max(0, center - limit // 3)
    return text[start : start + limit]


def _relevance(memory, event, query: str, changes: dict[str, Any]) -> float:
    scorer = memory.scorer
    domain = scorer.domain(query)
    if not changes.get("ignore_generic_bigrams"):
        return scorer.relevance_score(event, query, domain)
    signal = event.signal
    if signal is None:
        return 0.05
    text = _event_text(event)
    query_terms = set(scorer._keywords(query)) - _GENERIC_BIGRAMS
    event_terms = set(scorer._keywords(text)) - _GENERIC_BIGRAMS
    overlap = query_terms & event_terms
    if overlap:
        return min(1.0, 0.6 + 0.1 * len(overlap))
    if domain:
        event_domain = scorer._event_domain(signal, text)
        if event_domain and event_domain != domain:
            return 0.02
    return 0.25


def rank_target(
    memory: ADAPTMemory,
    query: str,
    target_terms: list[str],
    *,
    now: str | None,
    changes: dict[str, Any],
) -> dict[str, Any]:
    expanded = _expanded_query(query, changes)
    reference = parse_timestamp(now or "")
    if changes.get("recency_reference") == "latest_observed_event_time":
        reference = parse_timestamp(memory._latest_ts or "") or reference
    boosts = changes.get("structured_signal_boost") or {}
    scored = []
    for event in memory.stream.all():
        relevance = _relevance(memory, event, expanded, changes)
        recency = memory.scorer.recency_score(event, reference)
        importance = memory.scorer.importance_score(event)
        config = memory.scorer.config
        score = (
            config.w_relevance * relevance
            + config.w_recency * recency
            + config.w_importance * importance
            + float(boosts.get(event.type, 0.0))
        )
        scored.append((score, event))
    scored.sort(key=lambda item: item[0], reverse=True)
    target_ranks = [
        index
        for index, (_, event) in enumerate(scored, 1)
        if all(term.lower() in _event_text(event).lower() for term in target_terms)
    ]
    best = min(target_ranks, default=len(scored) + 1)
    target_excerpt = ""
    if target_ranks:
        target_event = scored[best - 1][1]
        signal = target_event.signal
        if signal and signal.predicate != "raw_observation":
            target_excerpt = f"{signal.predicate}: {signal.object}"
        else:
            raw = target_event.raw_text or ""
            if changes.get("raw_excerpt") == "query_centered":
                target_excerpt = _query_centered_excerpt(
                    raw, expanded, int(changes.get("max_chars", 220))
                )
            else:
                target_excerpt = raw[:100]
    return {
        "query": query,
        "expanded_query": expanded,
        "target_terms": target_terms,
        "event_count": len(scored),
        "best_target_rank": best,
        "target_in_top_5": best <= 5,
        "target_in_top_20": best <= 20,
        "target_visible_in_excerpt": all(
            term.lower() in target_excerpt.lower() for term in target_terms
        ),
        "target_excerpt": target_excerpt,
        "top_5": [
            {
                "rank": index,
                "score": round(score, 6),
                "type": event.type,
                "timestamp": event.timestamp,
                "text": _event_text(event)[:240],
            }
            for index, (score, event) in enumerate(scored[:5], 1)
        ],
    }


def replay_personalization_memory(
    tasks_path: Path, task_id: str, subtask_index: int
) -> tuple[ADAPTMemory, dict[str, Any]]:
    tasks = json.loads(tasks_path.read_text(encoding="utf-8"))
    task = next((item for item in tasks if item.get("id") == task_id), None)
    if task is None:
        raise ValueError(f"task {task_id} not found")
    subtasks = task.get("subtasks") or []
    if not 0 <= subtask_index < len(subtasks):
        raise ValueError(f"subtask index {subtask_index} out of range")
    memory = ADAPTMemory(language="chinese")
    for subtask in subtasks[: subtask_index + 1]:
        memory.update(subtask.get("interactions") or [])
    return memory, subtasks[subtask_index]

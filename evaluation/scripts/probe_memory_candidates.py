"""Deterministic offline pre-screen for pending memory.* candidates.

Replays ADAPTMemory up to each failed subtask of a task and diffs the
read() injection between the baseline configuration and each candidate
overlay. Target terms (offline evidence only) decide which side surfaces
the preference the instruction is asking about.

Usage:
    python evaluation/scripts/probe_memory_candidates.py \
        --tasks evaluation/vitabench/data/vita/domains/personalization/tasks.json \
        --task-id U642088 --registry evaluation/evolution/strategies.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vitabench" / "src"))

from agent.evolution.registry import StrategyRegistry  # noqa: E402
from agent.memory.adapt_memory import ADAPTMemory  # noqa: E402


class ProbeMemory(ADAPTMemory):
    """ADAPTMemory configured directly from a changes dict (no registry)."""

    def __init__(self, retrieval_changes: dict, paging_changes: dict):
        super().__init__()
        config = self.scorer.config
        config.product_type_expansions = dict(
            retrieval_changes.get("product_type_expansions") or {}
        )
        config.ignore_generic_bigrams = bool(
            retrieval_changes.get("ignore_generic_bigrams", False)
        )
        config.structured_signal_boost = dict(
            retrieval_changes.get("structured_signal_boost") or {}
        )
        self._recency_reference = retrieval_changes.get("recency_reference")
        self._raw_excerpt_mode = paging_changes.get("raw_excerpt", "prefix")
        self._raw_excerpt_chars = int(paging_changes.get("max_chars", 100))


def load_task(tasks_path: Path, task_id: str) -> dict:
    tasks = json.loads(tasks_path.read_text(encoding="utf-8"))
    task = next((item for item in tasks if item.get("id") == task_id), None)
    if task is None:
        raise ValueError(f"task {task_id} not found")
    return task


def replay(memory: ADAPTMemory, subtasks: list[dict], index: int) -> None:
    for subtask in subtasks[:index]:
        memory.update(subtask.get("interactions") or [])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument(
        "--subtask-index", type=int, action="append", dest="indexes",
        help="zero-based failing subtask indexes to probe",
    )
    parser.add_argument(
        "--target-term", action="append", default=[],
        help="term:subtask_index pairs used as offline relevance evidence",
    )
    args = parser.parse_args()

    registry = StrategyRegistry(args.registry).load()
    candidates = {
        strategy_id: candidate
        for strategy_id, candidate in registry["candidates"].items()
        if candidate.get("status") == "pending"
        and str(candidate.get("component", "")).startswith("memory.")
    }
    if not candidates:
        print("no pending memory.* candidates")
        return 1

    terms_by_index: dict[int, list[str]] = {}
    for item in args.target_term:
        term, _, raw_index = item.rpartition(":")
        terms_by_index.setdefault(int(raw_index), []).append(term)

    task = load_task(args.tasks, args.task_id)
    subtasks = task.get("subtasks") or []
    active_ids = registry.get("active_strategy_ids") or []
    active_changes = {}
    for strategy_id in active_ids:
        candidate = registry["candidates"].get(strategy_id) or {}
        if str(candidate.get("component")) == "memory.retrieval":
            active_changes.update(candidate.get("changes") or {})

    report = {
        "task_id": args.task_id,
        "active_strategies": active_ids,
        "subtasks": {},
    }
    for index in args.indexes:
        instruction = subtasks[index].get("instruction") or ""
        target_terms = terms_by_index.get(index, [])
        per_config = {}
        for name, (retrieval_changes, paging_changes) in _configs(
            candidates, active_changes
        ):
            memory = ProbeMemory(retrieval_changes, paging_changes)
            replay(memory, subtasks, index)
            injection = memory.read(instruction, with_suggestion=False)
            hits = sorted(
                {
                    term
                    for term in target_terms
                    if term.lower() in injection.lower()
                }
            )
            per_config[name] = {
                "injection": injection,
                "target_hits": hits,
                "chars": len(injection),
            }
        report["subtasks"][f"subtask_{index}"] = {
            "instruction": instruction,
            "target_terms": target_terms,
            "configs": per_config,
        }
        _print_subtask(index, instruction, target_terms, per_config)

    out = Path("evaluation/evolution/runs/memory_candidate_prescreen.json")
    out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"\nreport written to {out}")
    return 0


def _configs(candidates: dict, active_changes: dict):
    yield "baseline(active)", (dict(active_changes), {})
    for strategy_id, candidate in sorted(candidates.items()):
        changes = candidate.get("changes") or {}
        if candidate.get("component") == "memory.retrieval":
            merged = dict(active_changes)
            merged.update(changes)
            yield strategy_id, (merged, {})
        else:
            yield strategy_id, (dict(active_changes), changes)


def _print_subtask(index, instruction, target_terms, per_config) -> None:
    print("=" * 78)
    print(f"subtask_{index}: {instruction}")
    if target_terms:
        print(f"target terms: {target_terms}")
    baseline_hits = per_config["baseline(active)"]["target_hits"]
    for name, item in per_config.items():
        marker = ""
        if name != "baseline(active)":
            gained = set(item["target_hits"]) - set(baseline_hits)
            lost = set(baseline_hits) - set(item["target_hits"])
            parts = []
            if gained:
                parts.append(f"+hits {sorted(gained)}")
            if lost:
                parts.append(f"-hits {sorted(lost)}")
            marker = f"  [{'; '.join(parts) or 'same hits'}]"
        print(f"-- {name} ({item['chars']} chars){marker}")
        print("   " + item["injection"].replace("\n", "\n   ")[:1200])


if __name__ == "__main__":
    raise SystemExit(main())

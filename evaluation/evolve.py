"""CLI for the controlled ADAPT bad-case evolution loop.

This tool reads VitaBench outputs but never modifies benchmark runtime files.
Expected-state rubrics may be used by VitaBench to calculate reward, but this
tool does not place them in an Agent prompt, memory, or promoted strategy.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from agent.evolution.cases import (
    apply_diagnosis_hints,
    load_results,
    mine_bad_cases,
    mine_log_hints,
)
from agent.evolution.catalog import propose_candidates
from agent.evolution.gate import PromotionGate
from agent.evolution.metrics import build_scorecards, read_subtask_rewards_from_log
from agent.evolution.registry import StrategyRegistry
from agent.evolution.retrieval_probe import rank_target, replay_personalization_memory


DEFAULT_REGISTRY = Path("evaluation/evolution/strategies.json")


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def analyze(args) -> dict[str, Any]:
    results = load_results(args.results)
    cases = mine_bad_cases(results)
    merged_hints: dict[str, list[str]] = {}
    if args.log:
        task_ids = {
            str(simulation.get("task_id") or simulation.get("id") or "unknown")
            for simulation in results.get("simulations") or []
        }
        if len(task_ids) != 1:
            raise ValueError("--log currently requires a single-task result file")
        merged_hints.update(mine_log_hints(args.log, next(iter(task_ids))))
    if args.diagnosis_hints:
        for case_id, codes in _read_json(args.diagnosis_hints).items():
            merged_hints.setdefault(case_id, []).extend(codes)
    if merged_hints:
        merged_hints = {
            case_id: sorted(set(codes)) for case_id, codes in merged_hints.items()
        }
        cases = apply_diagnosis_hints(cases, merged_hints)
    candidates = propose_candidates(cases)
    registry = StrategyRegistry(args.registry)
    registry.upsert(candidates)
    report = {
        "schema_version": 1,
        "source_results": str(args.results),
        "leakage_guard": (
            "evaluation outcomes are used only for offline diagnosis and gating; "
            "rubrics are never copied into Agent runtime inputs"
        ),
        "summary": {
            "bad_case_count": len(cases),
            "candidate_count": len(candidates),
            "diagnosis_counts": _diagnosis_counts(cases),
        },
        "bad_cases": [case.to_dict() for case in cases],
        "candidates": [candidate.to_dict() for candidate in candidates],
    }
    _write_json(args.report, report)
    return report


def _diagnosis_counts(cases) -> dict[str, int]:
    counts: dict[str, int] = {}
    for case in cases:
        for diagnosis in case.diagnoses:
            counts[diagnosis.code] = counts.get(diagnosis.code, 0) + 1
    return dict(sorted(counts.items()))


def gate(args) -> dict[str, Any]:
    baseline = load_results(args.baseline)
    candidate = load_results(args.candidate)
    baseline_card, candidate_card = build_scorecards(
        baseline,
        candidate,
        baseline_log=args.baseline_log,
        candidate_log=args.candidate_log,
    )
    decision = PromotionGate().evaluate(baseline_card, candidate_card)
    registry = StrategyRegistry(args.registry)
    registry.record_decision(
        args.strategy_id,
        promoted=decision.promoted,
        reasons=decision.reasons,
    )
    report = {
        "strategy_id": args.strategy_id,
        "promoted": decision.promoted,
        "reasons": decision.reasons,
        "baseline": asdict(baseline_card),
        "candidate": asdict(candidate_card),
        "cohort_rule": (
            "baseline failures are bad cases; baseline successes are regression "
            "cases; missing candidate cases score as failures"
        ),
    }
    if args.report:
        _write_json(args.report, report)
    return report


def status(args) -> dict[str, Any]:
    return StrategyRegistry(args.registry).load()


def gate_partial(args) -> dict[str, Any]:
    baseline = read_subtask_rewards_from_log(args.baseline_log)
    candidate = read_subtask_rewards_from_log(args.candidate_log)
    common = sorted(set(baseline) & set(candidate))
    regressions = [
        case_id
        for case_id in common
        if baseline[case_id] > 0 and candidate[case_id] <= 0
    ]
    improvements = [
        case_id
        for case_id in common
        if baseline[case_id] <= 0 and candidate[case_id] > 0
    ]
    rejected = bool(regressions)
    reasons = (
        [f"completed regression cases: {', '.join(regressions)}"]
        if rejected
        else ["no completed regression; partial evidence cannot promote"]
    )
    if rejected:
        StrategyRegistry(args.registry).record_decision(
            args.strategy_id, promoted=False, reasons=reasons
        )
    report = {
        "strategy_id": args.strategy_id,
        "decision": "rejected" if rejected else "inconclusive",
        "completed_common_cases": common,
        "improvements": improvements,
        "regressions": regressions,
        "reasons": reasons,
        "promotion_allowed": False,
    }
    _write_json(args.report, report)
    return report


def probe_retrieval(args) -> dict[str, Any]:
    registry = StrategyRegistry(args.registry)
    payload = registry.load()
    memory, subtask = replay_personalization_memory(
        args.tasks, args.task_id, args.subtask_index
    )
    baseline = rank_target(
        memory,
        subtask.get("instruction") or "",
        args.target_term,
        now=subtask.get("current_time"),
        changes={},
    )
    strategy_ids = args.strategy_id or [
        strategy_id
        for strategy_id, candidate in payload["candidates"].items()
        if str(candidate.get("component", "")).startswith("memory.")
    ]
    candidates = {}
    for strategy_id in strategy_ids:
        candidate = payload["candidates"].get(strategy_id)
        if candidate is None:
            raise KeyError(f"unknown strategy {strategy_id}")
        result = rank_target(
            memory,
            subtask.get("instruction") or "",
            args.target_term,
            now=subtask.get("current_time"),
            changes=candidate.get("changes") or {},
        )
        result["rank_delta"] = baseline["best_target_rank"] - result["best_target_rank"]
        candidates[strategy_id] = result
        registry.record_probe(
            strategy_id,
            {
                "task_id": args.task_id,
                "subtask_index": args.subtask_index,
                "target_terms": args.target_term,
                "baseline_rank": baseline["best_target_rank"],
                "candidate_rank": result["best_target_rank"],
                "rank_delta": result["rank_delta"],
            },
        )
    report = {
        "task_id": args.task_id,
        "subtask_index": args.subtask_index,
        "offline_only": True,
        "baseline": baseline,
        "candidates": candidates,
    }
    _write_json(args.report, report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Controlled bad-case evolution for the ADAPT Agent"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze_parser = subparsers.add_parser("analyze")
    analyze_parser.add_argument("--results", type=Path, required=True)
    analyze_parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    analyze_parser.add_argument("--report", type=Path, required=True)
    analyze_parser.add_argument("--log", type=Path)
    analyze_parser.add_argument("--diagnosis-hints", type=Path)
    analyze_parser.set_defaults(handler=analyze)

    gate_parser = subparsers.add_parser("gate")
    gate_parser.add_argument("--baseline", type=Path, required=True)
    gate_parser.add_argument("--candidate", type=Path, required=True)
    gate_parser.add_argument("--baseline-log", type=Path)
    gate_parser.add_argument("--candidate-log", type=Path)
    gate_parser.add_argument("--strategy-id", required=True)
    gate_parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    gate_parser.add_argument("--report", type=Path)
    gate_parser.set_defaults(handler=gate)

    partial_parser = subparsers.add_parser("gate-partial")
    partial_parser.add_argument("--baseline-log", type=Path, required=True)
    partial_parser.add_argument("--candidate-log", type=Path, required=True)
    partial_parser.add_argument("--strategy-id", required=True)
    partial_parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    partial_parser.add_argument("--report", type=Path, required=True)
    partial_parser.set_defaults(handler=gate_partial)

    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    status_parser.set_defaults(handler=status)

    probe_parser = subparsers.add_parser("probe-retrieval")
    probe_parser.add_argument("--tasks", type=Path, required=True)
    probe_parser.add_argument("--task-id", required=True)
    probe_parser.add_argument("--subtask-index", type=int, required=True)
    probe_parser.add_argument("--target-term", action="append", required=True)
    probe_parser.add_argument("--strategy-id", action="append")
    probe_parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    probe_parser.add_argument("--report", type=Path, required=True)
    probe_parser.set_defaults(handler=probe_retrieval)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    payload = args.handler(args)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

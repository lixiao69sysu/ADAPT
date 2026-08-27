"""Aggregate candidate shadow-policy attribution from ADAPT-visible events."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


def aggregate(events: Iterable[dict[str, Any]], *, sources: list[str] | None = None) -> dict:
    policy_stats: dict[str, Counter[str]] = defaultdict(Counter)
    top3_task_bands: dict[str, Counter[str]] = defaultdict(Counter)
    top3_grounding_sources: dict[str, Counter[str]] = defaultdict(Counter)
    structural_families: dict[str, Counter[str]] = defaultdict(Counter)
    decision_groups: dict[tuple, dict[str, tuple[str, ...]]] = defaultdict(dict)
    create_checked = 0
    create_consistent = 0
    create_by_source: dict[str, Counter[str]] = defaultdict(Counter)
    for event in events:
        kind = event.get("event")
        if kind == "candidate_attribution":
            policy = str(event.get("policy", "unknown"))
            summary = event.get("summary") or {}
            stats = policy_stats[policy]
            stats["decisions"] += 1
            stats["candidates"] += int(summary.get("candidate_count", 0))
            stats["admissible"] += int(summary.get("admissible_count", 0))
            stats["no_executable"] += int(
                bool(summary.get("no_executable_candidate", False))
            )
            stats["top3_non_task"] += int(
                summary.get("top3_non_task_category_count", 0)
            )
            stats["top3"] += int(summary.get("top3_count", 0))
            stats["historical_outranks"] += int(
                summary.get("historical_preference_outrank_count", 0)
            )
            stats["request_only_confusions"] += int(
                summary.get("request_only_grounding_confusions", 0)
            )
            group_key = (
                event.get("source", ""),
                event.get("user_id", ""),
                event.get("task_id", ""),
                event.get("trial", 0),
                event.get("instruction_epoch", 0),
                event.get("candidate_version", 0),
            )
            top3_ids = tuple(str(value) for value in summary.get("top3_ids", ()))
            decision_groups[group_key][policy] = top3_ids
            for record in event.get("top3") or []:
                top3_task_bands[policy][str(record.get("task_relevance_band", 0))] += 1
                structural_families[policy][str(record.get("structural_family", "unknown"))] += 1
                for source in record.get("grounding_sources") or ():
                    top3_grounding_sources[policy][str(source)] += 1
        elif kind == "create_consistency":
            create_checked += 1
            create_consistent += int(bool(event.get("consistent", False)))
            source = str(event.get("selection_source", "runtime_selected"))
            create_by_source[source]["checked"] += 1
            create_by_source[source]["consistent"] += int(
                bool(event.get("consistent", False))
            )

    rendered_policies = {}
    for policy, stats in sorted(policy_stats.items()):
        top3 = stats["top3"]
        decisions = stats["decisions"]
        rendered_policies[policy] = {
            **dict(stats),
            "top3_non_task_category_rate": (
                stats["top3_non_task"] / top3 if top3 else None
            ),
            "no_executable_rate": (
                stats["no_executable"] / decisions if decisions else None
            ),
            "top3_task_relevance_bands": dict(
                sorted(top3_task_bands[policy].items())
            ),
            "top3_grounding_sources": dict(
                sorted(top3_grounding_sources[policy].items())
            ),
            "top3_structural_families": dict(
                sorted(structural_families[policy].items())
            ),
        }
    comparisons = Counter()
    for policies in decision_groups.values():
        current = policies.get("current")
        if current is None:
            continue
        for policy in (
            "current_task_only",
            "task_first_preference_tiebreak",
        ):
            if policy in policies:
                comparisons[f"current_vs_{policy}_top3_changed"] += int(
                    policies[policy] != current
                )
                comparisons[f"current_vs_{policy}_decisions"] += 1
    return {
        "sources": sources or [],
        "policies": rendered_policies,
        "shadow_comparisons": dict(sorted(comparisons.items())),
        "create_consistency": {
            "consistent": create_consistent,
            "checked": create_checked,
            "rate": create_consistent / create_checked if create_checked else None,
            "by_selection_source": {
                source: {
                    **dict(counts),
                    "rate": (
                        counts["consistent"] / counts["checked"]
                        if counts["checked"]
                        else None
                    ),
                }
                for source, counts in sorted(create_by_source.items())
            },
        },
        "data_access": {
            "inputs": [
                "candidate_attribution.summary/top3",
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
            "per_candidate_ids_emitted": False,
        },
    }


def load_events(paths: list[Path]) -> list[dict[str, Any]]:
    events = []
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                event = json.loads(line)
                event.setdefault("source", str(path))
                events.append(event)
    return events


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("traces", nargs="+", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    report = aggregate(
        load_events(args.traces), sources=[str(path) for path in args.traces]
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()

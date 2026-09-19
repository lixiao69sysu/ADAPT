"""Do a user's preferences transfer across categories within one dimension?

Hierarchical pooling (partial pooling of a user's preference strength across the
categories they touch) only creates new discriminating information if the
preference really is shared. If "奶茶要半糖" predicts "咖啡也要半糖" for the same
user, pooling manufactures a condition where the current system has none -- which
matters because 19 of 21 wrong candidate bindings are indistinguishable from the
target using any readable information.

If it does not predict it, pooling is only a better estimator of a quantity that
is already estimated, and its headroom collapses to the same ~+0.03 as the other
selection-side changes.

Zero model: the memory is replayed with the heuristic parser only (llm=None),
over every user in the task set, using `subtask.interactions` -- the same input
path as `scripts/memory_evolution_audit.py`.

Reads: for each user, the dominant value per `(dimension, category)`. Then, for
each dimension that spans two or more categories, agreement across category
pairs, against the agreement expected if the two categories were independent
(Cohen's kappa). Kappa near 0 means pooling adds nothing.

Usage:
    python scripts/cross_category_preference.py
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent.vitabench_bootstrap as bootstrap  # noqa: E402

bootstrap.enable_vitabench_utf8()

from agent.memory.facts import fact_from_signal  # noqa: E402
from agent.memory.signals import SignalParser  # noqa: E402

# Dimensions where a shared value across categories is semantically plausible.
# `room_type` and `transport` are excluded by construction: they only ever live
# in one category, so they cannot test transfer.
POOLABLE = ("temperature", "sweetness", "taste", "budget", "topping", "attribute")


def load_user_facts(tasks: list[Any]) -> dict[str, list[Any]]:
    """user_id -> facts, replayed from that user's whole interaction history."""
    parser = SignalParser()
    per_user: dict[str, list[Any]] = {}
    for task in tasks:
        seen: set[str] = set()
        facts: list[Any] = []
        for subtask in task.subtasks:
            fresh = []
            for interaction in getattr(subtask, "interactions", None) or []:
                encoded = json.dumps(
                    interaction, ensure_ascii=False, sort_keys=True, default=str
                )
                fingerprint = hashlib.sha1(encoded.encode("utf-8")).hexdigest()
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                fresh.append(interaction)
            if not fresh:
                continue
            for index, signal in enumerate(parser.parse(fresh)):
                facts.append(fact_from_signal(signal, str(index)))
        per_user[str(task.id)] = facts
    return per_user


def kappa(pairs: list[tuple[str, str]]) -> tuple[float, float, float, int]:
    """Cohen's kappa for two symmetric category slots."""
    n = len(pairs)
    if not n:
        return 0.0, 0.0, 0.0, 0
    observed = sum(1 for a, b in pairs if a == b) / n
    slot = collections.Counter()
    for a, b in pairs:
        slot[a] += 1
        slot[b] += 1
    total = 2 * n
    expected = sum((count / total) ** 2 for count in slot.values())
    if expected >= 1.0:
        return observed, expected, 0.0, n
    return observed, expected, (observed - expected) / (1 - expected), n


def analyse(tasks: list[Any]) -> dict[str, Any]:
    per_user = load_user_facts(tasks)

    # (dimension, category) -> user -> Counter of values
    slot_values: dict[tuple[str, str], dict[str, collections.Counter]] = (
        collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    )
    for user_id, facts in per_user.items():
        for fact in facts:
            if fact.status != "active" or not fact.value:
                continue
            if fact.dimension not in POOLABLE:
                continue
            slot_values[(fact.dimension, fact.category)][user_id][fact.value] += 1

    # user -> dimension -> category -> dominant value
    dominant: dict[str, dict[str, dict[str, str]]] = collections.defaultdict(
        lambda: collections.defaultdict(dict)
    )
    for (dimension, category), by_user in slot_values.items():
        for user_id, counter in by_user.items():
            dominant[user_id][dimension][category] = counter.most_common(1)[0][0]

    dimension_pairs: dict[str, list[tuple[str, str]]] = collections.defaultdict(list)
    dimension_users: dict[str, set[str]] = collections.defaultdict(set)
    dimension_values: dict[str, collections.Counter] = collections.defaultdict(
        collections.Counter
    )
    for user_id, by_dimension in dominant.items():
        for dimension, by_category in by_dimension.items():
            categories = sorted(by_category)
            if len(categories) < 2:
                continue
            dimension_users[dimension].add(user_id)
            for i in range(len(categories)):
                for j in range(i + 1, len(categories)):
                    a = by_category[categories[i]]
                    b = by_category[categories[j]]
                    dimension_pairs[dimension].append((a, b))
                    dimension_values[dimension][a] += 1
                    dimension_values[dimension][b] += 1

    report: dict[str, Any] = {"users": len(per_user), "dimensions": {}}
    for dimension in sorted(dimension_pairs):
        pairs = dimension_pairs[dimension]
        observed, expected, k, n = kappa(pairs)
        report["dimensions"][dimension] = {
            "users_with_two_or_more_categories": len(dimension_users[dimension]),
            "category_pairs": n,
            "observed_agreement": round(observed, 4),
            "expected_agreement_if_independent": round(expected, 4),
            "kappa": round(k, 4),
            "distinct_values": len(dimension_values[dimension]),
            "top_values": dimension_values[dimension].most_common(6),
        }

    # The population pooling would actually help: users who have a preference in
    # one category of a dimension and none in another.
    covered = sum(len(v) for v in dimension_users.values())
    report["total_user_dimension_observations"] = covered
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", default="chinese")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    from agent.vitabench_runner import get_tasks

    tasks = get_tasks(args.language)
    report = analyse(tasks)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print(f"users {report['users']}")
    print(
        f"{'dimension':<14}{'users>=2cat':>12}{'pairs':>8}"
        f"{'observed':>10}{'expected':>10}{'kappa':>8}{'values':>8}"
    )
    print("-" * 70)
    for dimension, row in report["dimensions"].items():
        print(
            f"{dimension:<14}{row['users_with_two_or_more_categories']:>12}"
            f"{row['category_pairs']:>8}{row['observed_agreement']:>10.3f}"
            f"{row['expected_agreement_if_independent']:>10.3f}"
            f"{row['kappa']:>8.3f}{row['distinct_values']:>8}"
        )
        print(f"    top values: {row['top_values']}")
    print()
    print(
        "kappa ~ 0 means the two categories are independent, so pooling a user's "
        "strength across them adds no information. kappa > 0 means it does. "
        "This is a reach measurement: it says whether the signal exists, not "
        "whether exploiting it would raise the score."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

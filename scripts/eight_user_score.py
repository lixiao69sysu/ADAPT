"""Audit completeness and official-unit Avg@4 against the cached eight users.

No model calls; do not label incomplete runs Avg@4. Trials are replicates,
and the by-user differences below are descriptive, not a seed-pairing test.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path


def collect(payload):
    units = defaultdict(dict)
    seen = set()
    problems = []
    for sim in payload.get("simulations", []):
        user, trial = sim["task_id"], sim.get("trial", 0)
        if (user, trial) in seen:
            problems.append(f"duplicate simulation: {user}/{trial}")
        seen.add((user, trial))
        rewards = ((sim.get("reward_info") or {}).get("info") or {}).get("subtask_rewards", {})
        for key, value in rewards.items():
            if value not in (0, 1):
                problems.append(f"nonbinary reward: {user}/{trial}/{key}")
                continue
            units[(user, key)][trial] = float(value)
    return units, problems


def audit(candidate, baseline):
    control, errors = collect(baseline)
    treatment, issues = collect(candidate)
    errors += issues
    expected_users = set(baseline["tasks"])
    if set(candidate["tasks"]) != expected_users:
        errors.append("cohort differs from the cached eight users")
    if set(treatment) != set(control):
        errors.append(f"unit coverage: {len(treatment)}/{len(control)}")
    if candidate["info"].get("num_trials") != 4:
        errors.append("configured num_trials is not four")
    if candidate["info"].get("memory_type") == "groundtruth":
        errors.append("oracle memory is not an eligible ADAPT result")
    if candidate["info"].get("subtask_ids"):
        errors.append("sliced tasks cannot establish the full-user result")
    if any(set(v) != {0, 1, 2, 3} for v in treatment.values()):
        errors.append("some units lack exactly trials 0..3")
    for key in ("llm_agent", "llm_user", "llm_evaluator", "max_steps"):
        if candidate["info"].get(key) != baseline["info"].get(key):
            errors.append(f"baseline setting differs: {key}")
    average = lambda values: sum(values) / len(values) if values else None
    means = {key: average(list(v.values())) for key, v in treatment.items()}
    base_means = {key: average(list(v.values())) for key, v in control.items()}
    avg = average(list(means.values()))
    users = {}
    for user in sorted(expected_users):
        values = [v for (uid, _), v in means.items() if uid == user]
        ref = [v for (uid, _), v in base_means.items() if uid == user]
        users[user] = {"observed_mean": average(values), "stock_mean": average(ref)}
    return {
        "complete_comparable": not errors,
        "official_Avg_at_4": avg if not errors else None,
        "observed_unit_mean": avg,
        "stock_Avg_at_4": average(list(base_means.values())),
        "target_achieved": not errors and avg is not None and avg >= 0.35,
        "scored_replicates": sum(len(v) for v in treatment.values()),
        "issues": errors, "by_user": users,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("--baseline", type=Path, default=Path("data/simulations/stock_avg4_8u.json"))
    args = parser.parse_args()
    print(json.dumps(audit(json.loads(args.checkpoint.read_text(encoding="utf-8")),
                           json.loads(args.baseline.read_text(encoding="utf-8"))), indent=2))


if __name__ == "__main__":
    main()

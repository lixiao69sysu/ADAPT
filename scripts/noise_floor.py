"""Decompose the noise floor of a checkpoint into its two variance components.

The promotion criteria need to answer one question: *if this change were run on
different users, would the measured delta survive?* That is a question about the
**between-user** variance component. A floor computed from the spread across
repeated trials of the same user measures only sampling noise, and it is not a
substitute.

This matters concretely here: the four trials of one user replay the *same*
script (verified by `opensig` in `runaway_autopsy.py`), so trials are replicates
rather than independent task draws. When a user scores identically in every
trial, its within-user variance is exactly zero and contributes nothing — yet it
still shifts a within-user-based floor toward optimism.

Reports, for one or more checkpoints:

- `within_user_sd`: spread of trial rewards around each user's own mean.
- `between_user_sd`: spread of user means around the cohort mean. Pairing is the
  only device that removes this component, so it is the floor that a paired
  comparison must actually beat.
- `paired_delta_se`: SE of the mean *difference* between two arms on the same
  users. Requires two checkpoints covering the same (user, trial) units.
- the N needed to resolve a given target delta, given the between-user SD.

Usage:
    python scripts/noise_floor.py data/simulations/stock_avg4_8u.json
    python scripts/noise_floor.py baseline.json candidate.json --target 0.05
"""

from __future__ import annotations

import argparse
import collections
import json
import math
from typing import Any


def _unit_rewards(payload: dict[str, Any]) -> dict[tuple[str, int], float]:
    """(user, trial) -> reward, and also the per-user mean."""
    out: dict[tuple[str, int], float] = {}
    for sim in payload.get("simulations", []):
        reward = (sim.get("reward_info") or {}).get("reward")
        if reward is None:
            continue
        trial = sim.get("trial")
        if trial is None:
            continue
        out[(sim.get("task_id"), int(trial))] = float(reward)
    return out


def hierarchy(payload: dict[str, Any]) -> dict[str, Any]:
    """The nesting of units in one checkpoint, with the reward attached to each.

    Four levels exist in the same file, and "unit" is meaningless without saying
    which one is meant:

    1. `user`            - `task_id`. The leaderboard cohort.
    2. `simulation`      - one (user, trial) record; `.reward_info.reward` is the
                           mean over that user's subtasks. THIS is what most of
                           the tooling in this repo calls a "unit".
    3. `subtask_trial`   - one (user, trial, subtask); binary reward. Four trials
                           replay the same script, so these are replicates.
    4. `subtask_cluster` - one (user, subtask) across all trials. The independent
                           unit for any paired test.
    """
    per_user_subtasks: dict[str, int] = {}
    flat: list[float] = []
    sim_rewards: list[float] = []
    users: set[str] = set()

    for sim in payload.get("simulations", []):
        user = str(sim.get("task_id"))
        users.add(user)
        reward = (sim.get("reward_info") or {}).get("reward")
        if reward is not None:
            sim_rewards.append(float(reward))
        info = (sim.get("reward_info") or {}).get("info") or {}
        rewards = info.get("subtask_rewards") or {}
        if info.get("num_subtasks"):
            per_user_subtasks[user] = int(info["num_subtasks"])
        for value in rewards.values():
            flat.append(float(value))

    user_means = _user_means(_unit_rewards(payload))
    weighted = sum(user_means.values()) / len(user_means) if user_means else 0.0

    return {
        "users": len(users),
        "simulation_records": len(sim_rewards),
        "subtask_trial_units": len(flat),
        "subtask_clusters": sum(per_user_subtasks.values()) if per_user_subtasks else 0,
        "subtasks_per_user": dict(sorted(per_user_subtasks.items())),
        "mean_over_simulation_records": round(
            sum(sim_rewards) / len(sim_rewards), 4
        )
        if sim_rewards
        else 0.0,
        "user_weighted_avg": round(weighted, 4),
        "flat_subtask_trial_mean": round(sum(flat) / len(flat), 4) if flat else 0.0,
        "note": (
            "flat_subtask_trial_mean is the OFFICIAL Avg: it is the mean over "
            "(task_id, subtask_idx) units of that unit's per-trial mean, which "
            "is what _compute_subtask_pass_metrics/average_at_k in the vendored "
            "metrics compute (stock_avg4_8u: 0.2925). user_weighted_avg is a "
            "different quantity -- the equal-user-weight mean (0.2940) -- and "
            "must never be quoted as the official Avg. The two differ because "
            "users carry unequal subtask counts."
        ),
    }


def _user_means(units: dict[tuple[str, int], float]) -> dict[str, float]:
    grouped: dict[str, list[float]] = collections.defaultdict(list)
    for (user, _), reward in units.items():
        grouped[user].append(reward)
    return {user: sum(values) / len(values) for user, values in grouped.items()}


def _sample_sd(values: list[float], mean: float) -> float:
    if len(values) < 2:
        return 0.0
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(variance)


def decompose(payload: dict[str, Any]) -> dict[str, Any]:
    units = _unit_rewards(payload)
    if not units:
        return {"error": "no graded simulations"}

    grouped: dict[str, list[float]] = collections.defaultdict(list)
    for (user, _), reward in units.items():
        grouped[user].append(reward)
    means = {user: sum(values) / len(values) for user, values in grouped.items()}

    cohort_mean = sum(units.values()) / len(units)
    between_sd = _sample_sd(list(means.values()), cohort_mean)

    within_sq = 0.0
    within_dof = 0
    for user, values in grouped.items():
        if len(values) < 2:
            continue
        within_sq += sum((value - means[user]) ** 2 for value in values)
        within_dof += len(values) - 1
    within_sd = math.sqrt(within_sq / within_dof) if within_dof else 0.0

    flat = sum(1 for user, values in grouped.items() if len(set(values)) == 1)
    trials = collections.Counter(len(values) for values in grouped.values())
    n_users = len(grouped)

    between_se = between_sd / math.sqrt(n_users) if n_users else 0.0
    return {
        "units": len(units),
        "users": n_users,
        "trials_per_user": dict(trials),
        "cohort_mean": round(cohort_mean, 4),
        "between_user_sd": round(between_sd, 4),
        "within_user_sd": round(within_sd, 4),
        # SINGLE-ARM quantities. `unpaired_2se_over_users` describes how well one
        # arm's user mean is pinned down; it is NOT the threshold for a
        # two-arm difference, and it is NOT "the smallest change worth
        # building". For two independent arms of n users with equal
        # between-user sd, the difference has sd sqrt(2)*between_sd, so its
        # 2*SE is sqrt(2) times larger -- reported separately below.
        "unpaired_2se_over_users": round(2 * between_se, 4),
        "two_arm_unpaired_2se": round(2 * math.sqrt(2) * between_se, 4),
        "naive_within_2se": round(2 * within_sd / math.sqrt(len(units)), 4),
        "users_with_zero_trial_variance": flat,
        "note": (
            "unpaired_2se_over_users is a single-arm interval; two_arm_unpaired_2se "
            "is the corresponding two-arm difference interval (same n, equal "
            "variance). neither is a universal detection floor: subtasks share a "
            "user's history, so 100 metric units are not 100 independent samples. "
            "Cluster by user and prefer the paired, user-level contrast."
        ),
    }


def _official_units(payload: dict[str, Any]) -> dict[tuple[str, int], float]:
    """(task_id, subtask_idx) -> mean reward over that unit's trials.

    This is the official metric unit, and it is also the "same user, same task"
    block that a paired contrast may legitimately use. It is NOT the same as
    ``_unit_rewards``, which keys on (user, trial) and therefore treats the four
    trials of one script as four independent observations.
    """
    collected: dict[tuple[str, int], list[float]] = collections.defaultdict(list)
    for sim in payload.get("simulations", []):
        user = str(sim.get("task_id"))
        info = (sim.get("reward_info") or {}).get("info") or {}
        for name, value in (info.get("subtask_rewards") or {}).items():
            digits = "".join(char for char in str(name) if char.isdigit())
            if not digits:
                continue
            collected[(user, int(digits))].append(float(value))
    return {
        key: sum(values) / len(values) for key, values in collected.items() if values
    }


def paired(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Paired contrast on shared official units, plus a cluster-robust rollup.

    Two levels are reported because they answer different questions:

    * ``official_unit`` -- paired over (task_id, subtask_idx). Most powerful, but
      units inside one user share that user's history, so they are correlated and
      this interval is optimistic.
    * ``user_cluster`` -- each user's own mean over its units, then paired over
      shared users. This is cluster-robust with respect to within-user
      correlation and is the honest headline when the cohort is small.

    Pairing is by (user, subtask), never by seed: the same seed does not
    reproduce, so matching trial i to trial i claims precision the data does not
    support (E-057).
    """
    left = _official_units(baseline)
    right = _official_units(candidate)
    shared = sorted(set(left) & set(right))
    if len(shared) < 2:
        return {"error": "fewer than two shared official units", "shared_units": len(shared)}

    diffs = [right[key] - left[key] for key in shared]
    mean_diff = sum(diffs) / len(diffs)
    sd = _sample_sd(diffs, mean_diff)
    se = sd / math.sqrt(len(diffs))
    better = sum(1 for value in diffs if value > 0)
    worse = sum(1 for value in diffs if value < 0)
    same = sum(1 for value in diffs if value == 0)
    discordant = better + worse
    z = (better - discordant / 2) / math.sqrt(discordant / 4) if discordant else 0.0

    per_user: dict[str, list[float]] = collections.defaultdict(list)
    for (user, _idx), value in zip(shared, diffs):
        per_user[user].append(value)
    user_diffs = [sum(values) / len(values) for values in per_user.values()]
    user_mean = sum(user_diffs) / len(user_diffs) if user_diffs else 0.0
    user_sd = _sample_sd(user_diffs, user_mean)
    user_se = user_sd / math.sqrt(len(user_diffs)) if user_diffs else 0.0
    users_better = sum(1 for value in user_diffs if value > 0)
    users_worse = sum(1 for value in user_diffs if value < 0)

    return {
        "shared_units": len(shared),
        "official_unit": {
            "mean_diff": round(mean_diff, 4),
            "paired_sd": round(sd, 4),
            "paired_2se": round(2 * se, 4),
            "units_better": better,
            "units_worse": worse,
            "units_tied": same,
            "sign_test_z": round(z, 2),
        },
        "user_cluster": {
            "shared_users": len(user_diffs),
            "mean_diff": round(user_mean, 4),
            "paired_sd": round(user_sd, 4),
            "paired_2se": round(2 * user_se, 4),
            "users_better": users_better,
            "users_worse": users_worse,
        },
        "note": (
            "official_unit pairs (task_id, subtask_idx); units within a user are "
            "correlated, so prefer the user_cluster interval for the headline. "
            "Never pair by seed (E-057)."
        ),
    }


def users_needed(between_sd: float, target: float) -> int:
    """Users required for an unpaired 2*SE to fall below `target`."""
    if target <= 0 or between_sd <= 0:
        return 0
    return math.ceil((2 * between_sd / target) ** 2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoint", nargs="+")
    parser.add_argument("--target", type=float, default=0.05, help="delta to resolve")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    payloads = []
    for path in args.checkpoint:
        with open(path, encoding="utf-8") as handle:
            payloads.append(json.load(handle))

    report: dict[str, Any] = {}
    for path, payload in zip(args.checkpoint, payloads):
        report[path] = decompose(payload)
        report[path]["hierarchy"] = hierarchy(payload)
    if len(payloads) == 2:
        report["_paired"] = paired(payloads[0], payloads[1])

    first = report[args.checkpoint[0]]
    if "between_user_sd" in first:
        report["_users_needed"] = {
            "target": args.target,
            "users_for_unpaired_2se_below_target": users_needed(
                first["between_user_sd"], args.target
            ),
        }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    for key, value in report.items():
        print(f"== {key} ==")
        if isinstance(value, dict):
            for name, item in value.items():
                print(f"   {name}: {item}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Paired arm comparison for one or more checkpoints.

Zero-model. Pairs two runs unit by unit on ``(task_id, trial, subtask_id)`` and
reports the direction of every flip, not just the aggregate. Aggregate means on
a handful of users cannot resolve the effects this project cares about, so the
unit-level discordant counts are the primary output.

Usage:
    python scripts/paired_arms.py A.json B.json [--label A --label B]
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import pathlib
import sys
from typing import Any

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _user_weighted(
    units: dict[tuple[str, int, str], dict[str, Any]], shared: list[tuple[str, int, str]]
) -> float:
    """Official Avg shape: mean over users of each user's mean subtask reward."""
    grouped: dict[str, list[float]] = {}
    for key in shared:
        grouped.setdefault(key[0], []).append(units[key]["reward"])
    if not grouped:
        return 0.0
    per_user = [sum(values) / len(values) for values in grouped.values()]
    return sum(per_user) / len(per_user)


def _official_unit_rewards(
    units: dict[tuple[str, int, str], dict[str, Any]]
) -> dict[tuple[str, int], float]:
    """Aggregate (task_id, trial, subtask_id) records to the official unit.

    The official unit is ``(task_id, subtask_idx)`` observed across trials
    (``_compute_subtask_pass_metrics`` in the vendored metrics). Four trials of a
    user replay the *same* script, so treating 400 per-trial records as 400
    independent observations is pseudoreplication: it inflates every
    significance claim by roughly the trial count. Aggregating first, then
    pairing, is what makes a sign test on this cohort meaningful.
    """
    collected: dict[tuple[str, int], list[float]] = {}
    for (task_id, _trial, _sid), record in units.items():
        idx = record.get("subtask_idx")
        if idx is None:
            continue
        collected.setdefault((task_id, int(idx)), []).append(record["reward"])
    return {
        key: sum(values) / len(values) for key, values in collected.items() if values
    }


def _sign_test(wins: int, losses: int) -> tuple[float, float]:
    """Exact-ish two-sided sign test; returns (z, p). Ties are excluded."""
    discordant = wins + losses
    if not discordant:
        return 0.0, 1.0
    z = (wins - discordant / 2) / math.sqrt(discordant / 4)
    # Normal approximation to the two-sided binomial test.
    p = math.erfc(abs(wins - discordant / 2) / math.sqrt(discordant / 2))
    return z, p


def load_units(path: pathlib.Path) -> dict[tuple[str, int, str], dict[str, Any]]:
    """Map (task_id, trial, subtask_id) -> observable unit record."""
    data = json.loads(path.read_text(encoding="utf-8"))
    units: dict[tuple[str, int, str], dict[str, Any]] = {}
    for sim in data.get("simulations") or []:
        task_id = str(sim.get("task_id"))
        trial = sim.get("trial")
        info = (sim.get("reward_info") or {}).get("info") or {}
        rewards = info.get("subtask_rewards") or {}
        # The guard snapshot is per-simulation; subtask-level detail comes from
        # the integrity trajectories, so pair on subtask_id and read the sim
        # level guard state once.
        guard = (sim.get("states") or {}).get("landing_guard") or {}
        for order, tr in enumerate(
            (sim.get("states") or {}).get("integrity_subtask_trajectories") or []
        ):
            sid = tr.get("subtask_id")
            key = f"subtask_{tr.get('subtask_idx')}_reward"
            if key not in rewards:
                continue
            tool_names = [
                c["name"]
                for m in (tr.get("messages") or [])
                for c in (m.get("tool_calls") or [])
                if isinstance(c, dict) and c.get("name")
            ]
            wrote = any(
                n.startswith("create_") or n in {"instore_book", "instore_reservation"}
                for n in tool_names
            )
            units[(task_id, trial if trial is not None else -1, str(sid))] = {
                "reward": float(rewards[key]),
                "subtask_idx": tr.get("subtask_idx"),
                "wrote": wrote,
                "searches": sum(1 for n in tool_names if "search" in n),
                "messages": len(tr.get("messages") or []),
                "term": tr.get("termination_reason"),
                "guard": guard,
                # The guard snapshot is written once per simulation, not per
                # subtask, so events must be de-duplicated on this key or they
                # are counted once per subtask in the same simulation.
                # ``order`` is the position of this subtask in the orchestrator's
                # sequence, which is the key used by the agent's per-subtask
                # guard attribution.
                "order": order,
                "sim_key": (task_id, trial, str(sim.get("id"))),
            }
    return units


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--label", action="append", default=None)
    ap.add_argument(
        "--trial",
        type=int,
        default=None,
        help=(
            "restrict to one trial index; use when the control checkpoint holds "
            "more trials than the treatment (e.g. cached baseline at 4 trials "
            "vs a single-trial run)"
        ),
    )
    args = ap.parse_args()

    path_a, path_b = pathlib.Path(args.a), pathlib.Path(args.b)
    labels = (args.label or ["A", "B"])[:2]
    units_a, units_b = load_units(path_a), load_units(path_b)
    if args.trial is not None:
        units_a = {k: v for k, v in units_a.items() if k[1] == args.trial}
        units_b = {k: v for k, v in units_b.items() if k[1] == args.trial}
    shared = sorted(set(units_a) & set(units_b))

    print(f"A = {labels[0]:8} {path_a.name}  units={len(units_a)}")
    print(f"B = {labels[1]:8} {path_b.name}  units={len(units_b)}")
    print(f"paired units = {len(shared)}   (A-only {len(set(units_a) - set(units_b))}, "
          f"B-only {len(set(units_b) - set(units_a))})")
    if not shared:
        raise SystemExit("no paired units; refusing to report an unpaired comparison")

    mean_a = sum(units_a[k]["reward"] for k in shared) / len(shared)
    mean_b = sum(units_b[k]["reward"] for k in shared) / len(shared)
    # The official metric is `_compute_subtask_pass_metrics` in
    # vita/metrics/agent_metrics.py: its unit is (task_id, subtask_idx) and
    # average_at_k is a plain mean over that unit's trials. Averaging those unit
    # means reproduces the flat (user, trial, subtask) mean whenever every
    # cluster contributes all trials, which is the balanced case. The
    # equal-user-weight view is a different, secondary quantity: it is what
    # CLAUDE.md reports as the "user-level 4-trial mean" (0.2940 vs official
    # 0.2925 on the stock cohort).
    weighted_a = _user_weighted(units_a, shared)
    weighted_b = _user_weighted(units_b, shared)
    wins = [k for k in shared if units_b[k]["reward"] > units_a[k]["reward"]]
    losses = [k for k in shared if units_b[k]["reward"] < units_a[k]["reward"]]
    both = [k for k in shared if units_a[k]["reward"] == units_b[k]["reward"] == 1.0]
    neither = [k for k in shared if units_a[k]["reward"] == units_b[k]["reward"] == 0.0]
    discordant = len(wins) + len(losses)

    print()
    print(f"{'metric':30} {labels[0]:>9} {labels[1]:>9}")
    print(f"{'official Avg@4 shape':30} {mean_a:9.4f} {mean_b:9.4f}")
    print(f"{'equal-user-weight':30} {weighted_a:9.4f} {weighted_b:9.4f}")
    print(f"{'delta official (B-A)':30} {'':9} {mean_b - mean_a:+9.4f}")
    print(f"{'delta equal-weight (B-A)':30} {'':9} {weighted_b - weighted_a:+9.4f}")
    print()
    print(f"B fixes  (A=0 -> B=1): {len(wins)}")
    print(f"B breaks (A=1 -> B=0): {len(losses)}")
    print(f"both pass / both fail : {len(both)} / {len(neither)}")
    print(f"discordant pairs      : {discordant}")

    # ---- PRIMARY: paired contrast on official units -----------------------
    # The block above counts per-trial records, which are replicates of one
    # script, not independent samples. The verdict below aggregates each
    # (task_id, subtask_idx) over its trials first, which is the official unit,
    # and additionally rolls up to per-user means so within-user correlation is
    # handled. Both must be reported; a claim resting only on the per-trial
    # counts is pseudoreplicated.
    official_a = _official_unit_rewards(units_a)
    official_b = _official_unit_rewards(units_b)
    official_shared = sorted(set(official_a) & set(official_b))
    print()
    print("== PRIMARY: paired contrast on official units (task_id, subtask_idx) ==")
    if len(official_shared) < 2:
        print("   fewer than two shared official units; no verdict")
    else:
        o_wins = [k for k in official_shared if official_b[k] > official_a[k]]
        o_losses = [k for k in official_shared if official_b[k] < official_a[k]]
        o_ties = len(official_shared) - len(o_wins) - len(o_losses)
        z, p = _sign_test(len(o_wins), len(o_losses))
        mean_oa = sum(official_a[k] for k in official_shared) / len(official_shared)
        mean_ob = sum(official_b[k] for k in official_shared) / len(official_shared)
        print(f"   shared official units : {len(official_shared)}  (ties {o_ties})")
        print(f"   A={labels[0]} {mean_oa:.4f}   B={labels[1]} {mean_ob:.4f}   "
              f"delta(B-A) {mean_ob - mean_oa:+.4f}")
        print(f"   B fixes / B breaks    : {len(o_wins)} / {len(o_losses)}")
        print(f"   sign test             : z={z:+.2f}  p={p:.4f}"
              f"   {'PASS (fixes>breaks, p<0.05)' if len(o_wins) > len(o_losses) and p < 0.05 else 'not significant'}")

        # Cluster-robust rollup: one difference per user, so units inside a user
        # no longer contribute independently.
        per_user: dict[str, list[float]] = {}
        for key in official_shared:
            per_user.setdefault(key[0], []).append(official_b[key] - official_a[key])
        user_diffs = [sum(v) / len(v) for v in per_user.values()]
        u_better = sum(1 for d in user_diffs if d > 0)
        u_worse = sum(1 for d in user_diffs if d < 0)
        print(f"   user-cluster rollup   : users {len(user_diffs)}, "
              f"better {u_better}, worse {u_worse}, "
              f"mean per-user delta {sum(user_diffs) / len(user_diffs):+.4f}")

    print()
    print("== per-trial listings below are NOT independent (attribution only) ==")
    if discordant:
        # Retained for attribution, but this z is pseudoreplicated: the four
        # trials of a user replay one script.
        z = (len(wins) - discordant / 2) / math.sqrt(discordant / 4)
        print(f"per-trial sign-test z (do not quote as significance): {z:+.2f}")
    else:
        print("per-trial sign-test   : n/a (no discordant pairs)")

    if wins:
        print("\n-- B fixes --")
        for k in wins:
            a, b = units_a[k], units_b[k]
            print(f"  {k[2]:18} A(search={a['searches']}, wrote={a['wrote']}) -> "
                  f"B(search={b['searches']}, wrote={b['wrote']})")
    if losses:
        print("\n-- B breaks --")
        for k in losses:
            a, b = units_a[k], units_b[k]
            print(f"  {k[2]:18} A(search={a['searches']}, wrote={a['wrote']}) -> "
                  f"B(search={b['searches']}, wrote={b['wrote']})")

    # Guard attribution. The agent records per-subtask state, so a unit where
    # the guard stayed silent is distinguishable from one where it had no reason
    # to fire -- the gap that made the first P722245 experiment unattributable.
    print("\n-- arm B guard attribution --")
    totals = collections.Counter()
    seen_sims: set[tuple] = set()
    rows: list[str] = []
    legacy = False
    for k in shared:
        snap = units_b[k]["guard"]
        if not snap:
            continue
        sim_key = units_b[k]["sim_key"]
        first_of_sim = sim_key not in seen_sims
        seen_sims.add(sim_key)
        per = (snap.get("per_subtask") or {}).get(str(units_b[k]["order"]))
        if per is None:
            legacy = True
            if not first_of_sim:
                continue
            for e in snap.get("events") or []:
                totals["fired"] += 1
                totals["budget_type" if e.get("budget") else "timing_type"] += 1
                totals["accepted" if e.get("accepted") else "rejected"] += 1
            continue
        ev = per.get("events") or []
        totals["fired"] += len(ev)
        totals["units_commit"] += 1 if per.get("is_commit") else 0
        totals["units_with_candidates"] += 1 if per.get("candidates_seen") else 0
        totals["units_that_wrote"] += 1 if per.get("wrote") else 0
        for e in ev:
            totals["budget_type" if e.get("budget") else "timing_type"] += 1
            totals["accepted" if e.get("accepted") else "rejected"] += 1
        rows.append(
            f"  {k[2]:18} commit={'Y' if per.get('is_commit') else 'n'}"
            f" cand={'Y' if per.get('candidates_seen') else 'n'}"
            f" wrote={'Y' if per.get('wrote') else 'n'}"
            f" fired={len(ev)}"
            f"  | {str(per.get('instruction',''))[:34]}"
        )
    if legacy:
        print("  NOTE: this artifact predates per-subtask attribution; only a")
        print("  de-duplicated simulation-level total is available.")
    for r in rows:
        print(r)
    print(f"  totals: {dict(totals) or 'none recorded'}")


if __name__ == "__main__":
    main()

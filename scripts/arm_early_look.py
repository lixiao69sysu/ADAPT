"""Early look at a partially completed arm, per user.

Purpose: decide whether an in-flight run is worth continuing, *without* letting
run-to-run noise masquerade as a regression. This is deliberately NOT the
pre-registered verdict device (that is ``scripts/adapt_arm_report.py``): it
issues no verdict, applies no cohort or config gate, and can be run on one user.

The calibration that makes it usable comes from the baseline itself. Each user's
four baseline trials are four independent draws of that user's own script, so
their spread IS that user's single-trial noise. Measured on stock_dev.json:

    P1  0.1538 0.3077 0.3846 0.3077   mean 0.2885  range 0.2308
    P5  0.1667 0.2500 0.0833 0.0000   mean 0.1250  range 0.2500
    P4  0.2727 x4                      mean 0.2727  range 0.0000

So a single user's single trial moves by up to ~0.25 on its own. The flag below
therefore fires only when the arm is worse than *everything that user ever did*
across its four baseline trials -- a self-calibrating bar, not a fixed one.

Usage:
    python scripts/arm_early_look.py --arm data/simulations/adapt_dev_1t.json
    python scripts/arm_early_look.py --arm ... --baseline ...
"""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def per_user_slices(path: pathlib.Path) -> dict[str, dict[int, float]]:
    """user -> {trial: mean subtask reward} from a checkpoint."""
    data = json.loads(path.read_text(encoding="utf-8"))
    collected: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    for sim in data.get("simulations") or []:
        info = (sim.get("reward_info") or {}).get("info") or {}
        breakdown = info.get("subtask_rewards") or {}
        if not breakdown:
            continue
        trial = sim.get("trial", 0) or 0
        collected[str(sim.get("task_id"))][trial].extend(
            float(v) for v in breakdown.values()
        )
    return {
        user: {trial: sum(v) / len(v) for trial, v in trials.items() if v}
        for user, trials in collected.items()
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True)
    ap.add_argument("--baseline", default="data/simulations/stock_dev.json")
    ap.add_argument(
        "--margin",
        type=float,
        default=0.0,
        help=(
            "extra slack on top of the user's own baseline range before flagging; "
            "0 means 'worse than anything this user ever did in 4 baseline trials'"
        ),
    )
    args = ap.parse_args()

    arm_path = pathlib.Path(args.arm)
    if not arm_path.exists():
        print(f"no arm checkpoint yet: {arm_path} (nothing to compare)")
        return
    data = json.loads(arm_path.read_text(encoding="utf-8"))
    sims = data.get("simulations") or []
    tasks = [str(t) for t in (data.get("tasks") or [])]
    print(f"arm      {arm_path.name}")
    info = data.get("info") or {}
    print(f"config   agent={info.get('agent_kind')} memory={info.get('memory_type')} "
          f"profile_summary={info.get('profile_summary')} "
          f"switches={info.get('adapt_agent')}")
    print(f"progress {len(sims)} of {len(tasks)} users written")
    if not sims:
        print("no user has finished yet; nothing to compare")
        return

    base = per_user_slices(pathlib.Path(args.baseline))
    arm = per_user_slices(arm_path)

    print()
    print(f"{'user':10} {'n':>3} {'arm':>7} {'base_t0':>8} {'base4':>7} "
          f"{'d(base4)':>9} {'own range':>10}  flag")
    flagged = []
    deltas = []
    for user in sorted(arm):
        trials = arm[user]
        if len(trials) != 1:
            print(f"{user:10} !! arm has {len(trials)} trials; early look assumes 1")
        m_arm = statistics.mean(trials.values())
        arm_sim = next(
            (s for s in sims if str(s.get("task_id")) == user), {}
        )
        n = len(((arm_sim.get("reward_info") or {}).get("info") or {})
                .get("subtask_rewards") or {})
        if user not in base:
            print(f"{user:10} {n:>3} {m_arm:>7.4f} {'--':>8} {'--':>7} {'--':>9} "
                  f"{'--':>10}  not in baseline")
            continue
        b_slices = [base[user][t] for t in sorted(base[user])]
        m_base4 = statistics.mean(b_slices)
        m_t0 = base[user].get(0, float("nan"))
        own_range = max(b_slices) - min(b_slices)
        delta = m_arm - m_base4
        deltas.append(delta)
        bad = delta < -(own_range + args.margin)
        if bad:
            flagged.append((user, delta, own_range))
        print(f"{user:10} {n:>3} {m_arm:>7.4f} {m_t0:>8.4f} {m_base4:>7.4f} "
              f"{delta:>+9.4f} {own_range:>10.4f}  {'WORSE THAN EVER' if bad else ''}")

    if deltas:
        print()
        print(f"completed users: {len(deltas)}  mean delta vs baseline 4-trial mean "
              f"{statistics.mean(deltas):+.4f}")
        print("calibration: one user's own single-trial range above is pure noise, so a "
              "single-user delta inside it says nothing.")
    if flagged:
        print()
        for user, delta, own_range in flagged:
            print(f"STOP SIGNAL: {user} is {delta:+.4f} vs its baseline mean, outside "
                  f"its own {own_range:.4f} baseline range.")
        print("Consider stopping: this user did worse than in any of its four "
              "baseline trials, and the gap exceeds its own measured jitter.")
    elif deltas:
        print()
        print("No user is outside its own baseline range yet; a stop would be noise.")


if __name__ == "__main__":
    main()

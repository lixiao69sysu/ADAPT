"""Anatomy of the arm's +0.0375: is it noise, and if not, where does it sit?

Written in response to "the increase can't all be noise" -- a fair challenge that
deserves arithmetic rather than a restatement of the noise floor. Four angles:

1. User-level paired test: the dev cohort, one delta each.
2. A null distribution built from the baseline itself. The baseline's four trials
   are four independent draws of the same condition, so each slice's deviation
   from the four-trial mean IS what a single-trial arm looks like under the null.
   Comparing +0.0375 against that distribution is a real test; quoting a floor is
   not.
3. Structure of the flips: by domain, by baseline pass pattern, by user, with the
   instructions of the fixes that landed on never-passed subtasks.
4. The users whose baseline user-mean never varied -- whether that means their
   subtasks are stable, or the churn cancels out.

All weights are the official unit weighting (mean over the 100 official units),
stated explicitly, because unit weighting (0.2925) and equal-user weighting
(0.2940) are different quantities on the baseline.

Zero-model; no rubric, target or evaluator data is read.
"""

from __future__ import annotations

import json
import math
import pathlib
import statistics
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ARM = pathlib.Path("data/simulations/adapt_dev_1t.json")
BASE = pathlib.Path("data/simulations/stock_dev.json")


def units(path):
    """(task_id, subtask_key) -> {trial: reward}."""
    data = json.loads(path.read_text(encoding="utf-8"))
    out = defaultdict(dict)
    for sim in data.get("simulations") or []:
        breakdown = ((sim.get("reward_info") or {}).get("info") or {}).get(
            "subtask_rewards"
        ) or {}
        trial = sim.get("trial", 0) or 0
        for key, reward in breakdown.items():
            out[(str(sim["task_id"]), str(key))][trial] = float(reward)
    return out


def official(unit_map, trial=None):
    """(task_id, subtask_key) -> mean reward (over one trial if given)."""
    out = {}
    for key, per_trial in unit_map.items():
        vals = [v for t, v in per_trial.items() if trial is None or t == trial]
        if vals:
            out[key] = sum(vals) / len(vals)
    return out


def domain_of(messages):
    """Domain from observed tool names; there is no domain field in the trace."""
    names = [
        c.get("name", "")
        for m in messages or []
        for c in (m.get("tool_calls") or [])
        if isinstance(c, dict)
    ]
    for n in names:
        if n.startswith("delivery_"):
            return "delivery"
        if n.startswith("instore_"):
            return "instore"
    for n in names:
        if any(k in n for k in ("hotel", "flight", "train", "attraction", "taxi")):
            return "ota"
    return "?"


def idx_of(key):
    tail = key.rsplit("_", 2)[-2]
    return int(tail) if tail.isdigit() else None


def main() -> None:
    A, B = units(ARM), units(BASE)
    arm_raw = json.loads(ARM.read_text(encoding="utf-8"))
    A_off = official(A)
    B_off = official(B)

    meta = {}
    for sim in arm_raw.get("simulations") or []:
        for tr in (sim.get("states") or {}).get("integrity_subtask_trajectories") or []:
            instr = ""
            for msg in tr.get("messages") or []:
                if msg.get("role") == "user":
                    instr = (msg.get("content") or "").replace("\n", " ")[:34]
                    break
            meta[(str(sim["task_id"]), tr.get("subtask_idx"))] = (
                domain_of(tr.get("messages")),
                instr,
            )

    # ---------------- 1. user-level paired test ---------------------------
    print("=" * 78)
    print("1. USER-LEVEL PAIRED TEST  (n = the dev cohort, one delta each)")
    per_arm, per_base = defaultdict(list), defaultdict(list)
    for (tid, _k), per_trial in A.items():
        per_arm[tid].extend(per_trial.values())
    for (tid, _k), per_trial in B.items():
        per_base[tid].extend(per_trial.values())
    deltas = [statistics.mean(per_arm[t]) - statistics.mean(per_base[t]) for t in per_arm]
    n = len(deltas)
    mean_d = statistics.mean(deltas)
    sd_d = statistics.stdev(deltas)
    se_d = sd_d / math.sqrt(n)
    t_stat = mean_d / se_d
    p_t = math.erfc(abs(t_stat) / math.sqrt(2))
    up = sum(1 for d in deltas if d > 0)
    down = sum(1 for d in deltas if d < 0)
    p_sign = min(1.0, 2 * sum(math.comb(n, k) for k in range(max(up, down), n + 1)) / 2 ** n)
    print(f"   deltas           : {[round(d, 4) for d in deltas]}")
    print(f"   mean {mean_d:+.4f}   sd {sd_d:.4f}   SE {se_d:.4f}")
    print(f"   paired t         : t={t_stat:+.2f}  p={p_t:.3f}")
    print(f"   sign test        : {up} up / {down} down / {n - up - down} flat  p={p_sign:.2f}")
    print(f"   (baseline between-user sd = 0.0823; a 1-trial arm adds its own noise,")
    print(f"    so the observed per-user delta sd is {sd_d:.4f}, i.e. larger)")

    # ---------------- 2. null from the baseline's own trials --------------
    print()
    print("=" * 78)
    print("2. NULL DISTRIBUTION FROM THE BASELINE'S OWN FOUR TRIALS")
    print("   (unit-weighted, so it is the same quantity as the arm's 0.3300)")
    B_pooled_per_trial = {}
    for tr in range(4):
        vals = [v for per_trial in B.values() for t, v in per_trial.items() if t == tr]
        B_pooled_per_trial[tr] = sum(vals) / len(vals)
    four = statistics.mean(B_pooled_per_trial.values())
    slice_sd = statistics.stdev([B_pooled_per_trial[t] for t in sorted(B_pooled_per_trial)])
    arm_pooled = sum(A_off.values()) / len(A_off)
    dev = arm_pooled - four
    print(f"   baseline slice pooled : "
          f"{ {t: round(B_pooled_per_trial[t], 4) for t in sorted(B_pooled_per_trial)} }")
    print(f"   baseline 4-trial mean : {four:.4f}")
    print(f"   slice deviations      : "
          f"{[round(B_pooled_per_trial[t] - four, 4) for t in sorted(B_pooled_per_trial)]}")
    print(f"   slice sd              : {slice_sd:.4f}")
    print(f"   ARM pooled            : {arm_pooled:.4f}")
    print(f"   arm deviation         : {dev:+.4f}  = {dev / slice_sd:.2f} slice-sd")
    print("   -> A null arm deviates by this distribution. Four draws is a weak")
    print("      estimate, but the arm sits inside the range a null arm produces;")
    print("      it is at the high end, not outside it.")

    # ---------------- 3. structure of the flips ---------------------------
    print()
    print("=" * 78)
    print("3. STRUCTURE OF THE FLIPS  (arm vs baseline trial-0 slice, official units)")
    B_t0 = official(B, trial=0)
    fixes, breaks = [], []
    for key in sorted(A_off):
        if key not in B_t0:
            continue
        inner = B[key]
        pattern = "".join(str(int(inner[t])) for t in sorted(inner))
        idx = idx_of(key[1])
        domain, instr = meta.get((key[0], idx), ("?", ""))
        row = (key[0], idx, domain, pattern, A_off[key], B_t0[key], instr)
        if A_off[key] > B_t0[key]:
            fixes.append(row)
        elif A_off[key] < B_t0[key]:
            breaks.append(row)

    def counts(rows, i):
        c = defaultdict(int)
        for r in rows:
            c[r[i]] += 1
        return dict(sorted(c.items(), key=lambda kv: str(kv[0])))

    print(f"   fixes {len(fixes)}  by domain {counts(fixes, 2)}")
    print(f"         by user   {counts(fixes, 0)}")
    print(f"         on subtasks the baseline never passed (0/4): "
          f"{sum(1 for r in fixes if r[3] == '0000')}")
    print(f"   breaks {len(breaks)}  by domain {counts(breaks, 2)}")
    print(f"          by user   {counts(breaks, 0)}")
    print(f"          on subtasks the baseline always passed (4/4): "
          f"{sum(1 for r in breaks if r[3] == '1111')}")

    print()
    print("   -- the fixes that landed on never-passed (0/4) subtasks --")
    for tid, idx, domain, pattern, am, bm, instr in fixes:
        if pattern == "0000":
            print(f"     {tid} idx{idx:<3} {domain:<9} {instr}")

    print()
    print(f"   {'domain':10} {'n':>3} {'arm':>7} {'base_t0':>8} {'delta':>8} {'base4':>7}")
    per_dom = defaultdict(list)
    for key in A_off:
        idx = idx_of(key[1])
        per_dom[meta.get((key[0], idx), ("?",))[0]].append(key)
    for domain in sorted(per_dom):
        keys = per_dom[domain]
        ma = statistics.mean(A_off[k] for k in keys)
        m0 = statistics.mean(B_t0[k] for k in keys if k in B_t0)
        m4 = statistics.mean(B_off[k] for k in keys if k in B_off)
        print(f"   {domain:10} {len(keys):>3} {ma:>7.4f} {m0:>8.4f} {ma - m0:>+8.4f} {m4:>7.4f}")

    # ---------------- 4. users with a constant baseline mean --------------
    print()
    print("=" * 78)
    print("4. USERS WHOSE BASELINE USER-MEAN NEVER VARIED")
    print("   (a constant mean can still hide churn; this separates the two)")
    for tid in sorted(per_base):
        keys = [k for k in B if k[0] == tid]
        slice_means = [
            statistics.mean(B[k][t] for k in keys if t in B[k]) for t in range(4)
        ]
        if max(slice_means) - min(slice_means) > 1e-9:
            continue
        unstable = [
            k for k in keys if len({int(B[k][t]) for t in B[k]}) > 1
        ]
        a_rate = statistics.mean(A_off[k] for k in keys if k in A_off)
        print(f"   {tid}: baseline {slice_means[0]:.4f} in all 4 trials; "
              f"individually unstable subtasks {len(unstable)}/{len(keys)}; "
              f"arm {a_rate:.4f}")
        for k in unstable:
            idx = idx_of(k[1])
            print(f"        idx{idx:<3} baseline "
                  f"{''.join(str(int(B[k][t])) for t in sorted(B[k]))} "
                  f"-> arm {A_off.get(k, float('nan')):.0f}  "
                  f"{meta.get((tid, idx), ('?', ''))[1]}")


if __name__ == "__main__":
    main()

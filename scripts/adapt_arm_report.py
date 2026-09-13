"""One-shot, zero-model verdict report for the 8-user ADAPT arm.

Implements the rules pre-registered (before the arm data existed) in
``docs/ADAPT_ARM_PREREG_2026-09-13.md``. Nothing here decides anything the
preregistration did not already fix; the script exists so the verdict is a
command, not a reading of a table.

Usage:
    python scripts/adapt_arm_report.py `
        --arm data/simulations/adapt8_1t.json `
        --baseline data/simulations/stock_avg4_8u.json

Unit semantics (vendored, vita/metrics/agent_metrics.py):
    official unit = (task_id, subtask_idx); its reward is the plain mean of its
    per-trial binary rewards; strict success = subtask reward == 1.0.

For a 1-trial arm the unit reward is one draw, so the like-for-like baseline
reference is the *trial-0 slice*, not the 4-trial Avg@4. Both are printed; the
verdict thresholds were fixed against the pooled quantity.
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import statistics
import sys
from collections import Counter, defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

EXPECTED_TASKS = (
    "E057330",
    "E941775",
    "J365414",
    "M793481",
    "O309411",
    "P722245",
    "Q089190",
    "U000828",
)
EXPECTED_CONFIG = {
    "agent_kind": "adapt",
    "memory_type": "adapt",
    "profile_summary": True,
    "num_trials": 1,
}
EXPECTED_SWITCHES = {
    "proactive_loop": True,
    "candidate_evidence": False,
    "task_state": False,
}
# Pre-registered thresholds, on the pooled (100-unit) average.
LOW_THRESHOLD = 0.32
TARGET = 0.35
NOISE_FLOOR = 0.0582  # 8-user unpaired 2*SE, the cohort's resolution limit


def load(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def units_of(data: dict) -> dict[tuple[str, int, str], float]:
    """(task_id, trial, subtask_key) -> reward, straight from subtask_rewards."""
    out: dict[tuple[str, int, str], float] = {}
    for sim in data.get("simulations") or []:
        info = (sim.get("reward_info") or {}).get("info") or {}
        breakdown = info.get("subtask_rewards") or {}
        trial = sim.get("trial", 0) or 0
        for key, reward in breakdown.items():
            out[(str(sim.get("task_id")), trial, str(key))] = float(reward)
    return out


def official_units(
    units: dict[tuple[str, int, str], float], trial: int | None = None
) -> dict[tuple[str, str], float]:
    """Aggregate per-trial records to the official unit (task_id, subtask_key)."""
    collected: dict[tuple[str, str], list[float]] = defaultdict(list)
    for (task_id, t, key), reward in units.items():
        if trial is not None and t != trial:
            continue
        collected[(task_id, key)].append(reward)
    return {k: sum(v) / len(v) for k, v in collected.items() if v}


def sign_test(wins: int, losses: int) -> tuple[float, float]:
    discordant = wins + losses
    if not discordant:
        return 0.0, 1.0
    z = (wins - discordant / 2) / math.sqrt(discordant / 4)
    p = math.erfc(abs(wins - discordant / 2) / math.sqrt(discordant / 2))
    return z, p


def required_net(discordant: int) -> int:
    """Smallest net (wins - losses) that clears p < 0.05 at this discordance.

    The analytic bound net >= 1.96*sqrt(n) is optimistic because the device uses
    the normal approximation, so the minimum is found by search. Returns 0 when
    no split of this many discordant pairs can clear the gate.
    """
    for wins in range(discordant // 2, discordant + 1):
        if sign_test(wins, discordant - wins)[1] < 0.05:
            return 2 * wins - discordant
    return 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument(
        "--allow-partial-cohort",
        action="store_true",
        help="restrict to the arm's users (smoke validation only); not a cohort claim",
    )
    ap.add_argument(
        "--ignore-config-gate",
        action="store_true",
        help=(
            "validate the reader on a checkpoint that is not the arm (e.g. the "
            "baseline itself); never use this to produce a verdict"
        ),
    )
    args = ap.parse_args()

    arm_path, base_path = pathlib.Path(args.arm), pathlib.Path(args.baseline)
    arm, base = load(arm_path), load(base_path)
    problems: list[str] = []

    # ---- 0. configuration -------------------------------------------------
    print("=" * 78)
    print(f"ARM      {arm_path.name}")
    info = arm.get("info") or {}
    for key, want in EXPECTED_CONFIG.items():
        got = info.get(key)
        flag = "ok " if got == want else "!! "
        if got != want:
            problems.append(f"info[{key!r}] = {got!r}, expected {want!r}")
        print(f"  {flag}{key:22} = {got!r}")
    switches = info.get("adapt_agent") or {}
    for key, want in EXPECTED_SWITCHES.items():
        got = switches.get(key)
        flag = "ok " if got == want else "!! "
        if got != want:
            problems.append(f"info['adapt_agent'][{key!r}] = {got!r}, expected {want!r}")
        print(f"  {flag}adapt_agent.{key:13} = {got!r}")
    print(f"      models               = agent={info.get('llm_agent')} "
          f"user={info.get('llm_user')} evaluator={info.get('llm_evaluator')}")

    # ---- 1. cohort identity (E-092) ---------------------------------------
    arm_tasks = sorted(str(t) for t in (arm.get("tasks") or []))
    base_tasks = sorted(str(t) for t in (base.get("tasks") or []))
    print()
    print(f"cohort   arm={arm_tasks}")
    print(f"         base={base_tasks}")
    if arm_tasks == base_tasks:
        print("         identical task sets -> cohorted comparison")
    elif args.allow_partial_cohort:
        print("         !! partial cohort override: restricting to the arm's users; "
              "this is NOT a cohort claim")
    else:
        problems.append("arm and baseline cohorts differ (E-092): refusing")

    # ---- 2. coverage ------------------------------------------------------
    print()
    print("=" * 78)
    sims = arm.get("simulations") or []
    seen = [str(s.get("task_id")) for s in sims]
    statuses = Counter(str(s.get("evaluation_status", "?")) for s in sims)
    print(f"coverage distinct_users={len(set(seen))}/{len(arm_tasks)}  "
          f"simulations={len(sims)}  evaluation_status={dict(statuses)}")
    missing = [t for t in arm_tasks if t not in seen]
    if missing:
        problems.append(f"users missing from the arm checkpoint: {missing}")
        print(f"  !! missing users: {missing}")
    else:
        print("  ok every cohort user is present")
    bad = [s.get("task_id") for s in sims if s.get("evaluation_status") not in (None, "ok")]
    if bad:
        print(f"  !! non-ok evaluation_status on: {bad} "
              "(those units count as reward 0 in the official metric)")

    if problems and not args.ignore_config_gate:
        print()
        print("!" * 78)
        print("ABORT: the arm does not match the pre-registered measurement")
        for p in problems:
            print(f"  - {p}")
        raise SystemExit(2)
    if problems and args.ignore_config_gate:
        print()
        print("!! config/cohort gate ignored for reader validation; this output is "
              "NOT a verdict")
        for p in problems:
            print(f"   - {p}")

    # ---- 3. metric --------------------------------------------------------
    a_units = units_of(arm)
    b_units = units_of(base)
    a_off = official_units(a_units)
    b_off = official_units(b_units)
    b_off_t0 = official_units(b_units, trial=0)
    if args.allow_partial_cohort:
        keep = set(arm_tasks)
        a_off = {k: v for k, v in a_off.items() if k[0] in keep}
        b_off = {k: v for k, v in b_off.items() if k[0] in keep}
        b_off_t0 = {k: v for k, v in b_off_t0.items() if k[0] in keep}

    a_pooled = statistics.mean(a_off.values())
    b_pooled = statistics.mean(b_off.values())
    b_t0 = statistics.mean(b_off_t0.values())
    print()
    print("=" * 78)
    print(f"official units: arm={len(a_off)}  baseline={len(b_off)}")
    print(f"  arm  pooled Avg@1 (100-unit mean)     = {a_pooled:.4f}")
    print(f"  base pooled Avg@4 (all trials)        = {b_pooled:.4f}")
    print(f"  base trial-0 slice Avg@1 (same shape) = {b_t0:.4f}")
    print(f"  delta vs pooled base                  = {a_pooled - b_pooled:+.4f}")
    print(f"  delta vs trial-0 base                 = {a_pooled - b_t0:+.4f}")

    # reachability, from the baseline's own unit distribution
    per_unit_trials: dict[tuple[str, str], list[float]] = defaultdict(list)
    for (tid, _t, k), v in b_units.items():
        per_unit_trials[(tid, k)].append(v)
    counts = Counter(sum(1 for v in vals if v == 1.0)
                     for vals in per_unit_trials.values())
    need = (TARGET - b_pooled) * len(b_off)
    print(f"  base unit pass counts over trials: "
          f"{ {c: counts[c] for c in sorted(counts)} }")
    print(f"  to reach {TARGET}: net +{need:.4f} unit-score "
          f"= ~{math.ceil(need)} units fully resolved, or ~{math.ceil(need * 4)} rescued trials")

    # ---- 4. verdict (pre-registered) --------------------------------------
    print()
    print("-" * 78)
    delta_pooled = a_pooled - b_pooled
    need_metric = need / len(b_off)
    # The thresholds below are calibrated for the full 8-user / 100-unit cohort.
    # Issuing a verdict from a gate-ignored or partial-cohort run would be exactly
    # the kind of number this repository has been burned by, so those modes report
    # the arithmetic and refuse the verdict.
    verdict_allowed = not (args.ignore_config_gate or args.allow_partial_cohort)
    if not verdict_allowed:
        print("VALIDATION MODE: the config/cohort gate was bypassed, so NO VERDICT is issued.")
        print(f"  (for reference only: pooled {a_pooled:.4f} vs base {b_pooled:.4f}, "
              f"delta {delta_pooled:+.4f}; thresholds are {LOW_THRESHOLD}/{TARGET})")
    else:
        if a_pooled <= LOW_THRESHOLD:
            verdict = "NOT RESOLVABLE (below half the required movement)"
            detail = ("falsifies 'the current mechanisms deliver the +%.4f needed for "
                      "%.2f' on this cohort; it does NOT show the mechanisms are inert"
                      % (need_metric, TARGET))
            action = ("stop adding observation-layer mechanisms; go back to the 44.9% "
                      "state loss")
        elif a_pooled < TARGET:
            verdict = "NOT RESOLVABLE"
            detail = ("inside the band where this cohort can resolve neither success "
                      "nor failure")
            action = ("report as unresolvable; do not promote, do not re-roll for a "
                      "nicer draw")
        else:
            verdict = "MEETS TARGET, UNCONFIRMED"
            detail = "at or above the target on the pooled 100-unit mean"
            action = "run 4 trials on the same 8 users to confirm; claim nothing before that"
        print(f"VERDICT: {verdict}")
        print(f"  {detail}")
        print(f"  action: {action}")
        print(f"  cohort noise floor: +-{NOISE_FLOOR} on 8 unpaired users; "
              f"observed delta {delta_pooled:+.4f}")

    # ---- 5. per user ------------------------------------------------------
    print()
    print("=" * 78)
    per_a: dict[str, list[float]] = defaultdict(list)
    per_b: dict[str, list[float]] = defaultdict(list)
    per_b0: dict[str, list[float]] = defaultdict(list)
    for (tid, _k), v in a_off.items():
        per_a[tid].append(v)
    for (tid, _k), v in b_off.items():
        per_b[tid].append(v)
    for (tid, _k), v in b_off_t0.items():
        per_b0[tid].append(v)
    print(f"{'user':10} {'n':>3} {'arm':>7} {'base':>7} {'base t0':>8} {'d(base)':>8}")
    for tid in sorted(set(per_a) | set(per_b)):
        if tid not in per_a or tid not in per_b:
            continue
        m_a = statistics.mean(per_a[tid])
        m_b = statistics.mean(per_b[tid])
        m_0 = statistics.mean(per_b0[tid]) if per_b0.get(tid) else float('nan')
        print(f"{tid:10} {len(per_a[tid]):>3} {m_a:>7.4f} {m_b:>7.4f} {m_0:>8.4f} "
              f"{m_a - m_b:>+8.4f}")

    # ---- 6. paired contrast on official units, trial 0 --------------------
    # Both sides are restricted to their trial-0 slice so the two arms have the
    # same granularity. For the 1-trial arm that is its only trial; for the
    # 4-trial baseline it is one draw, which is the like-for-like reference.
    # Comparing the arm's *pooled* mean against the baseline's trial 0 would
    # pair a 4-trial average with a single draw and manufacture flips.
    print()
    print("=" * 78)
    a_off_t0 = official_units(a_units, trial=0)
    if args.allow_partial_cohort:
        a_off_t0 = {k: v for k, v in a_off_t0.items() if k[0] in keep}
    shared = sorted(set(a_off_t0) & set(b_off_t0))
    if len(shared) < 2:
        print("fewer than two shared official units; no paired verdict")
    else:
        wins = [k for k in shared if a_off_t0[k] > b_off_t0[k]]
        losses = [k for k in shared if a_off_t0[k] < b_off_t0[k]]
        ties = len(shared) - len(wins) - len(losses)
        z, p = sign_test(len(wins), len(losses))
        print(f"paired official units={len(shared)} (ties {ties})  "
              f"arm trials represented={sorted({t for (_1, t, _2) in a_units})}")
        print(f"  arm fixes  (base 0 -> arm 1): {len(wins)}")
        print(f"  arm breaks (base 1 -> arm 0): {len(losses)}")
        print(f"  sign test z={z:+.2f} p={p:.4f}  "
              f"{'GATE PASSED' if len(wins) > len(losses) and p < 0.05 else 'gate NOT passed'}")
        discordant = len(wins) + len(losses)
        need_net = required_net(discordant)
        if discordant:
            print(f"  gate arithmetic: {discordant} discordant pairs -> the sign test "
                  f"needs net >= {need_net} (delta >= {need_net / len(b_off):+.4f}); "
                  f"observed net {len(wins) - len(losses)}")
            print(f"    (a net of +6 -- exactly the +0.0575 the target needs -- clears "
                  f"the gate only while discordance stays <= 9)")
        by_user: dict[str, list[float]] = defaultdict(list)
        for k in shared:
            by_user[k[0]].append(a_off_t0[k] - b_off_t0[k])
        diffs = [sum(v) / len(v) for v in by_user.values()]
        better = sum(1 for d in diffs if d > 0)
        worse = sum(1 for d in diffs if d < 0)
        print(f"  user-cluster rollup: users={len(diffs)} better={better} worse={worse} "
              f"mean per-user delta {statistics.mean(diffs):+.4f}")
        print("  NOTE: any promotion also requires fixes > breaks with p < 0.05; "
              "an aggregate rise with coin-flip flips is unconfirmed.")

    # ---- 7. mechanism counters -------------------------------------------
    print()
    print("=" * 78)
    print("mechanism counters (states['adapt_agent'] = agent.loop_events)")
    totals: Counter = Counter()
    found = False
    for sim in sims:
        events = (sim.get("states") or {}).get("adapt_agent")
        if not events:
            continue
        found = True
        for key, value in events.items():
            if isinstance(value, bool):
                continue
            totals[key] += value
        print(f"  {sim.get('task_id'):10} {json.dumps(events, ensure_ascii=False)}")
    if found:
        print(f"  TOTALS {dict(totals)}")
        committed = totals.get("questions_committed", 0)
        resolved = totals.get("answers_resolved_to_a_value", 0)
        if committed:
            print(f"  questions -> values: {resolved}/{committed} "
                  f"({100 * resolved / committed:.0f}%); a question that never resolves "
                  f"to a value cannot have changed a choice")
        else:
            print("  !! no question was ever committed: the proactiveness path did not "
                  "execute, so no delta may be attributed to it")
    else:
        print("  !! no states['adapt_agent'] found: this checkpoint predates the "
              "current runner's attribution fields (legacy format)")

    print()
    print("=" * 78)
    print("confounds to state alongside any delta: this arm differs from the cached "
          "baseline in 4 ways")
    for line in (
        "(1) new agent subclass + proactive question loop",
        "(2) --memory-type adapt (structured memory, incl. the E-090 supersession fix)",
        "(3) --profile-summary (LLM recall half)",
        "(4) 1 trial vs 4 trials",
    ):
        print(f"  {line}")
    print(f"arm sha/paths: {arm_path}  baseline: {base_path}")


if __name__ == "__main__":
    main()

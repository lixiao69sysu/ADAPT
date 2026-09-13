"""Per-trial slice of the official personalization metric.

Motivation: a 1-trial arm cannot be compared against a cached 4-trial Avg@4 --
those are different quantities. The like-for-like reference for an N-trial arm
is the same N-trial slice of the cached baseline, on the same official units
(task_id, subtask_idx).

Unit semantics follow the vendored code exactly:
  - unit            = (task_id, subtask_idx)
  - unit reward     = plain mean of that unit's per-trial binary rewards
                      (vita/metrics/agent_metrics.py:113 average_at_k)
  - strict success  = subtask_rewards[idx] == 1.0

Reported, for a checkpoint:
  - `pooled`  : every observed unit reward across all trials, i.e. the
                trial-marginal success rate. For a full 4-trial checkpoint this
                equals Avg@4; for a 1-trial arm it is Avg@1 over the same 100
                units, which is the only fair cross-arm quantity when the arms
                differ in trial count.
  - `slice[t]` : the trial-t slice, pooled over users. The spread of the four
                slices is the run-to-run jitter at user-independent level.

Unpaired 8-user comparison floor is +-0.0582 (docs: noise floor). A delta below
that is not resolvable; this script does not test significance, it only prints
the comparable quantity.

Usage:
    python scripts/_trial_slice_metrics.py CP [CP ...]
"""

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def load(path):
    d = json.loads(Path(path).read_text(encoding='utf-8'))
    units = defaultdict(dict)          # (task_id, idx) -> {trial: reward}
    trials_seen = defaultdict(set)     # task_id -> {trial}
    durations = []
    for s in d['simulations']:
        info = (s.get('reward_info') or {}).get('info') or {}
        breakdown = info.get('subtask_rewards') or {}
        if not breakdown:
            continue
        tid = s['task_id']
        trial = s.get('trial', 0) or 0
        trials_seen[tid].add(trial)
        if isinstance(s.get('duration'), (int, float)):
            durations.append(s['duration'])
        for idx, r in breakdown.items():
            units[(tid, str(idx))][trial] = float(r)
    return d, units, trials_seen, durations


def report(path):
    d, units, trials_seen, durations = load(path)
    trials = sorted({t for m in units.values() for t in m})
    print(f'=== {path}')
    info = d.get('info') or {}
    print(
        f"  config: agent={info.get('agent_kind')} memory={info.get('memory_type')} "
        f"profile_summary={info.get('profile_summary')} num_trials={info.get('num_trials')}"
    )
    print(f"  users={len(trials_seen)}  official units={len(units)}  trials={trials}")
    if durations:
        tot = sum(durations) / 3600.0
        print(
            f"  sim durations: n={len(durations)} mean={statistics.mean(durations)/60:.1f} min "
            f"total={tot:.1f} h ({tot/len(durations):.2f} h per (user,trial))"
        )

    all_rewards = [r for m in units.values() for r in m.values()]
    print(f"  pooled  Avg@{len(trials)} (all observed trial rewards) = {statistics.mean(all_rewards):.4f}  n={len(all_rewards)}")

    per_user = defaultdict(list)
    for (tid, _idx), m in units.items():
        per_user[tid].extend(m.values())
    eq = statistics.mean(statistics.mean(v) for v in per_user.values())
    print(f"  equal-user-weight mean (different quantity, not the official metric) = {eq:.4f}")

    for t in trials:
        sl = [m[t] for m in units.values() if t in m]
        print(f"  slice trial={t}: units={len(sl)}  Avg@1={statistics.mean(sl):.4f}")

    if len(trials) > 1:
        # Units observed in every trial: the paired-comparison-ready subset.
        full = {k: m for k, m in units.items() if len(m) == len(trials)}
        print(f"  units observed in all {len(trials)} trials: {len(full)}/{len(units)}")
    return d, units


if __name__ == '__main__':
    for p in sys.argv[1:]:
        report(p)
        print()

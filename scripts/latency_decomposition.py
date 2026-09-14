"""Why `latency / subtask` (0.96) and `latency / (person, trial)` (0.79) disagree.

`scripts/cost_latency_report.py` reports both from the same checkpoints, but they
have different denominators:

  latency / subtask        mean of `integrity_subtask_trajectories[].duration`
  latency / (person,trial) mean of `simulations[].duration`  (whole run, wall clock)

A 4% drop inside subtasks cannot produce a 21% drop in the whole run, so most of
the gain must sit *outside* the recorded subtask durations. This script
attributes the wall clock into head / inside-subtask / inter-subtask gaps / tail,
using the per-message timestamps, and then checks the mechanism: the baseline's
memory is one text blob rewritten in full by an LLM on every update, so its
memory size grows (1,962 -> 3,163 chars) and its per-boundary cost should grow
with it, while ADAPT's bounded card should not.

Zero model calls; reads the two saved checkpoints.

Usage:
    python data/latency_decomposition.py
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path

FMT = "%Y%m%d_%H%M%S"
PATHS = [
    ("baseline (rewrite, 4 trials)", "data/simulations/stock_avg4_8u.json"),
    ("ADAPT (adapt memory, 1 trial)", "data/simulations/adapt8_1t.json"),
]


def stamp(value):
    try:
        return datetime.strptime(str(value), FMT)
    except (TypeError, ValueError):
        return None


def spans_of(record):
    spans = []
    for traj in (record.get("states") or {}).get("integrity_subtask_trajectories") or []:
        stamps = [stamp(m.get("timestamp")) for m in (traj.get("messages") or [])]
        stamps = [s for s in stamps if s]
        if stamps:
            spans.append((min(stamps), max(stamps)))
    return sorted(spans)


def main() -> int:
    print("=== A. wall clock attribution ===")
    header = f"{'composition':<30}{'baseline':>12}{'ADAPT':>12}{'delta':>10}"
    print(header)
    print("-" * len(header))
    totals = {}
    for label, path in PATHS:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        head, inner, gap, tail, total, gap_n = [], [], [], [], [], []
        for record in data["simulations"]:
            start, end = stamp(record.get("start_time")), stamp(record.get("end_time"))
            spans = spans_of(record)
            if not (start and end and spans):
                continue
            total.append((end - start).total_seconds())
            head.append((spans[0][0] - start).total_seconds())
            tail.append((end - spans[-1][1]).total_seconds())
            inner.append(sum((b - a).total_seconds() for a, b in spans))
            gaps = [(spans[i + 1][0] - spans[i][1]).total_seconds() for i in range(len(spans) - 1)]
            gap.append(sum(gaps))
            gap_n.append(len(gaps))
        totals[label] = {
            "total": statistics.mean(total) / 60,
            "head": statistics.mean(head) / 60,
            "inner": statistics.mean(inner) / 60,
            "gap": statistics.mean(gap) / 60,
            "tail": statistics.mean(tail) / 60,
            "per_gap": statistics.mean(gap) / statistics.mean(gap_n),
            "n": len(total),
        }

    base, arm = totals[PATHS[0][0]], totals[PATHS[1][0]]
    for key, name in (
        ("total", "total wall clock"),
        ("head", "head (start -> 1st subtask)"),
        ("inner", "inside subtasks"),
        ("gap", "INTER-SUBTASK GAPS"),
        ("tail", "tail (last subtask -> end)"),
    ):
        print(
            f"{name:<30}{base[key]:>10.2f} m{arm[key]:>10.2f} m"
            f"{arm[key] - base[key]:>+9.2f} m"
        )
    print()
    print(f"per-gap cost: baseline {base['per_gap']:.0f} s -> ADAPT {arm['per_gap']:.0f} s "
          f"({(arm['per_gap'] / base['per_gap'] - 1) * 100:+.1f}%)")
    gain = base["total"] - arm["total"]
    print(f"share of the {gain:.2f} min total gain that sits in the gaps: "
          f"{100 * (base['gap'] - arm['gap']) / gain:.0f}%")

    print()
    print("=== B. does the gap track memory size? (rewrite-cost hypothesis) ===")
    header = f"{'gap after subtask':<20}{'baseline gap':>14}{'base mem':>10}{'ADAPT gap':>12}{'ADAPT mem':>11}"
    print(header)
    print("-" * len(header))
    trend = {}
    for label, path in PATHS:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        by_index, mem_by_index = defaultdict(list), defaultdict(list)
        for record in data["simulations"]:
            spans = spans_of(record)
            for i in range(len(spans) - 1):
                by_index[i].append((spans[i + 1][0] - spans[i][1]).total_seconds())
            for key, value in ((record.get("states") or {}).get("memory_snapshots") or {}).items():
                if isinstance(value, str) and key.startswith("subtask_"):
                    try:
                        mem_by_index[int(key.split("_")[1])].append(len(value))
                    except (ValueError, IndexError):
                        continue
        trend[label] = (by_index, mem_by_index)
    base_by, base_mem = trend[PATHS[0][0]]
    arm_by, arm_mem = trend[PATHS[1][0]]
    for i in sorted(set(base_by) | set(arm_by)):
        b = statistics.mean(base_by[i]) if base_by.get(i) else float("nan")
        a = statistics.mean(arm_by[i]) if arm_by.get(i) else float("nan")
        bm = statistics.mean(base_mem[i]) if base_mem.get(i) else float("nan")
        am = statistics.mean(arm_mem[i]) if arm_mem.get(i) else float("nan")
        print(f"{i:<20}{b:>13.1f}s{bm:>10.0f}{a:>11.1f}s{am:>11.0f}")
    print()
    for label in (PATHS[0][0], PATHS[1][0]):
        by, mem = trend[label]
        idx = sorted(by)
        if len(idx) < 2:
            continue
        first, last = statistics.mean(by[idx[0]]), statistics.mean(by[idx[-1]])
        m_first, m_last = statistics.mean(mem[idx[0]]), statistics.mean(mem[idx[-1]])
        print(f"{label:<32} gap {first:6.1f} -> {last:6.1f} s ({(last / first - 1) * 100:+4.0f}%)   "
              f"memory {m_first:.0f} -> {m_last:.0f} chars ({(m_last / m_first - 1) * 100:+4.0f}%)")
    print()
    print("=== C. is the gap trend real, or an endpoint artifact? ===")
    print("Comparing the FIRST index against the LAST index is one bucket against one")
    print("bucket. The robust version compares equal-width windows, and correlates memory")
    print("size against gap across all indices.")
    for label in (PATHS[0][0], PATHS[1][0]):
        by, mem = trend[label]
        idx = sorted(by)
        if len(idx) < 6:
            continue
        gaps = [statistics.mean(by[i]) for i in idx]
        mems = [statistics.mean(mem[i]) for i in idx]
        half = max(2, len(idx) // 3)
        early_gap = statistics.mean(gaps[:half])
        late_gap = statistics.mean(gaps[-half:])
        early_mem = statistics.mean(mems[:half])
        late_mem = statistics.mean(mems[-half:])
        print(f"  {label}")
        print(f"    endpoint  index {idx[0]} -> {idx[-1]}:  gap {gaps[0]:.1f} -> {gaps[-1]:.1f} s "
              f"({(gaps[-1] / gaps[0] - 1) * 100:+.0f}%)   memory {mems[0]:.0f} -> {mems[-1]:.0f} "
              f"({(mems[-1] / mems[0] - 1) * 100:+.0f}%)")
        print(f"    window    first {half} vs last {half}: gap {early_gap:.1f} -> {late_gap:.1f} s "
              f"({(late_gap / early_gap - 1) * 100:+.1f}%)   memory {early_mem:.0f} -> {late_mem:.0f} "
              f"({(late_mem / early_mem - 1) * 100:+.0f}%)")
        n = len(gaps)
        mg, mm = statistics.mean(gaps), statistics.mean(mems)
        cov = sum((gaps[i] - mg) * (mems[i] - mm) for i in range(n)) / n
        sg = statistics.pstdev(gaps)
        sm = statistics.pstdev(mems)
        r = cov / (sg * sm) if sg and sm else float("nan")
        print(f"    Pearson r(memory size, gap) over {n} indices = {r:+.2f}")
    print()
    print("Reading: the endpoint comparison is fragile -- the baseline gap is noisy across")
    print("indices (251, 331, 402, 253, ...) because each bucket averages only 32 runs and")
    print("the gap mixes memory update with evaluation and environment reset. The window")
    print("comparison and the correlation are the statistics to quote. The MEAN per-gap")
    print("difference in section A is the robust headline; the within-baseline growth is not")
    print("established by this data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

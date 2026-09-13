"""Decompose a checkpoint's content-free rubric detail into a diagnosis.

Reads ``states["rubric_detail"]`` (see ``agent/rubric_detail.py``) and reports how
far failing units actually were from passing, and which requirement dimensions
they missed. The fail/pass split is the point: reporting failure rates alone is
how this project produced three falsified bottlenecks in a row (E-053 and the
write-failure audit), so every dimension is shown with its passing-write control.

Zero-model: reads a saved artifact only.

Usage:
    python scripts/rubric_breakdown.py <checkpoint.json> [--per-subtask]
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def load_records(path: pathlib.Path) -> tuple[list[dict], dict[str, dict]]:
    """Return (simulation summaries, per-subtask records keyed by namespace)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    summaries: list[dict] = []
    records: dict[str, dict] = {}
    for item in data.get("simulations") or []:
        detail = (item.get("states") or {}).get("rubric_detail")
        if not detail:
            continue
        summaries.append({"task_id": item.get("task_id"), "trial": item.get("trial"), **{k: v for k, v in detail.items() if k != "per_subtask"}})
        for subtask_id, record in (detail.get("per_subtask") or {}).items():
            records[f"{item.get('task_id')}::{item.get('trial')}::{subtask_id}"] = record
    return summaries, records


def _rate(records: list[dict]) -> tuple[int, float | None, float | None]:
    graded = [r for r in records if isinstance(r.get("n_conditions"), int) and r.get("n_conditions")]
    if not graded:
        return 0, None, None
    fracs = [r["fraction_met"] for r in graded]
    pooled = sum(r["n_met"] for r in graded) / sum(r["n_conditions"] for r in graded)
    return len(graded), sum(fracs) / len(fracs), pooled


def _fmt(value: float | None) -> str:
    return f"{value:.3f}" if isinstance(value, float) else "n/a"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("checkpoint")
    parser.add_argument("--per-subtask", action="store_true", help="list every failing unit")
    args = parser.parse_args()

    path = pathlib.Path(args.checkpoint)
    summaries, records = load_records(path)
    available = {k: r for k, r in records.items() if r.get("status") == "available"}
    failing = {k: r for k, r in available.items() if r.get("reward") == 0.0}
    passing = {k: r for k, r in available.items() if r.get("reward") == 1.0}
    fail_dims: collections.Counter = collections.Counter()
    for record in failing.values():
        for label in record.get("missed_dimensions") or []:
            fail_dims[label] += 1

    print(f"checkpoint          : {path.name}")
    print(f"scored simulations  : {len(summaries)}")
    print(f"subtasks            : {len(records)}  (available {len(available)}, "
          f"unavailable {len(records) - len(available)})")
    print()

    n_all, mean_all, pooled_all = _rate(list(available.values()))
    print(f"graded subtasks     : {n_all}")
    print(f"mean fraction_met   : {mean_all:.4f}" if mean_all is not None else "mean fraction_met   : n/a")
    print(f"per-condition rate  : {pooled_all:.4f}" if pooled_all is not None else "per-condition rate  : n/a")
    print()

    print("-- how far from passing (reward 0 only) --")
    buckets = collections.Counter()
    for record in failing.values():
        n, met = record.get("n_conditions"), record.get("n_met")
        if isinstance(n, int) and isinstance(met, int):
            buckets[n - met] += 1
    for missing in sorted(buckets):
        print(f"  missed {missing:2} of the conditions : {buckets[missing]:4}")
    one_away = [k for k, r in failing.items()
                if isinstance(r.get("n_conditions"), int) and isinstance(r.get("n_met"), int)
                and r["n_conditions"] - r["n_met"] == 1]
    print(f"  => convertible pool (exactly one missed): {len(one_away)}")
    print()

    # A fail/pass comparison is degenerate here: reward=1 means *every*
    # condition was met, so passing units have no missed dimension by
    # construction and every dimension would look "discriminative". The honest
    # control is the opportunity count -- how many units even had a condition in
    # that dimension -- so a rare dimension is not compared against a common one.
    present: collections.Counter = collections.Counter()
    for record in available.values():
        for label in (record.get("dimensions") or {}):
            present[label] += 1

    print("-- missed dimension, normalised by opportunity --")
    labels = sorted(set(present) | set(fail_dims), key=lambda x: -(fail_dims.get(x, 0) / max(1, present.get(x, 0))))
    print(f"  {'dimension':16} {'present':>8} {'missed':>7} {'miss_rate':>10}")
    for label in labels:
        p = present.get(label, 0)
        m = fail_dims.get(label, 0)
        rate = m / p if p else float("inf")
        print(f"  {label:16} {p:8} {m:7} {rate:10.3f}")
    print("  (present = graded units carrying a condition of this dimension;")
    print("   pass-side counts are omitted because they are zero by construction)")

    n_fail, mean_fail, pooled_fail = _rate(list(failing.values()))
    n_pass, mean_pass, pooled_pass = _rate(list(passing.values()))
    print("-- distance from perfect, by outcome --")
    print(f"  failing (n={n_fail:4})  mean fraction_met {_fmt(mean_fail)}  per-condition {_fmt(pooled_fail)}")
    print(f"  passing (n={n_pass:4})  mean fraction_met {_fmt(mean_pass)}  per-condition {_fmt(pooled_pass)}")

    if args.per_subtask:
        print("\n-- failing units --")
        for key in sorted(failing):
            r = failing[key]
            print(f"  {key:34} {r.get('n_met')}/{r.get('n_conditions')} "
                  f"missed={','.join(r.get('missed_dimensions') or []) or '-'}")


if __name__ == "__main__":
    main()

"""Per-subtask reward matrix across result files, for unit-level comparison.

Usage: python scripts/_unit_rewards.py stock_dev.json iso_R5a_stock_adaptmemory_summary.json ...
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def unit_rewards(simulation: dict) -> list[float]:
    info = (simulation.get("reward_info") or {}).get("info") or {}
    subtasks = info.get("subtask_rewards") or {}
    ordered = sorted(
        subtasks.items(),
        key=lambda item: int(re.findall(r"\d+", item[0])[-1] or 0),
    )
    return [float(value or 0.0) for _, value in ordered]


def collect(path: Path, task: str) -> dict[int, list[float]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    simulations = payload.get("simulations") if isinstance(payload, dict) else payload
    rows: dict[int, list[float]] = {}
    for simulation in simulations or []:
        if str(simulation.get("task_id")) != task:
            continue
        for index, value in enumerate(unit_rewards(simulation)):
            rows.setdefault(index, []).append(value)
    return rows


def main() -> None:
    task = sys.argv[1]
    tables = {}
    for name in sys.argv[2:]:
        path = ROOT / "data" / "simulations" / name
        if not path.exists():
            print(f"{name}: MISSING")
            continue
        tables[name] = collect(path, task)
    labels = list(tables)
    width = max((len(name.replace(".json", "")) for name in labels), default=10)
    print(f"task={task}  " + "  ".join(f"{n.replace('.json',''):>{width}}" for n in labels))
    units = sorted({index for rows in tables.values() for index in rows})
    for unit in units:
        cells = []
        for name in labels:
            values = tables[name].get(unit)
            cells.append(
                "-" if values is None else "/".join(f"{value:g}" for value in values)
            )
        print(f"  unit {unit + 1:>2}: " + "  ".join(f"{cell:>{width}}" for cell in cells))
    for name in labels:
        rows = tables[name]
        flat = [value for values in rows.values() for value in values]
        print(f"  {name}: mean={sum(flat)/len(flat):.4f} over {len(flat)} unit-trials")


if __name__ == "__main__":
    main()

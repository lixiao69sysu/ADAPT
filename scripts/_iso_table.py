"""Print per-user official subtask-level Avg@1 for ADAPT result files."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def rewards(simulation: dict) -> list[float]:
    info = (simulation.get("reward_info") or {}).get("info") or {}
    subtasks = info.get("subtask_rewards") or {}
    if subtasks:
        ordered = sorted(
            subtasks.items(),
            key=lambda item: int(re.findall(r"\d+", item[0])[-1] or 0),
        )
        return [float(value or 0.0) for _, value in ordered]
    results = simulation.get("results") or []
    return [float(item.get("reward") or 0.0) for item in results]


def report(path: Path) -> None:
    if not path.exists():
        print(f"{path.name}: MISSING")
        return
    payload = json.loads(path.read_text(encoding="utf-8"))
    simulations = payload.get("simulations") if isinstance(payload, dict) else payload
    per_user: dict[str, list[float]] = {}
    for simulation in simulations or []:
        per_user.setdefault(str(simulation.get("task_id", "?")), []).extend(
            rewards(simulation)
        )
    parts = []
    overall: list[float] = []
    for user, values in sorted(per_user.items()):
        parts.append(
            f"{user}={sum(values) / len(values):.4f}"
            f"({sum(1 for value in values if value == 1.0)}/{len(values)})"
        )
        overall.extend(values)
    mean = sum(overall) / len(overall) if overall else 0.0
    print(f"{path.name}: total={mean:.4f}  " + "  ".join(parts))


for name in sys.argv[1:]:
    report(ROOT / "data" / "simulations" / name)

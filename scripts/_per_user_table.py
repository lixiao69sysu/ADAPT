"""Per-user mean reward for a checkpoint (offline, no API calls)."""
import json, sys
from pathlib import Path


def per_user(path: Path) -> dict[str, float]:
    d = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, list[float]] = {}
    for s in d["simulations"]:
        reward = ((s.get("reward_info") or {}).get("reward"))
        if reward is None:
            continue
        out.setdefault(s["task_id"], []).append(float(reward))
    return {user: sum(values) / len(values) for user, values in sorted(out.items())}


files = sys.argv[1:]
tables = {Path(f).name: per_user(Path(f)) for f in files}
users = sorted({u for t in tables.values() for u in t})
print(f"{'user':12}" + "".join(f"{name[:26]:>28}" for name in tables))
for user in users:
    row = f"{user:12}"
    for table in tables.values():
        value = table.get(user)
        row += f"{(f'{value:.4f}' if value is not None else '-'):>28}"
    print(row)
print(f"{'MEAN':12}" + "".join(
    f"{(sum(t.values())/len(t) if t else 0):>28.4f}" for t in tables.values()
))

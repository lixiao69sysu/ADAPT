import json
from pathlib import Path


def rewards(path):
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    out = {}
    for sim in d["simulations"]:
        subs = ((sim.get("reward_info") or {}).get("info") or {}).get("subtask_rewards") or {}
        out[sim["task_id"]] = [float(v or 0) for _, v in sorted(subs.items(), key=lambda kv: int(kv[0].split("_")[1]))]
    return out


tables = {
    "guard1": rewards("data/simulations/ab_guard_E057330.json"),
    "guard3": rewards("data/simulations/ab_guard3_E057330.json"),
    "guard4": rewards("data/simulations/ab_guard4.json"),
    "guard5": rewards("data/simulations/ab_guard5.json"),
    "stock": rewards("data/simulations/stock_avg4_8u.json"),
}
users = sorted(tables["guard5"])
for user in users:
    print(f"\n=== {user} ===")
    for label in ("guard1", "guard3", "guard4", "guard5", "stock"):
        values = tables[label].get(user)
        if not values:
            continue
        beats = sum(1 for v in values if v >= 0.5)
        print(f"  {label:7} n={len(values):2d} mean={sum(values)/len(values):.4f} successes={beats}")
total = {label: [sum(t[u]) for u in users if u in t for _ in [0]] for label, t in tables.items()}
print("\n--- per-user totals (sum of subtask rewards) ---")
for label in ("guard1", "guard3", "guard4", "guard5", "stock"):
    t = tables[label]
    present = [u for u in users if u in t]
    if not present:
        continue
    got = sum(sum(t[u]) for u in present)
    n = sum(len(t[u]) for u in present)
    print(f"  {label:7} users={present} total={got:.2f} units={n} mean={got/n:.4f}")

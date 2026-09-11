import json
from pathlib import Path

def rewards(path):
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    out = {}
    for sim in d["simulations"]:
        subs = ((sim.get("reward_info") or {}).get("info") or {}).get("subtask_rewards") or {}
        out[sim["task_id"]] = [float(v or 0) for _, v in sorted(subs.items(), key=lambda kv: int(kv[0].split("_")[1]))]
    return out

adapt = rewards("data/simulations/ab_guard3_E057330.json")
stock = rewards("data/simulations/stock_avg4_8u.json")
for user, values in adapt.items():
    print(f"ADAPT(guard3) {user}: {values}  mean={sum(values)/len(values):.4f}  successes={sum(1 for v in values if v>=0.5)}/{len(values)}")
    base = stock.get(user) or []
    if base:
        print(f"stock        {user}: mean={sum(base)/len(base):.4f} (4-trial mean)")
prev = rewards("data/simulations/ab_guard_E057330.json")
for user, values in prev.items():
    print(f"ADAPT(guard1) {user}: mean={sum(values)/len(values):.4f}  successes={sum(1 for v in values if v>=0.5)}/{len(values)}")

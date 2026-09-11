import json
from pathlib import Path


def rewards(path):
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    out = {}
    for sim in d["simulations"]:
        subs = ((sim.get("reward_info") or {}).get("info") or {}).get("subtask_rewards") or {}
        values = [float(v or 0) for _, v in sorted(subs.items(), key=lambda kv: int(kv[0].split("_")[1]))]
        out[sim["task_id"]] = values
    return out


cur = rewards("data/simulations/ab_guard4.json")
prev = rewards("data/simulations/ab_guard3_E057330.json")
prev2 = rewards("data/simulations/adapt_avg1_8u.json")
stock = rewards("data/simulations/stock_avg4_8u.json")

for user in sorted(cur):
    values = cur[user]
    print(f"\n=== {user} ===")
    print("  ADAPT (guard4) :", values, f"mean={sum(values)/len(values):.4f} successes={sum(1 for v in values if v>=0.5)}/{len(values)}")
    for label, table in (("guard3", prev), ("guard1", prev2)):
        if user in table and table[user]:
            v = table[user]
            print(f"  ADAPT ({label:6}) : mean={sum(v)/len(v):.4f} successes={sum(1 for x in v if x>=0.5)}/{len(v)}")
    if user in stock:
        v = stock[user]
        print(f"  stock (4 trials): mean={sum(v)/len(v):.4f}")

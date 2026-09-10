"""Per-subtask comparison: ADAPT (1 trial) vs stock (mean of 4 trials)."""
import json
from pathlib import Path


def per_subtask(path, users=None):
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    out = {}
    for sim in d["simulations"]:
        user = sim["task_id"]
        if users and user not in users:
            continue
        info = (sim.get("reward_info") or {}).get("info") or {}
        for key, value in (info.get("subtask_rewards") or {}).items():
            if value is None:
                continue
            index = int(key.split("_")[1])
            out.setdefault((user, index), []).append(float(value))
    return {k: sum(v) / len(v) for k, v in out.items() if v}


adapt = per_subtask("data/simulations/adapt_avg1_8u.json")
stock = per_subtask("data/simulations/stock_avg4_8u.json")
print(f"{'unit':26}{'ADAPT':>8}{'stock':>8}   verdict")
lost = gained = neutral = 0
for (user, index) in sorted(adapt, key=lambda k: (k[0], k[1])):
    a = adapt[(user, index)]
    s = stock.get((user, index))
    if s is None:
        verdict = "no-stock"
    elif a == 0 and s >= 0.5:
        verdict = "LOST"
        lost += 1
    elif a >= 1.0 and s <= 0.5:
        verdict = "GAINED"
        gained += 1
    else:
        verdict = ""
        neutral += 1
    s_text = "-" if s is None else f"{s:>8.3f}"
    print(f"{user + '_' + str(index):26}{a:>8.3f}{s_text}   {verdict}")
pairs = [(u, i) for (u, i) in adapt if (u, i) in stock]
adapt_mean = sum(adapt[k] for k in pairs) / len(pairs)
stock_mean = sum(stock[k] for k in pairs) / len(pairs)
print(f"\nlost={lost} gained={gained} neutral={neutral}")
print(f"ADAPT mean on {len(pairs)} units = {adapt_mean:.4f} | stock mean on SAME units = {stock_mean:.4f}")

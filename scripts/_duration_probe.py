import json
from pathlib import Path
d = json.loads(Path("data/simulations/stock_avg4_8u.json").read_text(encoding="utf-8"))
sims = d["simulations"]
durs = [s.get("duration") or 0 for s in sims]
print("simulations:", len(sims), "users:", len({s["task_id"] for s in sims}))
print("total seconds: %.0f  (%.1f h)" % (sum(durs), sum(durs)/3600))
print("mean per simulation: %.0f s" % (sum(durs)/max(1,len(durs))))
subs = 0
for s in sims:
    trajs = s["states"].get("integrity_subtask_trajectories") or []
    subs += len(trajs) if trajs else len(s["states"].get("new_states") or [])
print("subtask trajectories total:", subs)
print("mean per subtask: %.0f s" % (sum(durs)/max(1,subs)))

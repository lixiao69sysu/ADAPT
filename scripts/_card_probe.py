import json
from pathlib import Path
d = json.loads(Path("data/simulations/adapt_smoke_fix8.json").read_text(encoding="utf-8"))
sim = d["simulations"][0]
print("sim keys:", sorted(sim.keys()))
print("states keys:", sorted(sim["states"].keys()) if isinstance(sim["states"], dict) else type(sim["states"]))
trajs = sim["states"].get("integrity_subtask_trajectories")
print("trajs:", type(trajs), len(trajs) if trajs else None)
t = trajs[0]
print("traj keys:", sorted(t.keys()))
print("roles:", sorted({m.get("role") for m in t["messages"]}))
print("first msg:", json.dumps(t["messages"][0], ensure_ascii=False)[:400])

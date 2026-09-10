import json
from pathlib import Path
d = json.loads(Path("data/simulations/adapt_smoke_fix11.json").read_text(encoding="utf-8"))
sim = d["simulations"][0]
msgs = sim["states"]["integrity_subtask_trajectories"][0]["messages"]
for m in msgs:
    if m.get("role") == "tool" and "Train Info" in (m.get("content") or ""):
        print("TOOL:", (m.get("name") or ""))
        print(m["content"][:1500])
        print("=====")

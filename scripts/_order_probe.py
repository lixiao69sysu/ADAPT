import json
from pathlib import Path
d = json.loads(Path("data/simulations/adapt_smoke_fix11.json").read_text(encoding="utf-8"))
sim = d["simulations"][0]
print("reward:", sim.get("reward_info", {}).get("reward"))
for t in sim["states"]["integrity_subtask_trajectories"]:
    for state in t["states"]["new_states"]:
        for order in state.get("orders", []) if isinstance(state, dict) else []:
            print(json.dumps(order, ensure_ascii=False))
    print("raw new_states:", json.dumps(t["states"]["new_states"], ensure_ascii=False)[:900])

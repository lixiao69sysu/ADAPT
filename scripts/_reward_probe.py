import json
from pathlib import Path
d = json.loads(Path("data/simulations/adapt_smoke_fix10.json").read_text(encoding="utf-8"))
s = d["simulations"][1]
for t in s["states"]["integrity_subtask_trajectories"]:
    st = t.get("states") or {}
    for key in ("new_states", "old_states"):
        value = st.get(key)
        text = json.dumps(value, ensure_ascii=False)
        print(t.get("subtask_id"), key, type(value).__name__, len(text))
        print(text[:600])
        print("...")

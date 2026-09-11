import json
from pathlib import Path

def trials(path, idx):
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    out = []
    for sim in d["simulations"]:
        subs = ((sim.get("reward_info") or {}).get("info") or {}).get("subtask_rewards") or {}
        reward = float(subs.get(f"subtask_{idx}_reward") or 0)
        for t in ((sim.get("states") or {}).get("integrity_subtask_trajectories") or []):
            if t.get("subtask_idx") != idx:
                continue
            seq = []
            for m in t.get("messages") or []:
                if m.get("role") == "assistant":
                    for c in (m.get("tool_calls") or []):
                        seq.append(f"{c.get('name')}({json.dumps(c.get('arguments'), ensure_ascii=False)[:80]})")
                    if not (m.get("tool_calls") or []):
                        seq.append("SAY: " + (m.get("content") or "").replace("\n", " ")[:100])
            out.append((reward, seq, t["states"]["new_states"]))
    return out


print("=========== stock, unit idx 6 (fruit platter), 4 trials ===========")
for reward, seq, state in trials("data/simulations/stock_avg4_8u.json", 6):
    print(f"\n-- reward={reward} --")
    for step in seq[:10]:
        print("   ", step)
    print("    order:", json.dumps(state, ensure_ascii=False)[:220])

import json
from pathlib import Path

d = json.loads(Path("data/simulations/ab_guard_E057330.json").read_text(encoding="utf-8"))
sim = d["simulations"][0]
rewards = ((sim.get("reward_info") or {}).get("info") or {}).get("subtask_rewards") or {}
for t in (sim["states"]["integrity_subtask_trajectories"]):
    idx = t.get("subtask_idx")
    if idx not in {2, 6, 9}:
        continue
    reward = rewards.get(f"subtask_{idx}_reward")
    print(f"\n######## sub{idx} reward={reward} term={t.get('termination_reason')} msgs={len(t['messages'])} ########")
    instr = [m.get("content") for m in t["messages"] if m.get("role") == "user"]
    print("  first user turn:", (instr[0] or "")[:100] if instr else "-")
    for m in t["messages"]:
        if m.get("role") == "assistant":
            for c in (m.get("tool_calls") or []):
                print("   CALL", c.get("name"), json.dumps(c.get("arguments"), ensure_ascii=False)[:110])
            if not (m.get("tool_calls") or []):
                print("   SAY ", (m.get("content") or "").replace("\n", " ")[:120])
    print("  final state:", json.dumps(t["states"]["new_states"], ensure_ascii=False)[:200])

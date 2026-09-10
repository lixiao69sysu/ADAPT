"""Side-by-side action sequences for LOST units: ADAPT (1 trial) vs stock (best trial)."""
import json
from pathlib import Path

TARGETS = [("E941775", 13), ("E941775", 7), ("E057330", 9), ("E057330", 2)]


def seq(path, user, idx, want_success):
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    for sim in d["simulations"]:
        if sim["task_id"] != user:
            continue
        rewards = ((sim.get("reward_info") or {}).get("info") or {}).get("subtask_rewards") or {}
        reward = float(rewards.get(f"subtask_{idx}_reward") or 0)
        if want_success and reward < 0.5:
            continue
        if not want_success and False:
            continue
        for t in ((sim.get("states") or {}).get("integrity_subtask_trajectories") or []):
            if t.get("subtask_idx") != idx:
                continue
            steps = []
            for m in t.get("messages") or []:
                if m.get("role") == "assistant":
                    for call in (m.get("tool_calls") or []):
                        steps.append(f"{call.get('name')}({json.dumps(call.get('arguments'), ensure_ascii=False)[:90]})")
                    if not (m.get("tool_calls") or []):
                        text = (m.get("content") or "").replace("\n", " ")[:110]
                        steps.append(f'SAY: {text}')
            return reward, steps, t["states"]["new_states"]
    return None


for user, idx in TARGETS:
    print(f"\n######## {user} sub{idx} ########")
    a = seq("data/simulations/adapt_avg1_8u.json", user, idx, want_success=False)
    if a:
        print(f"-- ADAPT (reward={a[0]:.2f}) --")
        for step in a[1][:22]:
            print("   ", step)
        print("    final:", json.dumps(a[2], ensure_ascii=False)[:300])
    s = seq("data/simulations/stock_avg4_8u.json", user, idx, want_success=True)
    if s:
        print(f"-- stock successful trial (reward={s[0]:.2f}) --")
        for step in s[1][:22]:
            print("   ", step)
        print("    final:", json.dumps(s[2], ensure_ascii=False)[:300])

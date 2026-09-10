"""Where do the differences come from? writes/searches vs reward, both runs."""
import json, statistics
from pathlib import Path

USERS = {"E057330", "E941775"}


def features(path):
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = []
    for sim in d["simulations"]:
        if sim["task_id"] not in USERS:
            continue
        rewards = ((sim.get("reward_info") or {}).get("info") or {}).get("subtask_rewards") or {}
        for t in ((sim.get("states") or {}).get("integrity_subtask_trajectories") or []):
            idx = t.get("subtask_idx")
            msgs = t.get("messages") or []
            assistants = [m for m in msgs if m.get("role") == "assistant"]
            tools = [c.get("name", "") for m in assistants for c in (m.get("tool_calls") or [])]
            rows.append({
                "reward": float(rewards.get(f"subtask_{idx}_reward") or 0),
                "msgs": len(msgs),
                "write": sum(1 for n in tools if n.startswith("create_") or n in {"instore_book", "instore_reservation"}),
                "search": sum(1 for n in tools if "search" in n or "recommend" in n),
                "read": sum(1 for n in tools if n.startswith("get_")),
                "pay": sum(1 for n in tools if n.startswith("pay_")),
                "all_tools": tools,
                "states": t["states"]["new_states"],
            })
    return rows


for label, path in (("ADAPT", "data/simulations/adapt_avg1_8u.json"), ("stock", "data/simulations/stock_avg4_8u.json")):
    rows = features(path)
    solved = [r for r in rows if r["reward"] >= 0.5]
    unsolved = [r for r in rows if r["reward"] < 0.5]
    print(f"\n=== {label}: {len(rows)} units, solved(>=0.5)={len(solved)} ===")
    for name, group in (("solved", solved), ("unsolved", unsolved)):
        if not group:
            continue
        print(
            f"  {name:9} writes med={statistics.median(r['write'] for r in group):.1f} "
            f"max={max(r['write'] for r in group):2d} | zero-write {sum(1 for r in group if r['write']==0)}/{len(group)}"
            f" | searches med={statistics.median(r['search'] for r in group):.1f}"
            f" | reads med={statistics.median(r['read'] for r in group):.1f}"
            f" | msgs med={statistics.median(r['msgs'] for r in group):.0f}"
        )
    print("  write-count histogram:", {k: sum(1 for r in rows if r["write"] == k) for k in range(0, 6)})

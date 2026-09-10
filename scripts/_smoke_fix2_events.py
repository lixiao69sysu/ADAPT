import json, collections
from pathlib import Path
p = Path("data/simulations/smoke_fix2.jsonl")
rows = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
by_sub = collections.defaultdict(list)
for r in rows:
    by_sub[r.get("subtask_id") or r.get("task") or "?"].append(r)
for sub, rs in by_sub.items():
    kinds = collections.Counter(r.get("event") for r in rs)
    print(sub, dict(kinds))
    for r in rs:
        if r.get("event") in {"preflight_rejected", "user_observation", "search_budget_blocked", "recommendation_finalized"}:
            extra = {k: v for k, v in r.items() if k not in {"event", "subtask_id", "task_id", "ts", "timestamp"}}
            print("   ", r.get("event"), json.dumps(extra, ensure_ascii=False)[:220])

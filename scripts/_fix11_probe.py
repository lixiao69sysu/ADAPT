import json, collections
from pathlib import Path
rows = [json.loads(l) for l in Path("data/simulations/smoke_fix11.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
print(dict(collections.Counter(r.get("event") for r in rows)))
for r in rows:
    if r.get("event") in {"candidate_memory_retrieval","decision_card_refreshed","preflight_rejected","tool_result","runtime_policy_applied"}:
        extra = {k:v for k,v in r.items() if k not in {"event","subtask_id","task_id","ts","timestamp","seq","user_id"}}
        print(f"{r.get('event'):28} {json.dumps(extra, ensure_ascii=False)[:220]}")

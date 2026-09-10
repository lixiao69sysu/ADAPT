import json, collections
from pathlib import Path
p = Path("data/simulations/smoke_fix4.jsonl")
rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
kinds = collections.Counter(r.get("event") for r in rows)
print(dict(kinds))
for r in rows:
    if r.get("event") in {"entity_gap_search","preflight_rejected","recommendation_finalized","question_committed","user_observation"}:
        extra = {k: v for k, v in r.items() if k not in {"event","subtask_id","task_id","ts","timestamp","seq","user_id"}}
        print(f"{r.get('event'):24} {json.dumps(extra, ensure_ascii=False)[:200]}")

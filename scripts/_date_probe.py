import json
from pathlib import Path
rows = [json.loads(l) for l in Path("data/simulations/smoke_fix10.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
for r in rows:
    if r.get("event") in {"instruction_date_resolved", "date_grounding_call"}:
        extra = {k: v for k, v in r.items() if k not in {"event", "subtask_id", "task_id", "ts", "timestamp", "seq", "user_id"}}
        print(f"{r.get('event'):26} {json.dumps(extra, ensure_ascii=False)}")

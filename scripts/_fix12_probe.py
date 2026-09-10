import json
from pathlib import Path
d = json.loads(Path("data/simulations/adapt_smoke_fix12.json").read_text(encoding="utf-8"))
sim = d["simulations"][0]
print("reward:", sim["reward_info"]["reward"])
print("subtask rewards:", sim["reward_info"]["info"]["subtask_rewards"])
for t in sim["states"]["integrity_subtask_trajectories"]:
    print(json.dumps(t["states"]["new_states"], ensure_ascii=False)[:700])
rows = [json.loads(l) for l in Path("data/simulations/smoke_fix12.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
for r in rows:
    if r.get("event") in {"runtime_policy_applied","hierarchical_enrichment","candidate_memory_retrieval","instruction_date_resolved"}:
        extra = {k:v for k,v in r.items() if k not in {"event","subtask_id","task_id","ts","timestamp","seq","user_id"}}
        print(f"{r.get('event'):28} {json.dumps(extra, ensure_ascii=False)[:200]}")

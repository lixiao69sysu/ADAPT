import json
from pathlib import Path
rows = [json.loads(l) for l in Path("data/simulations/smoke_fix7.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
for r in rows:
    if r.get("event") == "recommendation_ranking":
        print("home_tokens:", r.get("home_tokens"), "best_evidence:", r.get("best_evidence"))
        for c in r.get("considered", []):
            print("  ", c)

import json
from pathlib import Path
path = Path("data/simulations/smoke_fix9.jsonl")
rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
for r in rows:
    if r.get("event") == "recommendation_ranking":
        print("home_tokens:", r.get("home_tokens"), "| best_decisive:", r.get("best_decisive"))
        for c in r.get("considered", []):
            print("   ", c)

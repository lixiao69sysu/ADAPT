import json
from pathlib import Path
d = json.loads(Path("data/simulations/adapt_smoke_fix8.json").read_text(encoding="utf-8"))
snaps = d["simulations"][0]["states"]["memory_snapshots"]
for key, value in snaps.items():
    text = json.dumps(value, ensure_ascii=False)
    print("KEY", key, "len", len(text))
    print(text[:1200])
    print("---")

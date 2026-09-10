"""Do the two runs differ in memory-tool use and search diversity?"""
import json
from collections import Counter
from pathlib import Path

USERS = {"E057330", "E941775"}


def tool_counter(path):
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    counter = Counter()
    per_unit_searches = []
    for sim in d["simulations"]:
        if sim["task_id"] not in USERS:
            continue
        for t in ((sim.get("states") or {}).get("integrity_subtask_trajectories") or []):
            names = [c.get("name", "") for m in (t.get("messages") or []) if m.get("role") == "assistant" for c in (m.get("tool_calls") or [])]
            counter.update(names)
            per_unit_searches.append(sum(1 for n in names if "search" in n or "recommend" in n))
    return counter, per_unit_searches


for label, path in (("ADAPT", "data/simulations/adapt_avg1_8u.json"), ("stock", "data/simulations/stock_avg4_8u.json")):
    counter, searches = tool_counter(path)
    print(f"\n=== {label} ===")
    print("  total tool calls:", sum(counter.values()))
    print("  memory tool (query_preference_memory):", counter.get("query_preference_memory", 0))
    print("  top tools:", counter.most_common(12))
    print("  searches per unit: mean=%.2f max=%d hist=%s" % (
        sum(searches) / max(1, len(searches)), max(searches or [0]),
        dict(sorted(Counter(searches).items())),
    ))

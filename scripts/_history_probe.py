import json, re
from collections import Counter
p = r"evaluation/vitabench/data/vita/domains/personalization/tasks.json"
with open(p, encoding="utf-8") as fh:
    tasks = json.load(fh)
task = next(t for t in tasks if t.get("id") == "P722245")
sub = next(s for s in task["subtasks"] if s["subtask_id"] == "sub_P722245_1")
blobs = []
for key in ("historical_behavior", "historical_chat", "interactions", "message_history"):
    value = sub.get(key)
    if value:
        blobs.append((key, json.dumps(value, ensure_ascii=False)))
print("fields:", [(k, len(v)) for k, v in blobs])
text = "\n".join(v for _, v in blobs)
for marker in ("动车", "高铁", "二等座", "一等座", "城际", "D1", "G8", "列车", "车次"):
    print(marker, text.count(marker))
sample = [m for m in re.findall(r"[^\n]{0,60}(?:动车|高铁|二等座|车次)[^\n]{0,60}", text)][:8]
for line in sample:
    print("  >", line[:160])

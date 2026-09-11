import json
p = r"evaluation/vitabench/data/vita/domains/personalization/tasks.json"
with open(p, encoding="utf-8") as fh:
    tasks = json.load(fh)
task = next(t for t in tasks if t.get("id") == "P722245")
sub = next(s for s in task["subtasks"] if s["subtask_id"] == "sub_P722245_1")
print("interaction types:", {type(i).__name__ for i in sub["interactions"]})
for i, item in enumerate(sub["interactions"]):
    text = json.dumps(item, ensure_ascii=False)
    if "动车" in text or "二等座" in text or "train" in text or "票务" in text:
        print("---", i, json.dumps(item, ensure_ascii=False)[:700])

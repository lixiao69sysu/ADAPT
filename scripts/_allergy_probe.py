import json
from pathlib import Path

p = r"evaluation/vitabench/data/vita/domains/personalization/tasks.json"
tasks = json.load(open(p, encoding="utf-8"))
task = next(t for t in tasks if t.get("id") == "E057330")
hits = 0
for sub in task["subtasks"]:
    for i, item in enumerate(sub.get("interactions") or []):
        text = json.dumps(item, ensure_ascii=False)
        if "哈密瓜" in text or "过敏" in text:
            hits += 1
            if hits <= 3:
                print("=== subtask", sub["subtask_id"], "interaction", i, "===")
                print(json.dumps(item, ensure_ascii=False)[:700])
                print()
print("total interactions mentioning 哈密瓜/过敏:", hits)

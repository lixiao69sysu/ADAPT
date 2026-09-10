import json
p = r"evaluation/vitabench/data/vita/domains/personalization/tasks.json"
with open(p, encoding="utf-8") as fh:
    tasks = json.load(fh)
want = {"sub_E941775_7", "sub_P722245_1", "sub_P722245_5"}
out = []
for t in tasks:
    for s in t.get("subtasks", []):
        if s.get("subtask_id") in want:
            out.append({
                "subtask_id": s.get("subtask_id"),
                "domain": s.get("domain"),
                "instruction": s.get("instruction"),
                "user_intention": s.get("user_intention"),
                "skill_tested": s.get("skill_tested"),
                "task": s.get("task"),
                "evaluation_criteria": s.get("evaluation_criteria"),
                "rubric_type": type(s.get("rubric")).__name__,
                "target_ids_type": type(s.get("target_product_ids")).__name__,
            })
with open("data/simulations/_offline_subtask_expect.json", "w", encoding="utf-8") as fh:
    json.dump(out, fh, ensure_ascii=False, indent=2)
print("written", len(out))

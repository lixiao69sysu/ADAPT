import json
p = r"evaluation/vitabench/data/vita/domains/personalization/tasks.json"
with open(p, encoding="utf-8") as fh:
    tasks = json.load(fh)
for t in tasks:
    if t.get("id") in {"E941775"}:
        prof = t.get("user_profile") or {}
        print({k: prof.get(k) for k in ("常住地", "常住住址", "职业", "性别", "出生日期")})

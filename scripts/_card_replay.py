"""Offline: does the compiled card carry a semantically relevant preference?

No model calls: ADAPTMemory.update is rule-based when summary rewriting is off.
"""
import json
from pathlib import Path

import agent.vitabench_bootstrap as bootstrap
bootstrap.enable_vitabench_utf8()

from agent.memory.adapt_memory import ADAPTMemory

tasks = json.load(open("evaluation/vitabench/data/vita/domains/personalization/tasks.json", encoding="utf-8"))
task = next(t for t in tasks if t["id"] == "E057330")
memory = ADAPTMemory(language="chinese", user_id="E057330")
for subtask in task["subtasks"][:7]:
    memory.update(subtask.get("interactions") or [])
    card = memory.compile_task(subtask["instruction"])
    print(f"\n===== {subtask['subtask_id']}: {subtask['instruction'][:40]}")
    print(card.render())
    print("  avoid =", card.avoid)
    print("  pool  =", card.preference_pool[:6])

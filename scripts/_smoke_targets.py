import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import agent.vitabench_bootstrap as bootstrap
bootstrap.enable_vitabench_utf8()
from vita.domains.personalization.environment import get_tasks

TARGET_USERS = ['E941775', 'P722245']
tasks = {t.id: t for t in get_tasks('chinese')}
for uid in TARGET_USERS:
    task = tasks[uid]
    print(f'=== {uid} ({len(task.subtasks)} subtasks) ===')
    for i, st in enumerate(task.subtasks):
        if st.domain in ('instore', 'ota'):
            print(f'  [{i}] {st.subtask_id}  {st.domain}  {st.instruction[:60]}')

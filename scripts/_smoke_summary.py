"""Per-subtask behaviour of the smoke run, from saved trajectories."""
import json
import re
from pathlib import Path

CP = Path('data/simulations/adapt_smoke_search_budget.json')
d = json.loads(CP.read_text(encoding='utf-8'))

WRITE = re.compile(r'^(create_|pay_|book|reserve|instore_book|instore_reservation)')
SEARCH = re.compile(r'(search|recommand|recommend|train_ticket|flight_search)')

for sim in d['simulations']:
    uid = sim['task_id']
    trajs = (sim.get('states') or {}).get('integrity_subtask_trajectories') or []
    print(f"=== {uid} (status={sim.get('evaluation_status')}, {len(trajs)} subtasks) ===")
    for traj in trajs:
        sid = traj.get('subtask_id')
        msgs = traj.get('messages') or []
        searches, writes, reads, errs = [], [], 0, 0
        for m in msgs:
            if m.get('role') == 'assistant':
                for call in m.get('tool_calls') or []:
                    name = call.get('name') or ''
                    if WRITE.match(name):
                        writes.append(name)
                    elif SEARCH.search(name):
                        searches.append(name)
                    else:
                        reads += 1
            elif m.get('role') == 'tool' and m.get('error'):
                errs += 1
        print(
            f"  {sid}: msgs={len(msgs)} searches={len(searches)}{searches} "
            f"writes={writes} other_tools={reads} tool_errors={errs} "
            f"term={traj.get('termination_reason')}"
        )

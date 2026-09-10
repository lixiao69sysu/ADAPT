import json
from copy import deepcopy
from pathlib import Path

SRC = Path('data/simulations/stock_dev_64k_baseline.json')
DST = Path('data/simulations/stock_avg4_8u.json')
COHORT8 = ['J365414', 'M793481', 'P722245', 'E057330', 'E941775', 'Q089190', 'U000828', 'O309411']

src = json.loads(SRC.read_text(encoding='utf-8'))
info = deepcopy(src['info'])
info['num_trials'] = 4
info['cohort'] = 'dev'
tasks = sorted(COHORT8)

reused = []
for uid in ('E057330', 'Q089190'):
    sim = next(s for s in src['simulations'] if s['task_id'] == uid and s.get('trial') == 0)
    assert sim.get('seed') == 42 and sim.get('evaluation_status') == 'ok', (uid, sim.get('seed'), sim.get('evaluation_status'))
    reused.append(deepcopy(sim))

checkpoint = {'timestamp': src['timestamp'], 'info': info, 'tasks': tasks, 'simulations': reused}
DST.write_text(json.dumps(checkpoint, ensure_ascii=False), encoding='utf-8')
print('seeded:', DST.name, '| tasks:', tasks)
print('reused trial0 sims:', [(s['task_id'], s.get('trial'), s.get('seed'), s['reward_info']['reward']) for s in reused])

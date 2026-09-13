import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent.vitabench_bootstrap as bootstrap
bootstrap.enable_vitabench_utf8()

from vita.metrics.agent_metrics import (
    _compute_subtask_pass_metrics,
    is_successful,
    pass_at_k,
)

CP = Path('data/simulations/stock_avg4_8u.json')
d = json.loads(CP.read_text(encoding='utf-8'))

sims = []
for s in d['simulations']:
    info = (s.get('reward_info') or {}).get('info') or {}
    sims.append(
        SimpleNamespace(
            task_id=s['task_id'],
            trial=s.get('trial', 0),
            reward=s.get('reward_info', {}).get('reward') if s.get('reward_info') else None,
            reward_info=SimpleNamespace(info=info),
        )
    )
tasks = [SimpleNamespace(id=t, domain='personalization') for t in d['tasks']]
results = SimpleNamespace(
    info=SimpleNamespace(num_trials=d['info']['num_trials']),
    tasks=tasks,
    simulations=sims,
)

print('=== official task-level (is_successful: reward == 1.0) ===')
success = [1 if is_successful(s.reward) else 0 for s in sims if isinstance(s.reward, (int, float))]
print('successful trials:', sum(success), '/', len(success))
by_task = {}
for s in sims:
    by_task.setdefault(s.task_id, []).append(s.reward)
task_pass4 = []
for tid, rewards in by_task.items():
    n = len(rewards)
    c = sum(1 for r in rewards if r == 1.0)
    task_pass4.append(pass_at_k(n, c, 4))
print('task-level Pass@4 (mean over users):', round(sum(task_pass4) / len(task_pass4), 4) if task_pass4 else None)

print()
print('=== official subtask-level (personalization; success: subtask reward == 1.0) ===')
sub = _compute_subtask_pass_metrics(results)
if sub is None:
    print('returned None (num_trials<=1 or no subtask_rewards)')
else:
    print('evaluation units (task_id, subtask_idx):', sub['num_units'])
    for k in sorted(sub['pass_at_n']):
        print(
            f"  k={k}:  Avg@{k}={sub['average_at_n'][k]:.4f}   "
            f"Pass@{k}={sub['pass_at_n'][k]:.4f}   Pass^{k}={sub['pass_hat_ks'][k]:.4f}"
        )

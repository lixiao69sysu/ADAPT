import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent.vitabench_bootstrap as bootstrap
bootstrap.enable_vitabench_utf8()

from vita.metrics.agent_metrics import _compute_subtask_pass_metrics

CP = Path('data/simulations/stock_avg4_8u.json')
d = json.loads(CP.read_text(encoding='utf-8'))
by_user = {}
for s in d['simulations']:
    by_user.setdefault(s['task_id'], {})[s.get('trial', 0)] = round(
        s['reward_info']['reward'], 4
    )

print('| 用户 | t0 | t1 | t2 | t3 | 用户 Avg@4 | 子任务数 |')
print('|---|---|---|---|---|---|---|')
sums = []
for uid in sorted(by_user):
    trials = by_user[uid]
    vals = [trials.get(t) for t in range(4)]
    mean = sum(v for v in vals if v is not None) / 4
    sums.append(mean)
    n_sub = None
    for s in d['simulations']:
        if s['task_id'] == uid and s.get('trial') == 0:
            n_sub = len((s['reward_info']['info'] or {}).get('subtask_rewards') or {})
    print(f"| {uid} | " + ' | '.join(f'{v:.4f}' for v in vals) + f" | **{mean:.4f}** | {n_sub} |")

overall = sum(sums) / len(sums)
print(f"\n用户级 Avg@4（8 人均值）= **{overall:.4f}**")

sims = []
for s in d['simulations']:
    info = (s.get('reward_info') or {}).get('info') or {}
    sims.append(SimpleNamespace(task_id=s['task_id'], trial=s.get('trial', 0),
                                reward_info=SimpleNamespace(info=info)))
results = SimpleNamespace(
    info=SimpleNamespace(num_trials=d['info']['num_trials']),
    tasks=[SimpleNamespace(id=t, domain='personalization') for t in d['tasks']],
    simulations=sims,
)
sub = _compute_subtask_pass_metrics(results)
print(f"\n官方 subtask 级（评估单元 {sub['num_units']} 个 = 用户×子任务）：")
for k in sorted(sub['pass_at_n']):
    print(f"  k={k}: Avg@{k}={sub['average_at_n'][k]:.4f}  Pass@{k}={sub['pass_at_n'][k]:.4f}  Pass^{k}={sub['pass_hat_ks'][k]:.4f}")

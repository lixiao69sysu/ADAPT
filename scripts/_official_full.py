"""Cross-check: run the FULL official compute_metrics() on a native Results."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent.vitabench_bootstrap as bootstrap
bootstrap.enable_vitabench_utf8()

from vita.data_model.simulation import (
    AgentInfo,
    Info,
    Results,
    SimulationRun,
    UserInfo,
)
from vita.environment.environment import EnvironmentInfo
from vita.data_model.tasks import Task
from vita.metrics.agent_metrics import compute_metrics

CP = Path('data/simulations/stock_avg4_8u.json')
d = json.loads(CP.read_text(encoding='utf-8'))

tasks = [Task.model_construct(id=t, domain='personalization') for t in d['tasks']]
sims = [SimulationRun.model_validate(s) for s in d['simulations']]
info = Info(
    git_commit='unknown',
    num_trials=int(d['info']['num_trials']),
    max_steps=int(d['info'].get('max_steps') or 100),
    max_errors=10,
    user_info=UserInfo(implementation='personalization_user', llm=d['info'].get('llm_user')),
    agent_info=AgentInfo(implementation=str(d['info'].get('agent_kind')), llm=d['info'].get('llm_agent')),
    environment_info=EnvironmentInfo(implementation='vitabench', domain_name='personalization'),
    seed=d['info'].get('seed'),
)
results = Results(info=info, tasks=tasks, simulations=sims)

metrics = compute_metrics(results)
print('OFFICIAL compute_metrics():')
print('  avg_reward          :', round(metrics.avg_reward, 4))
print('  pass_hat_ks (task)  :', {k: round(v, 4) for k, v in (metrics.pass_hat_ks or {}).items()})
print('  pass_at_n (task)    :', {k: round(v, 4) for k, v in (metrics.pass_at_n or {}).items()})
print('  average_at_n (task) :', {k: round(v, 4) for k, v in (metrics.average_at_n or {}).items()})
print('  subtask_pass_at_n   :', {k: round(v, 4) for k, v in (metrics.subtask_pass_at_n or {}).items()})
print('  subtask_pass_hat_ks :', {k: round(v, 4) for k, v in (metrics.subtask_pass_hat_ks or {}).items()})
print('  subtask_average_at_n:', {k: round(v, 4) for k, v in (metrics.subtask_average_at_n or {}).items()})
print('  subtask_num_units   :', metrics.subtask_num_units)
print('  skill_split_metrics :', json.dumps(metrics.skill_split_metrics, ensure_ascii=False)[:300] if metrics.skill_split_metrics else None)

import json
import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent.vitabench_bootstrap as bootstrap
bootstrap.enable_vitabench_utf8()

from agent.evaluation_integrity import IntegrityPersonalizationOrchestrator
from vita.config import models
from vita.data_model.simulation import SimulationRun
from vita.domains.personalization.environment import get_tasks
from vita.agent.personalization_agent import PersonalizationAgent
from vita.memory.rewrite_memory import RewriteMemory
from vita.prompts import get_prompts
from vita.user.personalization_user import PersonalizationUser

CHECKPOINT = Path('data/simulations/stock_dev_resume_remaining.json')
USER = 'U010122'
SUBTASK = 'sub_U010122_2'

checkpoint = json.loads(CHECKPOINT.read_text(encoding='utf-8'))
item = next(s for s in checkpoint['simulations'] if s['task_id'] == USER)
trajs = item['states']['integrity_subtask_trajectories']
traj = next(t for t in trajs if t['subtask_id'] == SUBTASK)
task = next(t for t in get_tasks('chinese') if t.id == USER)
sub = next(st for st in task.subtasks if str(st.subtask_id) == SUBTASK)
task = task.model_copy(update={'subtasks': [sub]}, deep=True)

time = task.subtasks[0].environment.get('time')
memory = RewriteMemory(language='chinese', user_id=task.id)
agent = PersonalizationAgent(tools=[], domain_policy=get_prompts('chinese').personalization_agent_system_prompt,
                             memory=memory, user_profile=task.user_profile, llm=None, llm_args={},
                             time=time, language='chinese')
user = PersonalizationUser(subtasks=task.subtasks, persona=str(task.user_profile), instructions=None,
                           llm=None, llm_args={}, language='chinese')
orch = IntegrityPersonalizationOrchestrator(
    task=task, agent=agent, user=user, llm_evaluator='qwen36-evaluator',
    llm_args_evaluator=deepcopy(models['qwen36-evaluator']), evaluation_type='trajectory',
    language='chinese', evaluator_retries=0, evaluator_retry_backoff_seconds=0.0)

sim = SimulationRun.model_validate(item)
sim = orch.reevaluate_saved(sim, [traj])
ri = sim.reward_info
print('reward:', ri.reward if ri else None)
print('info:', json.dumps(ri.info, ensure_ascii=False) if ri else None)
records = (sim.states or {}).get('evaluation_integrity', {}).get('subtasks', [])
print('records:', json.dumps(records, ensure_ascii=False)[:200])

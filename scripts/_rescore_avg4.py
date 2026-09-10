import json
import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent.vitabench_bootstrap as bootstrap
bootstrap.enable_vitabench_utf8()

from agent.evaluation_integrity import (
    IntegrityPersonalizationOrchestrator,
    patch_evaluator_extracter,
    restore_evaluator_extracter,
)
from agent.reevaluate_guarded import make_guarded_generate
from vita.config import models
from vita.data_model.simulation import SimulationRun
from vita.domains.personalization.environment import get_tasks
from vita.agent.personalization_agent import PersonalizationAgent
from vita.memory.rewrite_memory import RewriteMemory
from vita.prompts import get_prompts
from vita.user.personalization_user import PersonalizationUser
from vita.utils import llm_utils
import vita.evaluator.evaluator_traj as evaluator_traj
from vita.utils.utils import get_now

CP = Path('data/simulations/stock_avg4_8u.json')
backup = CP.with_name(CP.name + '.pre_rescore.bak')
if not backup.exists():
    backup.write_bytes(CP.read_bytes())
    print('backup saved:', backup.name, flush=True)

checkpoint = json.loads(CP.read_text(encoding='utf-8'))
tasks = {t.id: t for t in get_tasks('chinese')}
fails = [s for s in checkpoint['simulations'] if s.get('evaluation_status') == 'evaluation_failed']
print('re-scoring', len(fails), 'sims', flush=True)

for sim in fails:
    uid = sim['task_id']
    trial = sim.get('trial')
    task = tasks[uid]
    trajs = (sim.get('states') or {}).get('integrity_subtask_trajectories')
    if not trajs:
        print(f'  {uid} trial {trial}: NO TRAJECTORIES, skip', flush=True)
        continue
    saved_ids = {str(t['subtask_id']) for t in trajs}
    filtered = task.model_copy(
        update={'subtasks': [st for st in task.subtasks if str(st.subtask_id) in saved_ids]},
        deep=True,
    )
    print(f'=== {uid} trial {trial} start ({len(filtered.subtasks)} subtasks) ===', flush=True)
    time = filtered.subtasks[0].environment.get('time') if filtered.subtasks else None
    memory = RewriteMemory(language='chinese', user_id=task.id)
    agent = PersonalizationAgent(
        tools=[],
        domain_policy=get_prompts('chinese').personalization_agent_system_prompt,
        memory=memory, user_profile=task.user_profile, llm=None, llm_args={},
        time=time, language='chinese',
    )
    user = PersonalizationUser(
        subtasks=filtered.subtasks, persona=str(task.user_profile), instructions=None,
        llm=None, llm_args={}, language='chinese',
    )
    orch = IntegrityPersonalizationOrchestrator(
        task=filtered, agent=agent, user=user, llm_evaluator='qwen36-evaluator',
        llm_args_evaluator=deepcopy(models['qwen36-evaluator']), evaluation_type='trajectory',
        language='chinese', evaluator_retries=3, evaluator_retry_backoff_seconds=3.0,
    )
    sim_obj = SimulationRun.model_validate(sim)
    orig = llm_utils.generate
    guarded = make_guarded_generate(orig)
    llm_utils.generate = guarded
    evaluator_traj.generate = guarded
    orig_ext = patch_evaluator_extracter()
    try:
        sim_obj = orch.reevaluate_saved(sim_obj, trajs)
    finally:
        llm_utils.generate = orig
        evaluator_traj.generate = orig
        restore_evaluator_extracter(orig_ext)
    updated = sim_obj.model_dump(mode='json')
    integ = (updated.get('states') or {}).get('evaluation_integrity', {})
    sim['reward_info'] = updated.get('reward_info')
    sim['evaluation_status'] = integ.get('evaluation_status', 'ok')
    sim['evaluation_attempts'] = integ.get('evaluation_attempts', 0)
    sim['evaluator_error'] = integ.get('evaluator_error')
    sim['states'] = updated.get('states')
    rew = (sim.get('reward_info') or {}).get('reward')
    print(f'    reward: {round(rew, 4) if rew is not None else None}', flush=True)

checkpoint['timestamp'] = get_now()
checkpoint['reevaluation_guard'] = {
    'complete_criteria_required': True,
    'normalize_extracter': True,
    'source': str(CP),
}
CP.write_text(json.dumps(checkpoint, ensure_ascii=False), encoding='utf-8')
print('ALL RESCORE DONE', flush=True)

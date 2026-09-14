"""T2: offline failure clustering over observable trajectory evidence.

Uses only observable events stored in the checkpoint: tool-call names, tool
results (payment status), tool errors, assistant questions, termination reason.
Never reads rubrics, rewards-as-learning-signal, or target annotations.
Compares never-solved units (0/4) against always-solved units (4/4) as a control.
"""
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import agent.vitabench_bootstrap as bootstrap
bootstrap.enable_vitabench_utf8()
from vita.domains.personalization.environment import get_tasks

CP = Path('data/simulations/stock_avg4_8u.json')
d = json.loads(CP.read_text(encoding='utf-8'))

# subtask index -> domain
domain_by_unit = {}
for task in get_tasks('chinese'):
    if task.id not in d['tasks']:
        continue
    for i, st in enumerate(task.subtasks):
        domain_by_unit[(task.id, i)] = st.domain

WRITE_RE = re.compile(r'^(create_|pay_|cancel_|modify_|book|reserve|instore_book|instore_reservation|instore_cancel|instore_modify)')
SEARCH_RE = re.compile(r'(search|recommand|recommend|get_nearby|distance_to_time|_info$)')

def features(messages):
    n_assistant = n_user = n_tool = 0
    writes, searches, pay_ok = [], [], False
    tool_errors = 0
    asked = 0
    unpaid = False
    for m in messages:
        role = m.get('role')
        if role == 'assistant':
            n_assistant += 1
            calls = m.get('tool_calls') or []
            for c in calls:
                name = c.get('name') or ''
                if WRITE_RE.match(name):
                    writes.append(name)
                if SEARCH_RE.search(name):
                    searches.append(name)
            if not calls:
                text = (m.get('content') or '').strip()
                if text and (text.endswith('？') or text.endswith('?')) and '###STOP###' not in text:
                    asked += 1
        elif role == 'user':
            n_user += 1
        elif role == 'tool':
            n_tool += 1
            content = m.get('content') or ''
            if m.get('error'):
                tool_errors += 1
            if 'status=unpaid' in content or 'status:unpaid' in content:
                unpaid = True
            if 'Payment successful' in content or '支付成功' in content:
                unpaid = False
                pay_ok = True
    return {
        'n_msgs': len(messages), 'n_assistant': n_assistant, 'n_user': n_user,
        'n_writes': len(writes), 'n_searches': len(searches),
        'pay_ok': pay_ok, 'unpaid_end': unpaid, 'tool_errors': tool_errors,
        'asked': asked,
    }

unit = defaultdict(dict)      # (uid, idx) -> trial -> reward
unit_feat = defaultdict(dict) # (uid, idx) -> trial -> feature dict
for sim in d['simulations']:
    uid, trial = sim['task_id'], sim.get('trial', 0)
    trajs = (sim.get('states') or {}).get('integrity_subtask_trajectories') or []
    sr = ((sim.get('reward_info') or {}).get('info') or {}).get('subtask_rewards') or {}
    for i, traj in enumerate(trajs):
        rew = sr.get(f'subtask_{i}_reward')
        if rew is None:
            continue
        unit[(uid, i)][trial] = float(rew)
        unit_feat[(uid, i)][trial] = features(traj.get('messages') or [])

never, always, flaky = [], [], []
for key, rewards in unit.items():
    vals = [rewards.get(t) for t in range(4)]
    if any(v is None for v in vals):
        continue
    c = sum(1 for v in vals if v == 1.0)
    if c == 0:
        never.append(key)
    elif c == 4:
        always.append(key)
    else:
        flaky.append(key)

print(f'units: never={len(never)} always={len(always)} flaky={len(flaky)}')

def agg(keys):
    acc = defaultdict(list)
    for key in keys:
        for t in range(4):
            f = unit_feat[key].get(t)
            if f:
                for k, v in f.items():
                    acc[k].append(v)
    out = {}
    for k, vs in acc.items():
        if isinstance(vs[0], bool):
            out[k] = round(sum(1 for v in vs if v) / len(vs), 3)
        else:
            out[k] = round(sum(vs) / len(vs), 2)
    return out

print('\n=== observable features: never-solved vs always-solved (mean per trial) ===')
fn, fa = agg(never), agg(always)
keys = ['n_msgs', 'n_user', 'n_searches', 'n_writes', 'asked', 'tool_errors',
        'pay_ok', 'unpaid_end']
print(f"{'feature':<14} {'never(0/4)':>11} {'always(4/4)':>12}")
for k in keys:
    print(f'{k:<14} {str(fn.get(k)):>11} {str(fa.get(k)):>12}')

print('\n=== never-solved units by domain ===')
print(dict(Counter(domain_by_unit.get(k, '?') for k in never)))
print('=== always-solved units by domain ===')
print(dict(Counter(domain_by_unit.get(k, '?') for k in always)))
print('=== all units by domain ===')
print(dict(Counter(domain_by_unit.get(k, '?') for k in unit)))

print('\n=== never-solved units: observable failure signature (per unit, majority over 4 trials) ===')
sig = Counter()
for key in never:
    feats = [unit_feat[key].get(t) for t in range(4) if unit_feat[key].get(t)]
    if not feats:
        continue
    no_write = sum(1 for f in feats if f['n_writes'] == 0)
    unpaid = sum(1 for f in feats if f['unpaid_end'])
    paid = sum(1 for f in feats if f['pay_ok'])
    asked = sum(1 for f in feats if f['asked'] >= 2)
    errs = sum(1 for f in feats if f['tool_errors'] > 0)
    label = []
    if no_write >= 3:
        label.append('NO_WRITE')
    elif unpaid >= 3:
        label.append('UNPAID')
    if asked >= 3:
        label.append('OVER_ASK')
    if errs >= 3:
        label.append('TOOL_ERROR')
    sig['+'.join(label) if label else 'WRITE_DONE_BUT_WRONG'] += 1
for k, v in sig.most_common():
    print(f'  {k}: {v}')

print('\n=== never-solved: domain x signature ===')
cross = defaultdict(Counter)
for key in never:
    feats = [unit_feat[key].get(t) for t in range(4) if unit_feat[key].get(t)]
    if not feats:
        continue
    no_write = sum(1 for f in feats if f['n_writes'] == 0)
    unpaid = sum(1 for f in feats if f['unpaid_end'])
    asked = sum(1 for f in feats if f['asked'] >= 2)
    errs = sum(1 for f in feats if f['tool_errors'] > 0)
    label = []
    if no_write >= 3:
        label.append('NO_WRITE')
    elif unpaid >= 3:
        label.append('UNPAID')
    if asked >= 3:
        label.append('OVER_ASK')
    if errs >= 3:
        label.append('TOOL_ERROR')
    cross[domain_by_unit.get(key, '?')]['+'.join(label) if label else 'WRITE_DONE_BUT_WRONG'] += 1
for dom in sorted(cross):
    print(f'  {dom}: {dict(cross[dom])}')

print('\n=== mean searches / writes per trial by domain (all units) ===')
dom_feat = defaultdict(lambda: defaultdict(list))
for key in unit:
    for t in range(4):
        f = unit_feat[key].get(t)
        if f:
            dom_feat[domain_by_unit.get(key, '?')]['searches'].append(f['n_searches'])
            dom_feat[domain_by_unit.get(key, '?')]['writes'].append(f['n_writes'])
for dom in sorted(dom_feat):
    s = dom_feat[dom]['searches']
    w = dom_feat[dom]['writes']
    print(f'  {dom}: searches={sum(s)/len(s):.2f} writes={sum(w)/len(w):.2f} units={sum(1 for k in unit if domain_by_unit.get(k)=="dom")}')

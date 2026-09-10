"""Diagnose failure structure of the stock 8x4 baseline."""
import json
from collections import Counter, defaultdict
from pathlib import Path

d = json.loads(Path('data/simulations/stock_avg4_8u.json').read_text(encoding='utf-8'))

# per (user, subtask_idx): rewards across trials + skill tag
unit = defaultdict(dict)
skills = {}
for s in d['simulations']:
    uid, trial = s['task_id'], s.get('trial', 0)
    info = (s.get('reward_info') or {}).get('info') or {}
    sr = info.get('subtask_rewards') or {}
    st = info.get('subtask_skill_tested') or {}
    for k, v in sr.items():
        idx = int(k.split('_')[1])
        unit[(uid, idx)][trial] = float(v)
    for k, v in st.items():
        idx = int(k.split('_')[1])
        skills[(uid, idx)] = v

def is_proactive(value):
    if isinstance(value, str):
        return 'proactive' in value
    if isinstance(value, (list, tuple, set)):
        return any('proactive' in str(x) for x in value)
    return False


print('=== per-subtask success pattern across 4 trials ===')
buckets = Counter()
for key, rewards in sorted(unit.items()):
    vals = [rewards.get(t) for t in range(4)]
    if any(v is None for v in vals):
        continue
    buckets[sum(1 for v in vals if v == 1.0)] += 1
print('  units by #trials-solved (0/1/2/3/4):', dict(sorted(buckets.items())))
print('  total units:', sum(buckets.values()))
print('  proactive units:', sum(1 for k in unit if is_proactive(skills.get(k))))
print()

print('=== per-user subtask solve-rate across 4 trials ===')
per_user = defaultdict(list)
for (uid, idx), rewards in unit.items():
    vals = [rewards.get(t) for t in range(4)]
    if any(v is None for v in vals):
        continue
    per_user[uid].append(sum(1 for v in vals if v == 1.0) / 4)
for uid in sorted(per_user):
    rates = per_user[uid]
    always0 = sum(1 for r in rates if r == 0)
    always1 = sum(1 for r in rates if r == 1)
    flaky = len(rates) - always0 - always1
    print(f'  {uid}: units={len(rates)} 4/4={always1} 0/4={always0} 1-3/4={flaky}')
print()

print('=== consistently-failed units (0 of 4 trials) ===')
zero = [(uid, idx) for (uid, idx), r in unit.items()
        if all(r.get(t) == 0.0 for t in range(4))]
print('  count:', len(zero))
tagc = Counter()
for (uid, idx) in zero:
    tagc['proactive' if is_proactive(skills.get((uid, idx))) else 'personalize'] += 1
print('  tags:', dict(tagc))
print('  list:', sorted(zero)[:40])
print()

print('=== unpaid orders per (user, trial) ===')
unpaid = []
for s in d['simulations']:
    pending = False
    for m in s.get('messages') or []:
        if m.get('role') != 'tool':
            continue
        content = m.get('content') or ''
        if 'status=unpaid' in content or 'status:unpaid' in content:
            pending = True
        if 'Payment successful' in content or '支付成功' in content:
            pending = False
    if pending:
        unpaid.append((s['task_id'], s.get('trial')))
print('  trials ending with unpaid order:', len(unpaid), unpaid)

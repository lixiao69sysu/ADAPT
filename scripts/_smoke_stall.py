"""Inspect the stalled subtask (101 messages, max_steps, no write)."""
import json
from collections import Counter
from pathlib import Path

CP = Path('data/simulations/adapt_smoke_search_budget.json')
d = json.loads(CP.read_text(encoding='utf-8'))
sim = next(s for s in d['simulations'] if s['task_id'] == 'P722245')
traj = next(
    t for t in sim['states']['integrity_subtask_trajectories']
    if t['subtask_id'] == 'sub_P722245_5'
)
msgs = traj['messages']
print('total messages:', len(msgs), '| termination:', traj.get('termination_reason'))
roles = Counter(m.get('role') for m in msgs)
print('roles:', dict(roles))

print('\n--- first 24 messages (role: content head / tool calls) ---')
for i, m in enumerate(msgs[:24]):
    role = m.get('role')
    content = (m.get('content') or '').replace('\n', ' ')[:150]
    calls = [c.get('name') for c in (m.get('tool_calls') or [])]
    print(f'[{i}] {role}: {content}' + (f'  CALLS={calls}' if calls else ''))

print('\n--- last 16 messages ---')
for i, m in enumerate(msgs[-16:], start=len(msgs) - 16):
    role = m.get('role')
    content = (m.get('content') or '').replace('\n', ' ')[:150]
    calls = [c.get('name') for c in (m.get('tool_calls') or [])]
    print(f'[{i}] {role}: {content}' + (f'  CALLS={calls}' if calls else ''))

print('\n--- user simulator responses (what the user was told) ---')
user_msgs = [(i, (m.get('content') or '')[:120]) for i, m in enumerate(msgs) if m.get('role') == 'user']
for i, text in user_msgs[:12]:
    print(f'[{i}] {text}')

print('\n--- assistant text-only messages (no tool calls) ---')
text_only = [
    (i, (m.get('content') or '').replace('\n', ' ')[:160])
    for i, m in enumerate(msgs)
    if m.get('role') == 'assistant' and not (m.get('tool_calls') or [])
]
print('count:', len(text_only))
for i, text in text_only[:12]:
    print(f'[{i}] {text}')

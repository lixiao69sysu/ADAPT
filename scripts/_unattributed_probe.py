"""Dump the failing runs that no mechanical mechanism test yet explains.

`mechanism_attribution.py` assigns a primary mechanism label with a fixed test
order; whatever falls through the last test is reported as `unattributed` (15 of
283 on the stock baseline). This probe prints those runs in enough detail to
decide whether they are (a) a new general-logic mechanism worth naming, or
(b) genuinely unclassifiable from observables.

Reads target annotations for offline attribution only; the agent never sees them.

Usage:
    python scripts/_unattributed_probe.py data/simulations/stock_avg4_8u.json
"""

from __future__ import annotations

import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent.vitabench_bootstrap as bootstrap  # noqa: E402

bootstrap.enable_vitabench_utf8()

from agent.decision import TaskSpec  # noqa: E402
from scripts.candidate_selection_audit import observed_ids  # noqa: E402
from scripts.target_reachability import (  # noqa: E402
    bound_ids,
    is_write_tool,
    rank_of,
    result_records,
    target_ids,
)

_ID_LIKE = __import__("re").compile(r"^[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+$")


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "data/simulations/stock_avg4_8u.json"
    with open(path, encoding="utf-8") as handle:
        checkpoint = json.load(handle)
    from agent.vitabench_runner import get_tasks

    tasks_by_id = {task.id: task for task in get_tasks("chinese")}
    shown = 0
    buckets: collections.Counter = collections.Counter()

    for sim in checkpoint.get("simulations", []):
        task = tasks_by_id.get(str(sim.get("task_id")))
        if task is None:
            continue
        subtasks = list(task.subtasks)
        rewards = ((sim.get("reward_info") or {}).get("info") or {}).get(
            "subtask_rewards"
        ) or {}
        for traj in (sim.get("states") or {}).get(
            "integrity_subtask_trajectories"
        ) or []:
            index = traj.get("subtask_idx")
            if index is None or index >= len(subtasks):
                continue
            reward = rewards.get(f"subtask_{index}_reward")
            if reward is None or float(reward) == 1.0:
                continue
            messages = traj.get("messages") or []
            instruction = subtasks[index].instruction or ""
            entities, products, hosts = target_ids(subtasks[index].environment or {})

            writes, signatures = [], []
            for message in messages:
                if message.get("role") != "assistant":
                    continue
                for call in message.get("tool_calls") or []:
                    if not isinstance(call, dict):
                        continue
                    name = str(call.get("name") or "")
                    signatures.append(
                        f"{name}:{json.dumps(call.get('arguments') or {}, sort_keys=True, ensure_ascii=False)}"
                    )
                    if is_write_tool(name):
                        writes.append(name)

            observed = observed_ids(messages)
            bound = bound_ids(messages)
            hallucinated = [
                v for v in bound if _ID_LIKE.match(v) and v not in observed
            ]
            target_printed = False
            for message in messages:
                if message.get("role") != "tool":
                    continue
                records = result_records(message.get("content"))
                if any(
                    rank_of(t, records) is not None for t in (products | entities)
                ):
                    target_printed = True
            spec = TaskSpec.compile(instruction)
            last = ""
            for message in reversed(messages):
                if message.get("role") == "assistant" and isinstance(
                    message.get("content"), str
                ):
                    if message["content"].strip():
                        last = message["content"].strip()
                        break
            asked = any(m in last for m in ("？", "?", "吗", "呢"))
            repeated = max(collections.Counter(signatures).values()) if signatures else 0
            open_slots = list(spec.unknown_slots or [])

            explained = bool(hallucinated) or repeated >= 3 or (
                target_printed and writes and not ((products | entities) & bound)
            ) or (open_slots and not asked) or (asked and not open_slots) or not writes
            if explained:
                continue

            shown += 1
            buckets[subtasks[index].domain] += 1
            print("=" * 78)
            print(f"{sim.get('task_id')} sub{index} [{subtasks[index].domain}/"
                  f"{spec.action}] term={traj.get('termination_reason')}")
            print(f"  instruction : {instruction[:70]}")
            print(f"  target_printed={target_printed} writes={sorted(set(writes))} "
                  f"bound_target={bool((products|entities) & bound)} "
                  f"bound_ids={sorted(v for v in bound if _ID_LIKE.match(v))[:4]}")
            print(f"  open_slots={open_slots} asked={asked} repeated={repeated} "
                  f"turns={len([m for m in messages if m.get('role')=='assistant'])}")
            print(f"  last turn   : {last[:170]}")
    print("=" * 78)
    print(f"unattributed shown: {shown}  by domain: {dict(buckets)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

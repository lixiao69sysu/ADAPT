"""Probe: what does a 'host listed, product absent' or 'product printed but
failed' trajectory actually look like mechanically?

Read-only inspection for offline attribution. Prints tool names, ranks and
truncated result head, never annotation values.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import agent.vitabench_bootstrap as bootstrap  # noqa: E402

bootstrap.enable_vitabench_utf8()

from scripts.target_reachability import (  # noqa: E402
    bound_ids,
    rank_of,
    result_records,
    target_ids,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("checkpoint")
    parser.add_argument("--cell", default="host_listed_product_absent")
    parser.add_argument("--limit", type=int, default=2)
    parser.add_argument("--language", default="chinese")
    args = parser.parse_args()

    with open(args.checkpoint, encoding="utf-8") as handle:
        checkpoint = json.load(handle)

    from agent.vitabench_runner import get_tasks

    tasks_by_id = {task.id: task for task in get_tasks(args.language)}
    shown = 0
    for sim in checkpoint.get("simulations", []):
        task = tasks_by_id.get(str(sim.get("task_id")))
        if task is None:
            continue
        rewards = ((sim.get("reward_info") or {}).get("info") or {}).get(
            "subtask_rewards"
        ) or {}
        trajs = (sim.get("states") or {}).get("integrity_subtask_trajectories") or []
        for traj in trajs:
            index = traj.get("subtask_idx")
            if index is None or index >= len(task.subtasks):
                continue
            reward = rewards.get(f"subtask_{index}_reward")
            if reward is None or float(reward) != 0.0:
                continue
            environment = task.subtasks[index].environment or {}
            entities, products, hosts = target_ids(environment)
            if not products:
                continue
            messages = traj.get("messages") or []

            returned_product = False
            returned_host = False
            for message in messages:
                if message.get("role") != "tool":
                    continue
                records = result_records(message.get("content"))
                for target in products:
                    if rank_of(target, records) is not None:
                        returned_product = True
                for host in hosts:
                    if rank_of(host, records) is not None:
                        returned_host = True

            cell = (
                "product_printed"
                if returned_product
                else ("host_listed_product_absent" if returned_host else "never")
            )
            if cell != args.cell:
                continue

            print("=" * 78)
            print(f"user={sim.get('task_id')} trial={sim.get('trial')} "
                  f"subtask={index} domain={task.subtasks[index].domain} reward={reward}")
            print(f"target_entities={sorted(entities)} target_products={sorted(products)}")
            print(f"hosts={sorted(hosts)}")
            print(f"bound ids in writes: {sorted(bound_ids(messages))}")
            print("-" * 78)
            for step, message in enumerate(messages):
                role = message.get("role")
                if role == "assistant":
                    for call in message.get("tool_calls") or []:
                        if not isinstance(call, dict):
                            continue
                        name = call.get("name")
                        arguments = call.get("arguments")
                        text = json.dumps(arguments, ensure_ascii=False)
                        print(f"  [{step:>3}] CALL {name} {text[:220]}")
                elif role == "tool":
                    records = result_records(message.get("content"))
                    print(f"  [{step:>3}] RESULT {len(records)} records")
                    for record in records[:6]:
                        marker = ""
                        for target in products:
                            if target in record:
                                marker = "  <<< TARGET PRODUCT"
                                break
                        if not marker:
                            for host in hosts:
                                if host in record:
                                    marker = "  <<< TARGET HOST"
                                    break
                        print(f"          {record[:200]}{marker}")
                    if len(records) > 6:
                        print(f"          ... {len(records) - 6} more")
            shown += 1
            if shown >= args.limit:
                return 0
    print(f"no examples found for cell={args.cell}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

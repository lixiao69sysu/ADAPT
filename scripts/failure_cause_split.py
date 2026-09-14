"""Whose problem is it? Split the two largest failure classes by cause.

E-084 ranked the failures, but a ranking does not say whether a class is the
agent's fault, the search's fault, or the task's. This script splits the top two
classes at the point that decides it, using only observable evidence.

CLASS 1  target_never_printed (144 failing runs)
    The target product never appeared in any tool result. Two very different
    situations hide inside that:
      * the target's *host* was printed -> the agent saw a list containing the
        right container and did not open it. Agent-side, bounded by its rank.
      * nothing target-related was printed at all -> the search never surfaced
        the container. Query-side or reachability-side.
    It also reports whether the agent searched at all, since "never printed"
    means something different when no search happened.

CLASS 2  ended_asking_the_user (116 failing runs)
    No write was attempted and the last assistant turn was a question. Whether
    that is a fault depends on what the instruction asked for:
      * commit  -> a transaction was requested and the agent deferred it
      * recommend -> no write was owed, so ending on a question is not the defect

Usage:
    python scripts/failure_cause_split.py data/simulations/stock_dev.json
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent.vitabench_bootstrap as bootstrap  # noqa: E402

bootstrap.enable_vitabench_utf8()

from agent.decision import TaskSpec  # noqa: E402
from scripts.target_reachability import (  # noqa: E402
    is_write_tool,
    rank_of,
    result_records,
    target_ids,
)

SEARCH_MARKERS = ("search", "recommend", "recommand")


def _split(checkpoint: dict, tasks_by_id: dict[str, Any]) -> dict[str, Any]:
    never = collections.Counter()
    never_ranks: list[int] = []
    asking = collections.Counter()
    asking_domains: collections.Counter = collections.Counter()
    examples_never: list[str] = []
    examples_asking: list[str] = []

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
            spec = TaskSpec.compile(instruction)
            entities, products, hosts = target_ids(subtasks[index].environment or {})

            tool_results = [
                result_records(m.get("content"))
                for m in messages
                if m.get("role") == "tool"
            ]
            searched = any(
                any(marker in str(call.get("name") or "").lower() for marker in SEARCH_MARKERS)
                for m in messages
                for call in (m.get("tool_calls") or [])
                if isinstance(call, dict)
            )
            wrote = any(
                is_write_tool(str(call.get("name") or ""))
                for m in messages
                for call in (m.get("tool_calls") or [])
                if isinstance(call, dict)
            )

            # CLASS 1
            if products:
                product_seen = False
                host_rank: int | None = None
                for records in tool_results:
                    if any(rank_of(p, records) is not None for p in products):
                        product_seen = True
                    for host in hosts or entities:
                        rank = rank_of(host, records)
                        if rank is not None and (host_rank is None or rank < host_rank):
                            host_rank = rank
                if not product_seen:
                    if host_rank is not None:
                        never["host_was_printed_but_not_opened"] += 1
                        never_ranks.append(host_rank)
                    elif searched:
                        never["searched_but_no_target_container"] += 1
                    else:
                        never["never_searched_at_all"] += 1
                    if len(examples_never) < 6:
                        examples_never.append(
                            f"[{subtasks[index].domain}] host_rank={host_rank} "
                            f"searched={searched} :: {instruction[:40]!r}"
                        )

            # CLASS 2
            last_text = ""
            for message in reversed(messages):
                if message.get("role") == "assistant":
                    content = message.get("content")
                    if isinstance(content, str) and content.strip():
                        last_text = content.strip()
                        break
            if not wrote and any(m in last_text for m in ("？", "?", "吗", "呢")):
                asking[spec.action] += 1
                asking_domains[subtasks[index].domain] += 1
                if len(examples_asking) < 6:
                    examples_asking.append(
                        f"[{subtasks[index].domain}/{spec.action}] "
                        f"{instruction[:38]!r} -> {last_text[:52]!r}"
                    )

    total_never = sum(never.values())
    return {
        "class_1_target_never_printed": {
            "runs": total_never,
            "host_was_printed_but_not_opened": never["host_was_printed_but_not_opened"],
            "searched_but_no_target_container": never[
                "searched_but_no_target_container"
            ],
            "never_searched_at_all": never["never_searched_at_all"],
            "host_rank_median": sorted(never_ranks)[len(never_ranks) // 2]
            if never_ranks
            else None,
            "host_rank_at_or_below_10": sum(1 for r in never_ranks if r <= 10),
            "examples": examples_never,
        },
        "class_2_ended_asking_the_user": {
            "runs": sum(asking.values()),
            "by_action_the_instruction_asked_for": dict(asking.items()),
            "by_domain": dict(asking_domains.most_common()),
            "examples": examples_asking,
        },
        "note": (
            "Mechanical only. 'host was printed' means the target's own container id "
            "appeared in a tool result the agent received, so the agent had the "
            "container in hand. It does not claim the agent should have opened it."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoint")
    parser.add_argument("--language", default="chinese")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    with open(args.checkpoint, encoding="utf-8") as handle:
        checkpoint = json.load(handle)
    from agent.vitabench_runner import get_tasks

    tasks_by_id = {task.id: task for task in get_tasks(args.language)}
    report = _split(checkpoint, tasks_by_id)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    for section, payload in report.items():
        if isinstance(payload, dict):
            print(f"\n{section}:")
            for key, value in payload.items():
                print(f"    {key}: {value}")
        else:
            print(f"\n{section}: {payload}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

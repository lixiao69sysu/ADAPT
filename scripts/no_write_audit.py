"""Zero-model audit: why did a failing subtask bind no candidate id at all?

E-066 split the failing runs whose target product *was* printed: 57 bound a
different observed id, and 60 bound no id at all. The second group is the larger
and the less understood one, and it is the one that could be a mechanical defect
rather than a model choice.

For each such run this prints, mechanically:

* whether any write tool was called at all;
* the termination reason;
* the write tool names and the argument keys used;
* whether any id-like token appeared in a write argument.

A run that never wrote, or wrote with no id, is a different failure from a run
that chose the wrong id -- and only the first kind is plausibly a data-layer fix.

Usage:
    python scripts/no_write_audit.py data/simulations/stock_avg4_8u.json
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent.vitabench_bootstrap as bootstrap  # noqa: E402

bootstrap.enable_vitabench_utf8()

from scripts.target_reachability import (  # noqa: E402
    bound_ids,
    is_write_tool,
    rank_of,
    result_records,
    target_ids,
)

_ID_LIKE = re.compile(r"^[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+$")
_PAY_TOOLS = ("pay", "payment")


def analyse(checkpoint: dict, tasks_by_id: dict[str, Any]) -> dict[str, Any]:
    patterns: collections.Counter = collections.Counter()
    terminations: collections.Counter = collections.Counter()
    by_action: collections.Counter = collections.Counter()
    by_domain: collections.Counter = collections.Counter()
    action_domain: collections.Counter = collections.Counter()
    endings: collections.Counter = collections.Counter()
    turn_counts: list[int] = []
    key_usage: collections.Counter = collections.Counter()
    write_tool_usage: collections.Counter = collections.Counter()
    examples: list[str] = []
    total = 0

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
            if reward is None or float(reward) != 0.0:
                continue
            environment = subtasks[index].environment or {}
            entities, products, hosts = target_ids(environment)
            if not products:
                continue
            messages = traj.get("messages") or []

            printed = False
            for message in messages:
                if message.get("role") != "tool":
                    continue
                records = result_records(message.get("content"))
                if any(rank_of(target, records) is not None for target in products):
                    printed = True
                    break
            if not printed:
                continue

            bound = bound_ids(messages)
            if products & bound:
                continue  # bound the target; not this population
            observed_other = any(
                _ID_LIKE.match(value) and value not in products for value in bound
            )
            if observed_other:
                continue  # a different id was bound: a choice, not a no-write

            total += 1
            terminations[str(traj.get("termination_reason"))] += 1

            # The instruction's own action decides whether a write was owed.
            # Recommendation-type subtasks do not require one, and the user's
            # diagnosis warns explicitly against reading every unwritten run as
            # a missed order.
            from agent.decision import TaskSpec

            spec = TaskSpec.compile(subtasks[index].instruction or "")
            by_action[spec.action] += 1
            by_domain[spec.domain] += 1
            action_domain[(spec.action, spec.domain)] += 1

            # What was the agent doing instead of writing? A final turn that asks
            # the user something and then gets stopped is the "先问后做" shape;
            # a final turn that presents options without a question is a stall.
            assistant_turns = [
                m for m in messages if m.get("role") == "assistant"
            ]
            last_text = ""
            for message in reversed(assistant_turns):
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    last_text = content.strip()
                    break
            asked_marker = any(
                marker in last_text for marker in ("？", "?", "吗", "呢")
            )
            if not assistant_turns:
                ending = "no_assistant_turn"
            elif not last_text:
                ending = "last_turn_was_a_tool_call"
            elif asked_marker:
                ending = "last_turn_asks_the_user"
            else:
                ending = "last_turn_states_something"
            endings[ending] += 1
            turn_counts.append(len(assistant_turns))

            writes: list[str] = []
            keys: set[str] = set()
            pay = False
            for message in messages:
                for call in message.get("tool_calls") or []:
                    if not isinstance(call, dict):
                        continue
                    name = str(call.get("name") or "")
                    if is_write_tool(name):
                        writes.append(name)
                        write_tool_usage[name] += 1
                        arguments = call.get("arguments") or {}
                        if isinstance(arguments, dict):
                            keys |= {str(k) for k in arguments}
                    if any(token in name.lower() for token in _PAY_TOOLS):
                        pay = True
            for key in keys:
                key_usage[key] += 1

            if not writes:
                pattern = "no_write_at_all" + ("_after_pay" if pay else "")
            else:
                id_like = [
                    value
                    for value in bound
                    if _ID_LIKE.match(value)
                ]
                pattern = (
                    "wrote_without_any_id"
                    if not id_like
                    else "wrote_with_a_non_target_id"
                )
            patterns[pattern] += 1
            if len(examples) < 8:
                examples.append(
                    f"[{subtasks[index].domain}] term={traj.get('termination_reason')} "
                    f"writes={sorted(set(writes))} keys={sorted(keys)[:6]} -> {pattern}"
                )

    return {
        "failing_runs_with_target_printed_and_no_other_id_bound": total,
        "pattern": dict(patterns.most_common()),
        "termination_reason": dict(terminations.most_common()),
        "action_the_instruction_asked_for": dict(by_action.most_common()),
        "domain": dict(by_domain.most_common()),
        "action_x_domain": {
            f"{action}/{domain}": count
            for (action, domain), count in action_domain.most_common()
        },
        "how_the_run_ended": dict(endings.most_common()),
        "assistant_turns": {
            "min": min(turn_counts) if turn_counts else 0,
            "max": max(turn_counts) if turn_counts else 0,
            "mean": round(sum(turn_counts) / len(turn_counts), 1) if turn_counts else 0.0,
        },
        "write_tools_attempted": dict(write_tool_usage.most_common()),
        "write_argument_keys": dict(key_usage.most_common(15)),
        "examples": examples,
        "note": (
            "Mechanical only: it reads tool-call names and argument keys, never "
            "rubric text. 'no_write_at_all' means the agent never attempted a "
            "write, which is a different failure from choosing the wrong id."
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
    report = analyse(checkpoint, tasks_by_id)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    for key, value in report.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

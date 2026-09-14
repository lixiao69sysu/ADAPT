"""Long-horizon tool-use profile over a saved checkpoint.

Consolidates the ad-hoc audits written earlier in this project into one readable
report about *how the agent behaves across a long task*, which is the part a
per-subtask reward number hides:

* conversation shape        -- assistant turns and tool calls per subtask run
* tool mix                  -- which tools carry the work, and how concentrated it is
* repeat behaviour          -- identical call signatures replayed inside one run
* tool failures             -- results that report nothing found / an error
* expansion behaviour       -- did the run open a parent candidate at all, and how
                               often did it do so after the parent was listed
* decision shape            -- did the run ever attempt a write, and at which turn
* failure taxonomy          -- the mechanically separated failure populations

Everything is mechanical: tool names, argument signatures and result text. No
rubric, no reward-as-signal, no ``user_intention``.

Usage:
    python scripts/longhorizon_report.py data/simulations/stock_dev.json
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
    is_write_tool,
    rank_of,
    result_records,
    target_ids,
)

# Conservative "this call produced nothing usable" markers. Deliberately narrow:
# a false positive here would overstate the failure rate.
_EMPTY_MARKERS = ("no ", "not found", "none", "nothing", "empty", "没有", "未找到")
_DETAIL_TOOL_MARKERS = ("info", "detail", "_get_", "get_")
_ID_LIKE = re.compile(r"^[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+$")


def _percentile(values: list[int], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return float(ordered[min(len(ordered) - 1, int(len(ordered) * fraction))])


def _signature(name: str, arguments: Any) -> str:
    try:
        payload = json.dumps(arguments or {}, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        payload = str(arguments)
    return f"{name}:{payload}"


def _looks_empty(content: str) -> bool:
    head = (content or "").strip().casefold()[:120]
    if not head:
        return True
    return any(marker in head for marker in _EMPTY_MARKERS)


def analyse(checkpoint: dict, tasks_by_id: dict[str, Any]) -> dict[str, Any]:
    turns: list[int] = []
    calls_per_run: list[int] = []
    tool_calls: collections.Counter = collections.Counter()
    distinct_tools: list[int] = []
    empty_results = 0
    total_results = 0
    runs_with_repeat = 0
    max_repeat_overall = 0
    exercised: collections.Counter = collections.Counter()
    write_turn: list[int] = []
    taxonomy: collections.Counter = collections.Counter()
    graded = 0

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
            if reward is None:
                continue
            graded += 1
            messages = traj.get("messages") or []

            assistant_turns = 0
            run_calls: list[str] = []
            names: set[str] = set()
            opened_parents: set[str] = set()
            wrote = False
            first_write_at = 0
            for order, message in enumerate(messages):
                role = message.get("role")
                if role == "assistant":
                    assistant_turns += 1
                    for call in message.get("tool_calls") or []:
                        if not isinstance(call, dict):
                            continue
                        name = str(call.get("name") or "")
                        arguments = call.get("arguments") or {}
                        names.add(name)
                        run_calls.append(_signature(name, arguments))
                        tool_calls[name] += 1
                        if is_write_tool(name) and not wrote:
                            wrote = True
                            first_write_at = assistant_turns
                        if any(marker in name.lower() for marker in _DETAIL_TOOL_MARKERS):
                            for value in (arguments or {}).values():
                                if isinstance(value, str) and _ID_LIKE.match(value):
                                    opened_parents.add(value)
                elif role == "tool":
                    total_results += 1
                    if _looks_empty(message.get("content") or ""):
                        empty_results += 1

            turns.append(assistant_turns)
            calls_per_run.append(len(run_calls))
            distinct_tools.append(len(names))
            counts = collections.Counter(run_calls)
            worst = max(counts.values()) if counts else 0
            max_repeat_overall = max(max_repeat_overall, worst)
            if worst >= 3:
                runs_with_repeat += 1
            if opened_parents:
                exercised["opened_a_parent_candidate"] += 1
            if wrote:
                exercised["attempted_a_write"] += 1
                write_turn.append(first_write_at)

            if float(reward) == 1.0:
                taxonomy["pass"] += 1
                continue
            _entities, products, hosts = target_ids(subtasks[index].environment or {})
            if not products:
                taxonomy["fail_no_product_target"] += 1
                continue
            printed = False
            for message in messages:
                if message.get("role") != "tool":
                    continue
                records = result_records(message.get("content"))
                if any(rank_of(target, records) is not None for target in products):
                    printed = True
                    break
            if not printed:
                taxonomy["fail_target_never_printed"] += 1
            elif wrote:
                taxonomy["fail_wrote_but_not_the_target"] += 1
            else:
                text = ""
                for message in reversed(messages):
                    if message.get("role") == "assistant":
                        content = message.get("content")
                        if isinstance(content, str) and content.strip():
                            text = content.strip()
                            break
                if any(marker in text for marker in ("？", "?", "吗", "呢")):
                    taxonomy["fail_asked_then_abandoned"] += 1
                else:
                    taxonomy["fail_stalled_without_a_write"] += 1

    return {
        "graded_subtask_runs": graded,
        "conversation_shape": {
            "assistant_turns": {
                "mean": round(sum(turns) / len(turns), 1) if turns else 0.0,
                "median": _percentile(turns, 0.5),
                "p90": _percentile(turns, 0.9),
                "max": max(turns) if turns else 0,
            },
            "tool_calls_per_run": {
                "mean": round(sum(calls_per_run) / len(calls_per_run), 1)
                if calls_per_run
                else 0.0,
                "median": _percentile(calls_per_run, 0.5),
                "p90": _percentile(calls_per_run, 0.9),
                "max": max(calls_per_run) if calls_per_run else 0,
            },
            "distinct_tools_per_run": {
                "mean": round(sum(distinct_tools) / len(distinct_tools), 1)
                if distinct_tools
                else 0.0,
                "median": _percentile(distinct_tools, 0.5),
            },
        },
        "tool_mix_top": dict(tool_calls.most_common(12)),
        "tool_calls_total": sum(tool_calls.values()),
        "repeat_behaviour": {
            "runs_with_an_identical_call_replayed_3plus": runs_with_repeat,
            "share": round(runs_with_repeat / graded, 4) if graded else 0.0,
            "worst_identical_replay_in_one_run": max_repeat_overall,
        },
        "result_quality": {
            "tool_results": total_results,
            "looks_empty_or_error": empty_results,
            "share": round(empty_results / total_results, 4) if total_results else 0.0,
        },
        "expansion_and_decision": {
            key: {"n": value, "share": round(value / graded, 4) if graded else 0.0}
            for key, value in sorted(exercised.items())
        },
        "turns_until_first_write": {
            "median": _percentile(write_turn, 0.5),
            "p90": _percentile(write_turn, 0.9),
            "max": max(write_turn) if write_turn else 0,
        },
        "failure_taxonomy": dict(taxonomy.most_common()),
        "note": (
            "Mechanical counts over saved trajectories. 'looks_empty_or_error' is a "
            "narrow text heuristic and is a lower bound; the failure taxonomy only "
            "applies to subtasks whose target is a product."
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
    print(f"checkpoint: {args.checkpoint}")
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

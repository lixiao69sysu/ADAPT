"""Inspect a proactive-loop smoke checkpoint: did the loop actually close?

Reads the saved checkpoint and prints, for the smoke subtask: the loop counters
the agent recorded, and the user/assistant turns around the proposed question, so
we can see whether the model really asked it and what the loop did with the
reply.

Usage:
    python scripts/_proactive_smoke_inspect.py data/simulations/<smoke>.json
"""

from __future__ import annotations

import json
import pathlib
import sys


def main() -> int:
    path = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "")
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)

    for simulation in payload.get("simulations", []):
        states = simulation.get("states") or {}
        print(f"task={simulation.get('task_id')} trial={simulation.get('trial')}")
        print(f"  reward: {(simulation.get('reward_info') or {}).get('reward')}")
        # "proactive_loop" is the key pre-E-086 checkpoints used; the single
        # ADAPT Agent writes "adapt_agent".
        loop = states.get("adapt_agent") or states.get("proactive_loop")
        print(f"  adapt_agent counters: {loop}")
        trajectories = states.get("integrity_subtask_trajectories") or []
        for traj in trajectories:
            print(f"  --- subtask {traj.get('subtask_idx')} ({traj.get('subtask_id')}) "
                  f"termination={traj.get('termination_reason')}")
            messages = traj.get("messages") or []
            print(f"      messages: {len(messages)}")
            for index, message in enumerate(messages):
                role = message.get("role")
                content = message.get("content")
                if isinstance(content, list):
                    content = json.dumps(content, ensure_ascii=False)
                content = (content or "").strip().replace("\n", " ")
                calls = [
                    c.get("name")
                    for c in (message.get("tool_calls") or [])
                    if isinstance(c, dict)
                ]
                if role == "assistant":
                    print(f"      [{index:>2}] ASSISTANT calls={calls}")
                    if content:
                        print(f"           {content[:220]}")
                elif role == "user":
                    print(f"      [{index:>2}] USER: {content[:220]}")
                elif role == "tool":
                    print(f"      [{index:>2}] TOOL: {content[:110]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Cost and latency accounting from the two local checkpoints.

Both are the dev cohort; the baseline has 4 trials and the arm 1, so everything is
reported per subtask-unit as well as in total.

What is measured directly:
  model calls  = assistant messages carrying a `usage` block
  tokens       = that block's prompt_tokens / completion_tokens, per call
  latency      = the per-subtask `duration` recorded in the integrity trajectories,
                 and the per-simulation `duration`
Cost is not informative here: every endpoint is local, so actor cost is 0.0 by
construction, and that is reported rather than dressed up.
"""

import io
import json
import statistics

PATHS = [
    ("baseline (rewrite, 4 trials)", "data/simulations/stock_avg4_8u.json"),
    ("ADAPT (adapt memory, 1 trial)", "data/simulations/adapt8_1t.json"),
]


def account(path):
    d = json.load(io.open(path, encoding="utf-8"))
    prompt, completion, calls = [], [], 0
    subtask_durations, sim_durations = [], []
    subtasks = 0
    tool_calls = 0
    tool_errors = 0
    for s in d["simulations"]:
        sim_durations.append(s.get("duration") or 0.0)
        for m in s.get("messages") or []:
            if m.get("role") == "tool":
                if m.get("name"):
                    tool_calls += 1
                if m.get("error"):
                    tool_errors += 1
            u = m.get("usage")
            if m.get("role") == "assistant" and u:
                calls += 1
                prompt.append(u.get("prompt_tokens") or 0)
                completion.append(u.get("completion_tokens") or 0)
        for tr in (s.get("states") or {}).get("integrity_subtask_trajectories") or []:
            subtasks += 1
            if isinstance(tr.get("duration"), (int, float)):
                subtask_durations.append(tr["duration"])
    return {
        "sims": len(d["simulations"]),
        "subtasks": subtasks,
        "calls": calls,
        "prompt": prompt,
        "completion": completion,
        "subtask_durations": subtask_durations,
        "sim_durations": sim_durations,
        "tool_calls": tool_calls,
        "tool_errors": tool_errors,
    }


def pct(xs, q):
    xs = sorted(xs)
    i = min(len(xs) - 1, int(q * len(xs)))
    return xs[i]


print(f"{'metric':44} {'baseline':>14} {'ADAPT':>14} {'ratio':>8}")
print("-" * 84)
rows = []
A = {}
for label, path in PATHS:
    A[label] = account(path)
b, a = A[PATHS[0][0]], A[PATHS[1][0]]


def line(name, fb, fa, fmt="{:.3f}"):
    try:
        r = fa / fb if fb else float("nan")
    except ZeroDivisionError:
        r = float("nan")
    print(f"{name:44} {fmt.format(fb):>14} {fmt.format(fa):>14} {r:>8.2f}")


line("simulations", b["sims"], a["sims"], "{:.0f}")
line("subtask units", b["subtasks"], a["subtasks"], "{:.0f}")
line("model calls (total)", b["calls"], a["calls"], "{:.0f}")
line("model calls per subtask", b["calls"] / b["subtasks"], a["calls"] / a["subtasks"])
line("tool calls per subtask", b["tool_calls"] / b["subtasks"], a["tool_calls"] / a["subtasks"])
print()
line("prompt tokens per call (mean)",
     statistics.mean(b["prompt"]), statistics.mean(a["prompt"]), "{:.0f}")
line("prompt tokens per call (p50)",
     pct(b["prompt"], 0.5), pct(a["prompt"], 0.5), "{:.0f}")
line("prompt tokens per call (p95)",
     pct(b["prompt"], 0.95), pct(a["prompt"], 0.95), "{:.0f}")
line("prompt tokens per call (max)",
     max(b["prompt"]), max(a["prompt"]), "{:.0f}")
print()
line("completion tokens per call (mean)",
     statistics.mean(b["completion"]), statistics.mean(a["completion"]), "{:.1f}")
line("prompt tokens per subtask",
     sum(b["prompt"]) / b["subtasks"], sum(a["prompt"]) / a["subtasks"], "{:.0f}")
line("completion tokens per subtask",
     sum(b["completion"]) / b["subtasks"], sum(a["completion"]) / a["subtasks"], "{:.0f}")
line("total tokens per subtask",
     (sum(b["prompt"]) + sum(b["completion"])) / b["subtasks"],
     (sum(a["prompt"]) + sum(a["completion"])) / a["subtasks"], "{:.0f}")
print()
line("latency per subtask (s, mean)",
     statistics.mean(b["subtask_durations"]), statistics.mean(a["subtask_durations"]), "{:.1f}")
line("latency per subtask (s, p95)",
     pct(b["subtask_durations"], 0.95), pct(a["subtask_durations"], 0.95), "{:.1f}")
line("latency per (user, trial) (min, mean)",
     statistics.mean(b["sim_durations"]) / 60, statistics.mean(a["sim_durations"]) / 60, "{:.1f}")
line("wall clock (h, all sims summed)",
     sum(b["sim_durations"]) / 3600, sum(a["sim_durations"]) / 3600, "{:.1f}")
print()
print(f"{'actor cost (USD)':44} {'0.00 (local)':>14} {'0.00 (local)':>14}")
print()
print(f"tool errors: baseline {b['tool_errors']}/{b['tool_calls']}  "
      f"ADAPT {a['tool_errors']}/{a['tool_calls']}")

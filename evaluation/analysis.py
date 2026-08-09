"""Ablation study analysis for ADAPT.

Compares different configurations and generates comparison tables.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Dict, List


def load_results(path: Path) -> dict:
    """Load simulation results from JSON."""
    with open(path) as f:
        return json.load(f)


def compute_stats(results: dict) -> dict:
    """Compute statistics from simulation results."""
    simulations = results.get("simulations", [])
    if not simulations:
        return {"error": "no simulations"}
    
    rewards = [s["reward_info"]["reward"] for s in simulations]
    n_subtasks = [s["reward_info"]["info"].get("num_subtasks", 0) for s in simulations]
    n_passed = [
        sum(1 for v in s["reward_info"]["info"].get("subtask_rewards", {}).values() if v > 0)
        for s in simulations
    ]
    
    # Skill split
    skill_stats = defaultdict(list)
    for sim in simulations:
        for subtask_idx, skills in sim["reward_info"]["info"].get("subtask_skill_tested", {}).items():
            reward = sim["reward_info"]["info"]["subtask_rewards"].get(f"{subtask_idx}_reward", 0)
            for skill in skills or ["personalize"]:
                skill_stats[skill].append(reward)
    
    return {
        "avg_reward": sum(rewards) / len(rewards),
        "pass@1": sum(1 for r in rewards if r > 0) / len(rewards),
        "avg_subtasks": sum(n_subtasks) / len(n_subtasks),
        "avg_passed": sum(n_passed) / len(n_passed),
        "skill_split": {
            skill: sum(vals) / len(vals) for skill, vals in skill_stats.items()
        },
    }


def compare_configs(config_results: Dict[str, str]) -> str:
    """Generate comparison table for different configurations."""
    rows = []
    for config_name, path in config_results.items():
        try:
            results = load_results(Path(path))
            stats = compute_stats(results)
            rows.append((config_name, stats))
        except Exception as e:
            rows.append((config_name, {"error": str(e)}))
    
    # Generate markdown table
    lines = ["| 配置 | Avg@4 | Pass@4 | 平均通过子任务 |",
             "|------|:---:|:---:|:---:|"]
    for name, stats in rows:
        if "error" in stats:
            lines.append(f"| {name} | ERROR | - | - |")
        else:
            lines.append(
                f"| {name} | {stats['avg_reward']:.3f} | {stats['pass@1']:.3f} | {stats['avg_passed']:.1f} |"
            )
    
    return "\n".join(lines)


def time_decay_analysis(results: dict) -> str:
    """Analyze performance decay over task sequence position."""
    simulations = results.get("simulations", [])
    
    # Group subtasks by position (quartile)
    position_rewards = defaultdict(list)
    for sim in simulations:
        rewards = sim["reward_info"]["info"].get("subtask_rewards", {})
        n = len(rewards)
        if n == 0:
            continue
        for subtask_idx, (key, reward) in enumerate(sorted(rewards.items())):
            # Normalize position to quartile
            quartile = min(3, subtask_idx * 4 // n)
            position_rewards[quartile].append(reward)
    
    lines = ["| 位置区间 | 平均得分 | 子任务数 |",
             "|:---:|:---:|:---:|"]
    quartile_names = ["1-25%", "26-50%", "51-75%", "76-100%"]
    for q in range(4):
        if q in position_rewards:
            avg = sum(position_rewards[q]) / len(position_rewards[q])
            lines.append(f"| {quartile_names[q]} | {avg:.3f} | {len(position_rewards[q])} |")
        else:
            lines.append(f"| {quartile_names[q]} | - | 0 |")
    
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "compare":
        # Example usage
        configs = {
            "ADAPT-Full": "evaluation/vitabench/data/simulations/final_u642088.json",
        }
        print(compare_configs(configs))
    elif len(sys.argv) > 1 and sys.argv[1] == "decay":
        path = sys.argv[2] if len(sys.argv) > 2 else "evaluation/vitabench/data/simulations/final_u642088.json"
        results = load_results(Path(path))
        print(time_decay_analysis(results))

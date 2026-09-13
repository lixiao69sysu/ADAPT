"""Capability panel: the eight numbers, with each definition fixed.

Mirrors the panel layout

    总样本 / 成功率 / 约束满足率 / 主动询问准确率 / 无效提问率 /
    关键漏问率 / 平均步数 / 平均成本

with an explicit, reproducible definition for every row, and the cached baseline
computed the same way wherever the baseline can support it. Rows the baseline
cannot support are marked n/a with the reason, rather than being left blank or
silently filled with a different quantity.

Definitions
-----------
总样本        scored subtask-trial records. The arm has 100 (8 users x 1 trial);
              the baseline has 400 (x4 trials). Official units are 100 for both;
              per-trial records are replicates, not independent samples.
成功率        subtask reward == 1.0 (the vendored strict-success rule).
约束满足率    from states["rubric_detail"]: mean over graded subtasks of
              fraction_met, and the pooled per-condition rate
              (sum n_met / sum n_conditions). Baseline lacks rubric_detail.
提问命中率    answers_resolved_to_a_value / questions_committed. The mechanism's
              own counter: did the question become a usable slot value.
主动询问准确率 1 - (over_asking units / units), where over_asking is the
              mechanical primary label from scripts/mechanism_attribution.py.
无效提问率    over_asking units / units (asked when the task did not need it).
关键漏问率    missed_question units / units (never asked what was needed).
平均步数      messages per subtask trajectory.
平均成本      sum(agent_cost + user_cost) / simulations. Local models bill 0.

Zero-model: reads saved artifacts, plus one subprocess call to the existing
mechanism-attribution device so the two never drift apart.
"""

from __future__ import annotations

import json
import pathlib
import statistics
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ARM = pathlib.Path("data/simulations/adapt8_1t.json")
BASE = pathlib.Path("data/simulations/stock_avg4_8u.json")


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def attribution(path):
    """Run the existing device and return {mechanism: runs}."""
    done = subprocess.run(
        [sys.executable, "scripts/mechanism_attribution.py", str(path), "--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if done.returncode != 0:
        return None
    payload = json.loads(done.stdout.lstrip("\ufeff"))
    return {
        row["mechanism"]: row["runs"] for row in payload["attribution"]
    }, payload


def panel(path, label):
    data = load(path)
    sims = data.get("simulations") or []
    rewards, steps = [], []
    frac, met, cond = [], 0, 0
    cost = 0.0
    for sim in sims:
        states = sim.get("states") or {}
        info = ((sim.get("reward_info") or {}).get("info") or {})
        rewards.extend(float(v) for v in (info.get("subtask_rewards") or {}).values())
        for tr in states.get("integrity_subtask_trajectories") or []:
            steps.append(len(tr.get("messages") or []))
        detail = states.get("rubric_detail")
        if detail:
            for rec in (detail.get("per_subtask") or {}).values():
                if isinstance(rec.get("n_conditions"), int) and rec["n_conditions"]:
                    frac.append(rec["fraction_met"])
                    met += rec["n_met"]
                    cond += rec["n_conditions"]
        cost += float(sim.get("agent_cost") or 0.0) + float(sim.get("user_cost") or 0.0)

    units = len(rewards)
    mech = attribution(path)
    over = miss = None
    if mech:
        counts, payload = mech
        # Rates over scored subtask-trial records, matching the 总样本 row.
        over = counts.get("over_asking")
        miss = counts.get("missed_question")

    out = {
        "label": label,
        "总样本": units,
        "成功率": statistics.mean(rewards) if rewards else float("nan"),
        "约束满足率_fraction_met": statistics.mean(frac) if frac else None,
        "约束满足率_per_condition": (met / cond) if cond else None,
        "无效提问率": (over / units) if (over is not None and units) else None,
        "关键漏问率": (miss / units) if (miss is not None and units) else None,
        "平均步数": statistics.mean(steps) if steps else float("nan"),
        "平均成本": cost / len(sims) if sims else float("nan"),
        "_over": over,
        "_miss": miss,
    }
    return out


def main() -> None:
    arm = panel(ARM, "arm")
    base = panel(BASE, "base")

    # question-engine rows exist for the arm only
    raw = load(ARM)
    q = l = r = 0
    for sim in raw.get("simulations") or []:
        ev = (sim.get("states") or {}).get("adapt_agent") or {}
        q += ev.get("questions_committed", 0)
        l += ev.get("answers_linked", 0)
        r += ev.get("answers_resolved_to_a_value", 0)

    def fmt(v, scale=1.0, pct=True, digits=1):
        if v is None:
            return "n/a"
        return f"{100 * v:.{digits}f}%" if pct else f"{v:.{digits}f}"

    rows = [
        ("总样本", f"{arm['总样本']}", f"{base['总样本']}"),
        ("成功率", fmt(arm["成功率"]), fmt(base["成功率"])),
        ("约束满足率 (fraction_met)",
         fmt(arm["约束满足率_fraction_met"]), fmt(base["约束满足率_fraction_met"])),
        ("约束满足率 (逐条件)",
         fmt(arm["约束满足率_per_condition"]), fmt(base["约束满足率_per_condition"])),
        ("提问命中率 (落值/提交)",
         fmt(r / q if q else None), "n/a (基线无提问引擎)"),
        ("主动询问准确率 = 1 - 无效提问率",
         fmt(1 - arm["无效提问率"] if arm["无效提问率"] is not None else None),
         fmt(1 - base["无效提问率"] if base["无效提问率"] is not None else None)),
        ("无效提问率 (over_asking / 样本)",
         fmt(arm["无效提问率"]), fmt(base["无效提问率"])),
        ("关键漏问率 (missed_question / 样本)",
         fmt(arm["关键漏问率"]), fmt(base["关键漏问率"])),
        ("平均步数 (消息/子任务)", f"{arm['平均步数']:.1f}", f"{base['平均步数']:.1f}"),
        ("平均成本", f"{arm['平均成本']:.2f}", f"{base['平均成本']:.2f}"),
    ]
    print("=" * 74)
    print(f"{'指标':34} {'臂 (adapt)':>16} {'基线 (stock+rewrite)':>20}")
    print("=" * 74)
    for name, a, b in rows:
        print(f"{name:34} {a:>16} {b:>20}")
    print("=" * 74)
    print(f"问句原始计数: 提交 {q} / 关联 {l} / 落成槽值 {r}"
          f"   (over_asking {arm['_over']} 单元, missed_question {arm['_miss']} 单元)")
    print(f"基线同一设备: over_asking {base['_over']} 单元 / 400, "
          f"missed_question {base['_miss']} 单元 / 400")
    print()
    print("口径提醒: 成功率与各率为逐试次记录(臂 100 / 基线 400); 官方单位两者都是 100。")
    print("          基线为 4 试次, 臂为 1 试次, 故基线各率的分辨率更高。")


if __name__ == "__main__":
    main()

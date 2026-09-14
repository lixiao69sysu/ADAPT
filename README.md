# ADAPT

Current user-directed experiment (2026-09-13): the agent side is converged to
**one** ADAPT agent, `--agent adapt` → `AdaptAgent` (`agent/adapt_agent.py`). It
is the stock tool loop plus per-turn *observation* of the proactive question
loop: it may only observe and carry state, never decide, and it never modifies,
replaces, reorders or preempts the model's message, and never blocks a tool call
or a write. The current acceptance target is **official Avg@4 >= 0.35 on the same
eight users as `stock_avg4_8u.json`**. This supersedes the older stock-only/56-user
promotion scope described below. The convergence itself changes no score.

The initial experiment keeps `--memory-type rewrite` to isolate the agent change:

```powershell
python -m agent.vitabench_runner --agent adapt --cohort dev `
  --task-ids E057330 E941775 J365414 M793481 O309411 P722245 Q089190 U000828 `
  --num-trials 4 --memory-type rewrite `
  --agent-llm qwen38-agent --user-llm qwen35-user --evaluator-llm qwen36-evaluator `
  --save-to data/simulations/adapt_agent_v1_8u_avg4.json
python scripts/eight_user_score.py data/simulations/adapt_agent_v1_8u_avg4.json
```

Set the model/memory overlay environment variables as shown below before running.
The checkpoint records source fingerprints and effective inference settings;
resuming with changed runtime sources is rejected. The observer's per-subtask
counters are kept in `states.adapt_agent`, and the checkpoint identifies the
implementation as `adapt_agent_v1`.

Project decisions, reproduced failure modes, effective fixes and remaining
risks are maintained in [docs/ADAPT_ENGINEERING_LOG.md](docs/ADAPT_ENGINEERING_LOG.md).
The loop structure, its `[V]`/`[A]` ownership and the four injection points are
in [docs/AGENT_ARCHITECTURE.md](docs/AGENT_ARCHITECTURE.md).

ADAPT 运行在只读 VitaBench 2.0 之上，`evaluation/vitabench` 不做任何源码修改。
当前主线是 **stock 骨架 + 我们的数据层**：行为基线与原版 `PersonalizationAgent`
一致，个性化能力全部放在可插拔的记忆层里。V1 控制器（`ADAPTAgent`）、`agent/landing.py`
与 `agent/v2/` 已删除，其经验留在账本与 `docs/AGENT_ARCHITECTURE.md`。

## 架构

```text
agent/vitabench_runner.py                          # 外部 runner：唯一入口
  -> pristine VitaBench components                 # L1 跨子任务 / L2 对话循环（只读）
  -> PersonalizationAgent (stock skeleton)         # 20 行单步循环，全工具暴露
       -> RewriteMemory                            # 官方 "Agentic Memory" 后端
       -> ADAPTMemory                              # 我们的数据层（--memory-type adapt）
            -> Signal evidence stream
            -> incremental scoped FactStore
            -> scoped single-dimension drift
            -> pure proactive question proposal
            -> bounded LLM profile summary（--profile-summary）

  -> AdaptAgent(PersonalizationAgent)              # 唯一自研 agent（--agent adapt）：纯观察者
```

模型负责开放世界语义、搜索、候选判断与执行计划。数据层只提供**可直接使用的
维度结论**（含证据与作用域），不替模型排序、不隐藏工具、不自动追问、不自动
结束。运行时不读取 reward、rubric、target ID 或 target/distraction 标记。

## 验证

```powershell
./scripts/test_agent.ps1
```

该命令运行 Agent 测试、编译检查，并确认 VitaBench 源码无 diff。

## 评测命令

```powershell
# 主线：stock 骨架 + 数据层 + 有界画像
python -m agent.vitabench_runner `
  --agent stock --cohort dev --num-trials 4 --profile-summary `
  --memory-type adapt `
  --agent-llm qwen38-agent --user-llm qwen35-user `
  --evaluator-llm qwen36-evaluator `
  --save-to data/simulations/adapt_dev.json

# 对照：官方 Agentic Memory 后端
python -m agent.vitabench_runner `
  --agent stock --cohort dev --num-trials 4 `
  --memory-type rewrite `
  --agent-llm qwen38-agent --user-llm qwen35-user `
  --evaluator-llm qwen36-evaluator `
  --save-to data/simulations/stock_dev.json

# oracle 上界
--agent stock --memory-type groundtruth
```

`--agent` 只有 `stock`（原版骨架）与 `adapt`（`AdaptAgent`，纯观察者）两个取值。
`--landing-guard`、隔离开关（`--no-phase-gating` 等）以及 V1 控制器已随 `ADAPTAgent` 一起删除，
见 [docs/AGENT_ARCHITECTURE.md](docs/AGENT_ARCHITECTURE.md) 第 9 节。

## 配对测量

单臂的用户级均值在 1 trial 下带 ±0.04 噪声，比要判定的效应还大，所以对比必须
逐单元配对，并匹配 seed 与 trial 数：

```powershell
python scripts/paired_arms.py A.json B.json --label stock --label adapt --trial 0
```

输出不一致对的方向计数与符号检验。**不要拿 1 trial 的结果去比 4-trial 均值**——
这正是 E-053 记录的归因错误。

分级诊断（`states["rubric_detail"]`，逐条命中数与缺失维度）：

```powershell
python scripts/rubric_breakdown.py data/simulations/adapt_dev.json --per-subtask
```

## 资源受限评测

开发集和盲验集由稳定 hash `ADAPT-2026` 各选 8 个用户。日常仅运行相关单测和
1–2 用户 smoke；stock dev baseline 只运行一次并缓存。

**注意**：本仓库历史上存在两批不同的 "8 dev users"，交集只有 2 个用户
（`E057330`、`Q089190`）。跨批次的分数不可比，引用任何基线前先核对其用户列表。

evaluator 的 5xx/超时会重试；连续失败的轨迹保留但 `reward_info=null`，不会被
当作真实 0 分。服务恢复后可只重评保存的轨迹——不重放 agent 与用户模拟器：

```powershell
# 单用户
python -m agent.reevaluate_checkpoint data/simulations/run.json `
  --evaluator-llm qwen36-evaluator

# 全量重评并落盘分级判定
python -m agent.reevaluate_guarded `
  --checkpoint data/simulations/run.json `
  --output data/simulations/run_rubric_detail.json `
  --all --evaluator-llm qwen36-evaluator --normalize-extracter
```

只有通过 8-user dev 和 blind 后才运行 56-user Avg@1；代码冻结后再运行 Avg@4。

## 当前状态

主线与原版 `Agentic Memory` 基线**尚未证实有优势**：匹配 seed 的逐单元配对显示
Δ = +0.0000（z = 0.00），另一队列 7 用户上 Δ = −0.0103。目标 0.35 距基线
0.2925 还差 +0.0575，而**没有任何配置在 8 用户 × 4 trial 上被测过**。

更多边界、晋级条件和命令见 `CLAUDE.md`。

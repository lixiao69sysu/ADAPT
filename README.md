# ADAPT

Project decisions, reproduced failure modes, effective fixes and remaining
risks are maintained in [docs/ADAPT_ENGINEERING_LOG.md](docs/ADAPT_ENGINEERING_LOG.md).

ADAPT 运行在只读 VitaBench 2.0 之上。当前主线是以原版
`PersonalizationAgent` 为行为基线的 ADAPT V2；V1 已冻结，只用于实验复现和
架构对照。外部 runner 组合原版环境、用户模拟器和 evaluator，
`evaluation/vitabench` 不做源码修改。

## 架构

```text
agent/vitabench_runner.py
  -> pristine VitaBench components
  -> ADAPTV2(PersonalizationAgent)
       -> RewriteMemory + evidence-linked BeliefStore
       -> ObservationStore + candidate-conditioned retrieval
       -> advisory DecisionWorkspace + ModelPlanner + VOI
       -> independent OperationJournal + TransactionKernel
       -> StockCompatibleExecutor

  -> ADAPTAgent(PersonalizationAgent)  # frozen V1 comparison only
```

模型负责开放世界语义、搜索、候选判断和执行计划。V2 只组织证据并保护
可机械验证的事务不变式；不引入 V1 的 TaskSpec、硬排序、工具隐藏、
自动追问或自动结束。运行时不读取 reward、rubric、target ID 或
target/distraction 标记。

## 验证

```powershell
./scripts/test_agent.ps1
```

该命令运行 Agent 测试、编译检查，并确认 VitaBench 源码无 diff。

## 资源受限评测

开发集和盲验集由稳定 hash `ADAPT-2026` 各选 8 个用户。日常仅运行相关单测
和 1–2 用户 smoke；stock dev baseline 只运行一次并缓存。当前已知 evaluator
模型映射错误会导致 stock baseline 无法完成，修复外部模型配置前不要把空缓存
当作 baseline 成绩。

```powershell
python -m agent.vitabench_runner `
  --agent adapt_v2 --cohort dev `
  --agent-llm qwen38-agent --user-llm qwen35-user `
  --evaluator-llm qwen36-evaluator `
  --save-to data/simulations/adapt_v2_parity_dev.json
```

不传 `--v2-features` 时走 Stock 等价路径。功能必须逐项评测：

```powershell
# Stock + Hybrid Memory
--v2-features hybrid_memory

# V2 Planner + RewriteMemory
--v2-features decision_workspace planner

# Full V2
--v2-features hybrid_memory decision_workspace planner voi_questions transaction_enforcement

# Stock + Groundtruth oracle
--agent stock --memory-type groundtruth
```

evaluator 的 5xx/超时会重试；连续失败的轨迹保留但 `reward_info=null`，
不会被当作真实 0 分。服务恢复后可只重评保存的轨迹：

```powershell
python -m agent.reevaluate_checkpoint data/simulations/run.json `
  --evaluator-llm qwen36-evaluator
```

对照报告和晋级门槛（dev `+0.03`、personalize `-0.01` 底线、proactive
`+0.05`、blind 不回归、净胜样本为正）：

```powershell
python -m agent.v2.evaluation stock_dev.json candidate_dev.json `
  --blind-baseline stock_blind.json --blind-candidate candidate_blind.json
```

只有通过 8-user dev 和 blind 后才运行 56-user Avg@1；代码冻结后再运行
Avg@4。

更多边界、晋级条件和命令见 `CLAUDE.md`。

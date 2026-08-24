# ADAPT

Project decisions, reproduced failure modes, effective fixes and remaining
risks are maintained in [docs/ADAPT_ENGINEERING_LOG.md](docs/ADAPT_ENGINEERING_LOG.md).

ADAPT 是运行在只读 VitaBench 2.0 之上的完整个性化消费 Agent。项目只保留
一条路线：外部 runner 组合原版环境、用户模拟器、orchestrator 和 evaluator，
所有决策、记忆、工具治理和 trace 分析均位于本仓库根目录的 `agent/` 中。

## 架构

```text
agent/vitabench_runner.py
  -> pristine VitaBench components
  -> ADAPTAgent(PersonalizationAgent)
       -> typed TaskSpec / bounded DecisionCard
       -> TaskRuntime / ToolRegistry / QuestionGate
       -> CandidateLedger / CandidateRanker / write validation
       -> per-user executable ExecutionLessonStore
       -> ADAPTMemory / incremental FactStore
```

运行时不读取 reward、rubric、target product ID 或 target/distraction 标记；
VitaBench 的 prompt、工具、数据库、模拟器、orchestrator、evaluator 和 metrics
均不修改。

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
  --agent adapt --cohort dev `
  --agent-llm qwen38-agent --user-llm qwen35-user `
  --evaluator-llm qwen36-evaluator `
  --save-to data/simulations/adapt_dev.json `
  --debug-to data/traces/adapt_dev.jsonl
```

对照组使用同一纯净环境下的 stock `PersonalizationAgent + rewrite`。先通过
8-user dev 和 blind 门槛，再运行 56-user Avg@1；代码冻结后才运行 Avg@4。
最终目标是完整 56 用户 Avg@4 >= 0.35。

更多边界、晋级条件和命令见 `CLAUDE.md`。

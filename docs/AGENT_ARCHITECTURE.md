# ADAPT 整体数据流与 agent 流

2026-09-13 用户重新明确目标：允许自研 agent 架构，在 stock 已跑的同开发集用户上
达到官方 Avg@4 >= 0.35。agent 侧随后收敛为**唯一一个** agent：`--agent adapt` →
`AdaptAgent`（`agent/adapt_agent.py`），即 stock 骨架加每回合对主动提问闭环的
**观察**。它只观察、只记账，不做任何决定：不改写、不替换、不重排、不抢占模型消息，
也不阻断任何工具调用或写入。此前三条实验路径——EvidenceAgent（无评分产物）、
CandidateMarkingAgent（实测无增益）、ThrashGuardAgent（自身 smoke 零触发）——全部
删除（E-086）；收敛本身**不改变任何分数**，只是让"改一个 agent 再测量"成为可能。
默认实验保留 RewriteMemory。这条路径尚在冒烟，不能称已提分。下文 stock-only 描述
与“注入点1没有实现”属于本次实验之前的架构记录；已删除的旧控制器没有恢复。

> 本图只描述**代码里实际存在的路径**，标注每层的归属与只读边界。
> 图例：`[V]` = vendored VitaBench，只读，不可修改；`[A]` = ADAPT 自有代码。

---

## 0. 入口

```
$ python -m agent.vitabench_runner \
      --agent {stock|adapt}                       adapt = 唯一自研 agent（纯观察者）
      --memory-type {rewrite|groundtruth|adapt}   默认 rewrite
      [--profile-summary] [--num-trials N] [--cohort dev|blind|final|all]
      [--no-agent-context-guard] [--no-normalize-extracter]
```

---

## 1. 全流程

```
[A] main()                                                      agent/vitabench_runner.py
     │
     ├─ patch_evaluator_extracter()          ← 评测器输出归一化（E-037）
     │
     └─ run_selected()                                          def @509
          │  选用户：stable_user_split("ADAPT-2026") → dev / blind / final
          │  读 checkpoint（同 info+tasks 才可续跑，否则报错）
          │
          └─ for trial in range(num_trials):        ★ L0 批量循环【A】      @617
               for task in selected:                 ★ 严格串行，无并发
                    │
                    └─ _run_one_simulation()                            def @438
                         │
                         └── run_stock_personalization_task()          def @309
                              │
                              ├── memory = 按 --memory-type 选：
                              │      RewriteMemory        [V] stock 自带（= 官方 "Agentic Memory"）
                              │      GroundtruthMemory    [V] oracle 上界
                              │      ADAPTMemory          [A] 我们的数据层（--memory-type adapt）
                              │
                              ├── agent = PersonalizationAgent   [V] stock（--agent stock）
                              │        = AdaptAgent             [A] 唯一自研 agent，仅观察（--agent adapt）
                              │
                              ├── user = PersonalizationUser   [V] 用户模拟器（模糊回答，满意即 ###STOP###）
                              │
                              └── 若 agent_context_guard：
                                     monkeypatch vita.utils.llm_utils.generate   ← ★ 注入点 3
                                     （上下文溢出时按 token 缺口裁剪后重试一次）
                              │
                              └── IntegrityPersonalizationOrchestrator(...)   [A] 子类化【注入点 2】
                                   └─ run()                                 [A] 重写
                                        └─ super().run()  →  L1
```

---

## 2. L1 跨子任务循环【V 只读】

`personalization_orchestrator.py` `run()` @97，`for i, subtask in enumerate(task.subtasks)` @119

```
每个 subtask（一个用户平均 ~14 个）：
│
├─ 2a  记忆更新
│     if subtask.interactions:  agent.process_interactions(interactions)
│                                 └─ memory.update(new_interactions, llm, llm_args)
│     [V] GroundtruthMemory 时在此注入 canonical 偏好
│     [A] rag_cache 的 set_current_location 钩子（hasattr 守卫）
│
├─ 2b  环境与上下文
│     environment = _setup_subtask_environment(subtask)          [V]
│     _inject_memory_tools(environment)        ← 把 memory 上 @is_tool 的方法挂进 domain toolkit
│     agent.update_tools(env.get_tools())                        [A] 覆盖：换成新工具集
│     domain_policy = base_prompt + personalization_addendum     [V]
│        （proactive 子任务换成 personalization_agent_proactive_system_prompt）
│     agent.time = subtask.environment["time"] + 星期            [V]
│     agent.set_current_instruction(subtask.instruction)         ← ★ 每子任务的唯一重置钩子
│     memory_read_content = agent.memory.read(query=instruction)  ← 注入 prompt 的记忆快照
│
├─ 2c  _run_subtask()  →  新建 Orchestrator(max_steps=100, max_errors=10).run()   → L2
│
├─ 2d  评测（有 evaluation_criteria 时）
│     [A] IntegrityPersonalizationOrchestrator._evaluate_subtask()   ← 重写【注入点 2】
│           ├─ super()  →  evaluate_simulation(...)  →  TrajectoryEvaluator
│           │      滑窗逐窗 LLM judge → nl_rubrics（逐条 met）
│           │                        → rubric_score = 命中比例
│           │                        → reward = 1.0 仅当【全部命中】
│           ├─ 失败则重试 evaluator_retries=2（不退避重放 agent）
│           ├─ 记录 evaluation_records（区分"真 0"与"评测不可用"）
│           └─ [A] 新增：_record_rubric_detail() → 内容无关的分级记录
│
└─ user.mark_subtask_completed(); user.advance_to_next_subtask()

聚合：_aggregate_rewards()  【V】只保留 subtask_{i}_reward（二值）
                          【A】重写：过滤评测失败的假 0
                          【A】新增：states["rubric_detail"] = summarize(...)
```

---

## 3. L2 单子任务对话循环【V 只读】

`orchestrator.py` `run()` @213，`while not self.done` @223

```
        ┌───────────────────────────────────────────────────────────┐
        │  step_count += 1;  检查 max_steps / max_errors             │
        │                                                            │
        │   to_role == AGENT ──→ agent.generate_next_message(msg, st) │ ──→ L3
        │                          │                                 │
        │                          ├─ agent_msg.is_tool_call() ?     │
        │                          │     是 → ENV：executor 执行工具  │
        │                          │           len(tool_msgs) > 1 ?  │
        │                          │             MultiToolMessage     │
        │                          │             : tool_msgs[0]  ★裸 ToolMessage│
        │                          │     否 → USER：user.generate_next_message │
        │                          │                                 │
        │                          └─ agent.is_stop(msg)? → done      │
        │                                                            │
        │   user_msg 含 ###STOP### / TRANSFER / OUT_OF_SCOPE → done   │
        └───────────────────────────────────────────────────────────┘
                                    ↓ 结束
                             evaluation_type="trajectory"
                                    ↓
                        从 L2 返回 → L1 的 2d 评测
```

★ 注意 L2 对工具结果的**两种投递形状**——单次调用送裸 `ToolMessage`，只有一轮多调用才包
`MultiToolMessage`。任何重写循环的类都必须同时处理两者，否则会静默失效（E-053 期间踩过）。

---

## 4. L3 agent 单步【V 基类；在用的只有 PersonalizationAgent 与其子类 AdaptAgent】

基类 `llm_agent.py` `LLMAgent.generate_next_message` @87：

```
        state.messages.append(message)
        messages = state.system_messages + state.messages      ← 全历史
        assistant = generate(model, tools=self.tools, messages=messages, ...)
        state.messages.append(assistant)
        return assistant, state
                                    ← 全项目最短的循环，约 20 行
```

`PersonalizationAgent` 只加一件事：`system_prompt` 里拼进 `memory.read(query=当前指令)`。
**stock 的全部"个性化"就是这一句。**

### 4a. ~~ADAPTAgent~~ —— 已删除（保留控制流记录）

曾存在唯一的"替换循环"实现：旧 `agent/adapt_agent.py`（1748 行）+ `runtime/` 控制器（2598 行）。
（该路径 2026-09-13 晚些时候被复用为新的 `AdaptAgent`，见 §4d；两者除文件名外无关。）
它于本轮被整体删除（3467 行）。下面这段控制流是**历史记录**，用于理解 E-032…E-052 的成因；
代码已不存在。

```
generate_next_message()  @324
│  _observe_input(message)  ← 更新 runtime / ledger / 记录观测
│
├── 阶段 A：框架先手（7~9 个方法，任一命中就 return，模型本轮不被调用）
│     1  phase == DONE          → 罐头完成语
│     2  _framework_date_grounding()      ← 相对日期由框架注入
│     3  _framework_payment_question()    ← 框架替模型催支付
│     4  _framework_parameter_probe()     ← 框架替模型问缺失参数
│     5  _framework_recovered_write()     ← 框架重试可修复的写
│     6  _framework_enrichment()          ← 框架发父→子候选展开搜索
│     7  _framework_entity_gap_search()   ← 框架发针对实体的补搜
│     8  framework_speech=True（默认关）→ _framework_recommendation() 定稿 + phase=DONE
│                                        → _framework_question() 框架替模型提问
│        否则 phase==SELECT 时只累加 _select_turns（有界逃逸计数）
│
└── 阶段 B：模型 + 校验（最多 2 次重规划 = 3 次尝试）
      for attempt in range(_replan_limit + 1):
         _refresh_system_message(state)      ← 重建 system prompt
         compact_messages(state.messages)    ← 上下文裁剪
         allowed_tools = tool_registry.allowed_tools(...)   ★ 按 phase 裁剪工具
         generation_messages = _generation_messages(...)    ★ 写阶段【替换】历史
         assistant = generate(...)
         problems = _preflight(assistant, allowed_tools)    ★ 写前校验
         if not problems: 采纳 → return
         ├ 全为 "question gate:" 且 phase∈{SELECT,SEARCH,NEED_INFO}
         │   且有候选/已授权/choice_settled → promote_if_settled() 再试（E-049 逃逸口）
         └ 否则拒绝理由作为 ToolMessage(error=True) 塞回 + 记 lesson + 重规划
      预算耗尽 → _fallback_message()
             READY_TO_PAY → "订单已创建，目前尚未支付"
             NEED_INFO    → 问该槽位（E-050 修复）
             其他         → "现有候选无法满足硬约束，我没有执行下单"  ← 可能为假拒绝（A1）
```

### 4b. ~~ADAPTV2~~ —— 已删除

曾存在第四个实现：`agent/v2/`，flag 驱动、全部 flag 默认关（等价 stock），且是
`--agent` 的**默认值**。它**从未被任何测量使用**（32 份产物里 `agent_version`
一次都没出现过），却在无参数运行时被静默选中——这是全套代码里唯一一个"不改就
可能悄悄出事"的位置。已整体删除，`--agent` 默认值同时改为 `stock`。

删除前的可复用经验（flag 系统 / parity 契约 / `StockCompatibleExecutor` /
`ABLATION_MATRIX` / `promotion_report`）按用户决定一并放弃。

### 4c. ~~LandingGuardAgent~~ —— 已删除（保留机制记录）

曾存在 `agent/landing.py`（339 行）。**已被 E-053 否证**（三单元落地了但分数不动），
随后随控制器一并删除。

```
generate_next_message()  @240
│  _observe_inbound(message)   ← 同时处理裸 ToolMessage 与 MultiToolMessage，扫候选 id
│
└── 成交类指令 ∧ 已见候选 ∧ 尚未写入 ∧ 消息以确认请求收尾
       → 用「执行时序指令」重生成一次（指令只进本轮局部消息，不入 state）
       → 重试未变好则保留原答案（不可能比 baseline 更差）

```

### 4d. `AdaptAgent` —— 唯一在用的子类（纯观察者，E-086）

`agent/adapt_agent.py`（219 行）继承 stock `PersonalizationAgent`，只覆写
`generate_next_message`，在 `super()` 前后各做一次**记账观察**：

```
generate_next_message(message, state)
│  _observe_inbound(message)              ← 用户回复若在回答已发的问题 → 链接并解析为槽值
├─ super().generate_next_message(...)     ← 模型发言，原样返回，不做任何修改
└─ _observe_outbound(assistant_message)   ← 模型确实问了提议的问题 → commit，花掉提问预算
```

- 不修改、不替换、不重排、不抢占模型消息；不阻断工具调用或写入；不替模型或用户决定。
- 只携带状态：每子任务的 `simulation.states["adapt_agent"] = agent.loop_events`
  （`questions_committed` / `answers_linked` / `answers_resolved_to_a_value`）。
- 不变量由 `agent/tests/test_proactive_loop.py::test_observer_never_modifies_the_model_message` 守护。

---

## 5. 记忆层【A】agent/memory/（3667 行）——与循环解耦

```
process_interactions(interactions)            L1 的 2a 调用
      │
      └─ ADAPTMemory.update()
           ├─ SignalParser（signals.py 660）     原始交互 → 结构化偏好 signal
           ├─ MemoryStream（stream.py）          有序事件 + 时间戳 + 重要度
           ├─ LifecycleManager（lifecycle.py）    生命周期与选择性遗忘
           ├─ FactStore（fact_store.py）          增量、保证据的事实
           │    └─ EntityIndex（entity_index）    实体/品牌历史的**有界**索引
           ├─ DriftDetector（drift.py）           作用域内单维漂移
           ├─ RetrievalScorer（retrieval.py）     相关性 × 新近度 × 重要度
           ├─ ProactiveEngine（proactive.py）     缺口检测 → 纯函数提问提案
           └─ 画像摘要：LLM 归纳成有界块（≤ summary_max_chars，默认 800）
                ↑ 由 --profile-summary 开启（enable_summary_rewrite）

read(query)  ──→ 被 L1 的 2b 调用一次，也被 system_prompt 每次调用
      └─ 返回【有界 Decision Card】≤8 条事实 / 1200 字符
         + 置顶的有界画像块

★ read() 必须是纯函数（重复读不得改变 salience / 提问预算 / 记忆状态）
★ 运行时绝不读 rubric / reward / target_product_ids / distraction 标注
```

---

## 6. 只读边界与四个注入点

```
┌───────────────────────── 只读 vendored ─────────────────────────┐
│  L1 跨子任务循环     personalization_orchestrator.py            │
│  L2 对话循环         orchestrator.py                            │
│  L3 基循环           agent/llm_agent.py                         │
│  用户模拟器          user/personalization_user.py                │
│  评测器              evaluator/*.py                             │
│  工具与环境          domains/*/                                 │
│                                                                 │
│  校验：git -C evaluation/vitabench diff --exit-code HEAD -- src/vita
└─────────────────────────────────────────────────────────────────┘
                              ↑ 只能通过下面四个口子影响

【注入点 1】override generate_next_message        AdaptAgent（唯一占用者；纯观察者，只读不改）
【注入点 2】子类化 orchestrator                   IntegrityPersonalizationOrchestrator
                                                   （_run_subtask / _evaluate_subtask /
                                                     _aggregate_rewards / run / reevaluate_saved）
【注入点 3】monkeypatch llm_utils.generate        runner 的上下文溢出护栏
【注入点 4】最外层组装                            runner 选 agent / 记忆 / 薄层，跑 L0
```

**关键结构性结论：调度循环不属于我们。** L1/L2 不可改，唯一"每回合"的注入点就是 L3 那一个方法——
而模型正要在这个方法里发言。**所以任何回合级控制都必然变成与模型竞争，这就是 `ADAPTAgent` 长成
两阶段、41 个方法的成因，也是二十条自伤条目的共同来源。**

对照：注入点 2 是**已验证的、非抢占的**——`IntegrityPersonalizationOrchestrator` 只重写几个方法
就实现了评测重试、轨迹保全、分级落盘，**完全没有和模型竞争，也没碰 L2 的 while 循环**。

---

## 7. 当前推荐主线（本轮会话后的状态）

两个可选骨架：`--agent stock`（stock `PersonalizationAgent`）或 `--agent adapt`
（`AdaptAgent`，纯观察者，只多记两个记账转移）。

```
--agent stock --memory-type adapt --profile-summary
     │
     ├─ 骨架：stock PersonalizationAgent（20 行循环，全工具暴露，无阶段机，无终局拒绝）
     ├─ 数据层：ADAPTMemory + 有界画像
     └─ 状态：匹配 seed 下与 stock 相比 Δ = +0.000（4 用户 47 单元，z=0.00）
              ⇒ 平价，尚未证实有优势

已删除：ADAPTAgent + runtime 控制器 + lessons.py（3467 行）
已删除：agent/v2/（1425 行，零测量且 CLI 默认指向它，是静默风险）
已删除：LandingGuardAgent（339 行 + 其测试，见 E-053）
已删除：EvidenceAgent / CandidateMarkingAgent / ThrashGuardAgent / ProactiveLoopAgent
        （无评分产物 / 实测无增益 / 零触发；其观察逻辑收敛进 AdaptAgent，见 E-086）
```

---

## 8. 一句话总览

```
runner(L0, 我们的)
   └─ 组装：memory[A] + agent[A/V] + user[V] + orchestrator[A 子类]
        └─ L1 跨子任务[V] ── 每子任务：memory.update → read 注入 → L2 → 评测[A 包装]
             └─ L2 对话循环[V] ── agent 单步 / 用户回话 / 工具执行
                  └─ L3 单步：V 基类 20 行；PersonalizationAgent + AdaptAgent（纯观察）两条链
```

---

## 9. 迁移状态（删除已完成）

### 9.1 `agent/tool_recovery.py` —— 已归档（不是现有能力）

> **2026-09-13 更新**：该模块**没有生产调用点**，只有它自己的单测引用它，已移到 `archive/tool_recovery.py`，
> 测试一并移到 `archive/test_tool_recovery.py`。归档理由见 `archive/README.md`。
> 本节余下内容保留为历史记录：它描述的是这个护栏**为什么曾被提出**，不代表 ADAPT 现在具备该能力。

控制器里唯一**不是策略而是护栏**的部分：它不替模型做任何决定，只是拒绝重复一个已被环境证明为坏的参数，并在**已被成功解析过**的参数里给出替代。

- 依据：E-043 / E-051（环境无法地理编码带楼栋/单元后缀的长地址；stock 骨架不记得自己失败过的调用）
- 独立性：原模块 `runtime/tool_errors.py` 依赖 `runtime.tools.ToolRole`（属控制器）。新模块用
  `is_write_tool(name)`（与全库 `create_*` / `instore_book` / `instore_reservation` 约定一致）替代，
  **不 import 控制器任何东西**
- `runtime/tool_errors.py` 已随控制器删除

### 9.2 `agent/runtime/__init__.py` 改为惰性（PEP 562）

**这是"能不能删控制器"的关键单点。** 原 `__init__` 急切重导出 6 个控制器模块，而导入任何子模块都会先执行包的 `__init__`——于是数据层里那一句 `from agent.runtime.alignment import ...` 把整个控制器拖进了闭包（12321 行而非 11799 行）。这曾让"删控制器"看起来像重写 `decision.py`。

改为首次属性访问时解析后，**用真实 import hook 屏蔽 10 个控制器模块，数据层仍可导入并运行**
（`TaskSpec.compile`、`ADAPTMemory.update/read`、`agent.tool_recovery` 全部通过）。

### 9.3 已实证的删除集（5095 行，尚未执行）

| 模块 | 行数 |
|---|---|
| `agent/adapt_agent.py` | 1748 |
| ~~`agent/v2/`（9 模块）~~ **已删** | ~~1425~~ |
| `agent/runtime/state.py` | 486 |
| `agent/landing.py` | 339 |
| `agent/runtime/tools.py` | 296 |
| `agent/runtime/tool_errors.py`（shim） | 265 |
| `agent/runtime/evolution.py` | 252 |
| `agent/runtime/question_gate.py` | 147 |
| `agent/lessons.py` | 105 |
| `agent/runtime/debug.py` | 32 |

**测试代价（已分拣）**：没有任何测试文件只测可删集；11 个文件是 MIXED，需**编辑而非删除**——共 66 个测试函数可删、165 个必须保留（它们测的是共享层）。其中最反直觉的是 `test_agent_architecture.py`：2662 行里只有 16/113 个函数碰控制器。

**方法学警告**：该分拣判据只看"测试函数是否引用模块级导入绑定的可删符号"，**会漏掉经由模块级 helper / fixture 的间接依赖**，所以 165 这个"保留"数是乐观值。删除后必须以全量测试 + 逐文件 `pytest --collect-only`（屏蔽可删集）复核。

### 9.4 必留（数据层硬依赖，已实测）

**数据层真实 import 闭包（2026-09-13 实测，由子进程断言）**：导入 `agent.memory.adapt_memory` 后，
`sys.modules` 中**不存在** `agent.candidate_ledger`、`agent.runtime.ranking`、`agent.runtime.location`。
该性质由 `agent/tests/test_agent_architecture.py::test_data_layer_import_closure_excludes_retired_controller_code`
守护。生产代码从 `agent.decision` 只取 `Candidate` / `TaskSpec` / `_valid_fact_value` / 决策卡相关符号。

必留：`agent/decision.py` · `agent/intent.py` · `agent/runtime/alignment.py`（经 `agent/memory/grounding.py`） ·
`agent/memory/**` · `agent/vitabench_runner.py` · `agent/{rubric_detail,evaluation_integrity,vitabench_bootstrap}.py` · 离线工具

**本节此前把 `runtime/{ranking,location,schedule}.py` 一并列为"已实测的数据层硬依赖"，这是错的。**
实测结论是：`ranking.py` 与 `location.py` 只被 `agent/candidate_ledger.py` 和测试引用，
`schedule.py` 只被 `agent/tests/test_schedule.py` 引用——三者都不在数据层闭包里。
它们**保留**（不是"因为默认路径没用就整包删"，而是因为它们承载候选排序的语义知识，
且 E-062 的约束–候选对齐干预可能要复用），但**不得再被描述为数据层依赖或现有能力**。

### 9.5 `agent/candidate_ledger.py` —— 从 `decision.py` 拆出的退役控制器残留

`CandidateLedger` 原有 612 行嵌在 `decision.py` 里，而 `decision.py` 是数据层依赖的**共享数据结构**模块
（TaskSpec / DecisionCard / Constraint / build_decision_card）。把强制排序、领先者与门禁逻辑留在同一个文件里，
正是"容易重新带回旧控制器"的来源，因此单独拆出。

- **生产调用点为零**：`CandidateLedger` 的全部引用都在 `agent/tests/`。没有 getattr / 字符串查找等间接用法。
- 拆出后 `decision.py` 不再包含也不 import 该 ledger；数据层闭包因此与它和 ranker 彻底分离。
- 保留原因：相关单测（`test_agent_architecture.py`、`test_search_budget.py`、`test_choice_settlement.py`、
  `test_unbounded_veto.py`、`test_location_prior.py`、`test_order_class_signals.py`、
  `test_trace_replay_u200109.py`、`test_avoidance_signals.py`、`test_no_self_inflicted_loops.py`）
  编码了候选观察、搜索预算与约束绑定的行为知识。
- **它不是 ADAPT 的能力**，任何"ledger 会强制排序/门禁"的说法都已失效：退役控制器之后没有任何路径阻塞写入。

# ADAPT 工程演进账本

本文档持续记录 ADAPT 在开发过程中遇到的可复现难点、根因、有效方案和验证证据。它服务于三件事：

1. 防止重复走已经证明无效的路线。
2. 将一次案例修补抽象为可跨用户、跨领域复用的 Agent 能力。
3. 为后续 trace-driven champion–challenger harness 提供结构化历史。

本文档不是 VitaBench 测试数据的副本。不得记录或向运行时暴露隐藏 rubric、reward、target/distraction 标记或目标 ID。当前反复查看过 trace 的用户均属于开发用途，其结果不能表述为无偏测试成绩。

## 状态定义

- **VERIFIED**：已有单测以及真实轨迹或评测证据，且没有已知反例推翻结论。
- **PARTIAL**：某个失败模式已改善，但尚未证明跨用户或长序列泛化。
- **OPEN**：问题已复现，尚无通过门禁的有效方案。
- **SUPERSEDED**：旧方案已被后续方案取代；保留记录以解释演进路径。

“实现了代码”不等于“方案有效”。至少需要一个针对性回归测试；涉及模型行为的方案还需要真实 smoke 或开发集证据。

## 当前可复现基线

记录日期：2026-08-23。

- 单元/集成测试：`164 passed`。
- VitaBench 边界：`git -C evaluation/vitabench diff --exit-code HEAD -- src/vita` 通过。
- B865629 前三个子任务 smoke：`1.0 / 1.0 / 1.0`，聚合 `1.0`。
- 固定五开发用户 v5 Avg@1：`0.2108333333`。
- v5 用户级结果：B865629 `0.4375`、U010122 `0.1`、U200109 `0.1333333`、U901652 `0.25`、W974351 `0.1333333`。
- v5 可观察 trace 统计：51 次 preflight rejection、39 条 tool-error lesson、38 条 unpaid-order lesson、8 条 missed-write lesson、7 条 repeat-search lesson。

权威产物：

- `data/simulations/adapt_B865629_v33_sub1_3.json`
- `data/traces/adapt_B865629_v33_sub1_3.jsonl`
- `data/simulations/adapt_fixed5_v5.json`
- `data/traces/adapt_fixed5_v5.jsonl`

这五个用户已经参与架构调试，只能作为 development evidence。当前目标 0.3 应表述为 Dev Avg@1 目标。

## 当前可复现基线（2026-09-10 更新）

记录日期：2026-09-10。权威产物 `data/simulations/stock_avg4_8u.json`。

- VitaBench 边界：`git -C evaluation/vitabench diff --exit-code HEAD -- src/vita` 通过。
- **baseline**：stock agent（`PersonalizationAgent` + `RewriteMemory`，与本项目同一 runner、同一模型、同一 seed），官方 subtask 级指标 **Avg@4 = 0.2925、Pass@4 = 0.4000、Pass^4 = 0.2000**；task 级 Pass 为 0（官方 `is_successful` 要求 reward == 1.0）。逐用户结果见产物文件。
- 官方 skill split：personalize 0.3198、proactive 0.125。
- 非功能指标：`tool_errors` 11、`incomplete_payments` 25、agent 上下文守卫裁剪 147+ 次（0 次 agent 崩溃）。
- 指标口径：一律用官方 `vita.metrics.agent_metrics.compute_metrics`（见 E-030）；`agent/trace_metrics.py` 只作开发期粗筛。
- 评测完整性：12 条 evaluation_failed 轨迹经 ADAPT 侧补丁重评全部恢复（见 E-018 之后的归一化补丁与 `agent/reevaluate_guarded.py`）。

更早的 v5 五用户证据（0.2108 等）保留在下方历史段落，仅作 development evidence。

## 评测口径：官方 56 用户（正式基准）

**正式评测范围是全部 56 个个性化用户**，由固定哈希种子 `ADAPT-2026` 切分为
dev / blind / final（`agent/vitabench_runner.py::stable_user_split`）。标准命令：

```powershell
# 56 用户官方基准：先跑 Avg@1，通过后再冻结代码跑 Avg@4
python -m agent.vitabench_runner --agent adapt --cohort all --num-trials 1 `
  --agent-llm qwen38-agent --user-llm qwen35-user --evaluator-llm qwen36-evaluator `
  --save-to data/simulations/adapt_all56_avg1.json
```

范围声明（避免误读）：

- **56 用户正式基准尚未运行**；本文件当前所有 baseline 数字均为开发子集测量，不得表述为正式基准成绩。
- ADAPT 侧对照同样来自开发子集，仅用于机制验证；正式结论以 56 用户测量为准。

ADAPT 在开发子集上的逐轮配对（1 trial，`data/simulations/ab_guard*.json`，与 baseline 同一组用户与 seed）：

| 轮次 | 修复内容 | 用户 A | 用户 B | 合计算术均值 |
| --- | --- | --- | --- | --- |
| guard1 | E-042 护栏化（重复下单 / 记忆工具 / 探索额度） | 0.154 | — | — |
| guard3 | E-043 地址取值 | 0.154 | — | — |
| guard4 | E-044 过敏提取 | 0.154 | 0.214 | 0.185 |
| guard5 | E-045 建议式领先者 + 委托逃逸口 | 0.154 | 0.143 | 0.148 |
| baseline 同单元 | stock agent | 0.308 | 0.286 | 0.296 |

**结论口径**：ADAPT 目前约为 baseline 的 50–75%（单 trial，方差约 ±1 个单元）；
`guard5` 相对 `guard4` 的下降无法与噪声区分，因此**不声称 E-045 带来增益**。

---

## E-001：修改 VitaBench 会破坏 Agent 评测边界

- **状态**：VERIFIED
- **难点**：早期为了适配 ADAPT，修改了 VitaBench 内部 prompt、工具或框架连接点，导致 Agent 改进与 benchmark 改动无法区分。
- **根因**：把集成便利性放进了被测环境，而不是放在外部 runner 和 Agent 组合层。
- **有效方案**：保存历史 diff 为 `legacy_vitabench_fork.patch`，恢复 `evaluation/vitabench/src/vita`；所有模型配置、runner、Agent、memory 和 trace 工具迁移到 ADAPT 根目录。
- **验证**：VitaBench `src/vita` 对 HEAD 的 diff 为空；外部 runner 可完成真实 smoke 和 fixed5。
- **适用边界**：历史 patch 仅作证据，不得重新应用于 benchmark run。
- **能力抽象**：benchmark integrity / external composition。

## E-002：只改 memory 不能解决偏好到行动的断层

- **状态**：VERIFIED
- **难点**：即使 memory 中包含正确偏好，stock policy 仍可能错误追问、错误搜索、选择相似但错误的候选，或没有完成 WRITE。
- **根因**：memory backend 只控制“给模型看什么”，不能控制工具状态机、候选 provenance 和写前约束。
- **有效方案**：采用外部完整 `ADAPTAgent(PersonalizationAgent)`，统一拥有 TaskSpec、Decision Card、TaskRuntime、ToolRegistry、QuestionGate、CandidateLedger 和 ADAPTMemory。
- **验证**：完整 Agent 可在未修改 VitaBench 的环境中运行；候选、主动询问和 WRITE 行为都可被独立单测及 debug sidecar 观察。
- **适用边界**：不再维护 memory-only Track A；这不是官方 Memory Arena backend 的等价比较。
- **能力抽象**：preference-to-action grounding。

## E-003：全文摘要注入造成无关偏好和长序列污染

- **状态**：VERIFIED
- **难点**：单一 `_summary_text` 会混入跨领域、跨品类和过期偏好；长序列中 prompt 越来越难以正确利用。
- **根因**：存储、召回和决策表示没有分层，所有历史内容都以同等形式进入当前任务。
- **有效方案**：引入结构化 `PreferenceFact` 和 `TaskSpec`；每个子任务只编译最多 8 条、1200 字符的 `DecisionCard`，输出 MUST / AVOID / PREFER / ASK / EVIDENCE。
- **验证**：`ADAPTMemory.read()` 重复调用稳定；全文 summary 默认不进入 runtime prompt；相关测试覆盖当前指令优先级和有界输出。
- **适用边界**：内部原始摘要可作为召回证据，但不得直接注入。
- **能力抽象**：task-conditioned memory retrieval / context control。

## E-004：按 predicate 全局漂移会删除可共存偏好

- **状态**：VERIFIED
- **难点**：出现新的忌口、品牌或品类偏好时，旧实现可能抑制同 predicate 下所有历史事实。
- **根因**：漂移槽粒度过粗，没有区分 scope、facet、dimension 和 category/entity；多值集合被错误当成单值槽。
- **有效方案**：漂移键改为 `(scope, facet, dimension, category/entity)`；忌口、过敏和禁用品牌作为可共存集合；仅同槽明确新证据 supersede 旧值，并保留证据链。
- **验证**：测试覆盖多个忌口共存、不同品类品牌互不冲突和 superseded 状态保留。
- **适用边界**：只有真正单值且同范围的维度才能自动替换。
- **能力抽象**：temporal preference updating。

## E-005：memory read 的副作用导致主动询问额度漂移

- **状态**：VERIFIED
- **难点**：重复调用 `read(query)` 会改变询问计数；一次问题可能被 suggestion、生成和 update 多次计数。
- **根因**：问题规划、问题发送和状态提交混在同一方法中，子任务重置又依赖隐式 memory update。
- **有效方案**：`read()` 和 `propose_question()` 保持纯函数；只有真正发给用户时调用 `commit_question()`；由 `ADAPTAgent.set_current_instruction()` 显式 `begin_subtask()`；回答写入结构化槽。
- **验证**：测试覆盖连续三次 read 输出一致、额度不变、一次真实问题只计一次以及回答进入正确维度。
- **适用边界**：每个子任务最多两个不同关键维度。
- **能力抽象**：proactive interaction accounting。

## E-006：模糊名称解析会把相似规格映射到错误实体

- **状态**：VERIFIED
- **难点**：模型可能把“大杯版”“奶盖版”、相邻房型或同店相似套餐当作目标，或生成未观察到的 ID。
- **根因**：全局 fuzzy resolution 绕开了当前搜索结果的实体边界，且 WRITE 前没有完整规格校验。
- **有效方案**：建立每子任务 `CandidateLedger`；只允许当前 ledger 中的精确 ID；名称归一化仅允许当前候选内唯一精确匹配；WRITE 前检查名称、规格、日期、地点、库存、禁项和 parent ID。
- **验证**：测试覆盖非候选 ID、错误商品类别、规格、日期、地址、库存和禁项拒绝；真实 smoke 使用实际候选 ID 成功创建订单。
- **适用边界**：ledger 只保存 Agent 可观察的本子任务工具结果。
- **能力抽象**：entity grounding / safe tool execution。

## E-007：模型在已有候选后仍重复搜索或重复无效调用

- **状态**：PARTIAL
- **难点**：较小 policy model 容易改写关键词反复搜索，或收到参数错误后重复相同调用。
- **根因**：prompt 建议不足以形成可执行的搜索预算和 phase 约束；完整 transcript 还会锚定模型继续搜索。
- **有效方案**：对归一化 search signature 限制最多两次；第三次由 preflight 拒绝；工具参数错误保留最近错误并要求使用实际返回 ID；候选完整后 ToolRegistry 收窄合法动作。
- **验证**：第三次相同搜索的单测通过；v5 仍记录 7 条 repeat-search lesson，说明问题已受控但未消失。
- **适用边界**：不同、确有必要的补充搜索不应被同一 signature 误杀。
- **下一步**：把 phase/action routing 变成确定性 controller，而不是依靠重新提示模型。
- **能力抽象**：bounded exploration / loop control。

## E-008：READY_TO_CREATE 阶段被旧搜索 transcript 锚定

- **状态**：VERIFIED
- **难点**：B865629 子任务 6 中，模型在已经 READY_TO_CREATE 且搜索工具不再暴露后仍输出搜索意图，没有执行 WRITE。
- **根因**：完整对话中的旧搜索调用比 system prompt 中的 phase 指令对模型具有更强的局部锚定。
- **有效方案**：进入不可逆 CREATE/PAY 阶段后使用 focused context，只保留合并后的 system prompt、Candidate Ledger、controller directive 和最近用户消息。
- **验证**：针对性 B865629 子任务 6 smoke 成功创建 `奶茶加布蕾(热)` 订单；对应 context 回归测试通过。
- **适用边界**：仅在观察完成后的 CREATE/PAY 阶段启用，不用于搜索和主动询问阶段。
- **能力抽象**：phase-conditioned context control。

## E-009：多个 system message 导致 Qwen 兼容端点 HTTP 400

- **状态**：VERIFIED
- **难点**：focused context 初版追加了第二个 system message，Qwen 的 OpenAI-compatible endpoint 返回“System message must be at the beginning”。
- **根因**：端点虽兼容 OpenAI schema，但只接受一个开头 system message。
- **有效方案**：把所有 system 内容和 runtime controller 合并为一个 `SystemMessage`。
- **验证**：HTTP 400 消失，后续 B865629 smoke 和 fixed5 均能进入 CREATE。
- **适用边界**：保持单 system message 可兼容更严格的服务端实现。
- **能力抽象**：model endpoint compatibility。

## E-010：压缩 CREATE 上下文后丢失候选排序决策

- **状态**：VERIFIED
- **难点**：fixed5 v4 的 B865629 子任务 3 在 focused context 中选了 P00081 酸菜鱼汤锅，而完整上下文曾正确选择 P00094。
- **根因**：CandidateRanker 已把 P00094 排为第一，但 controller 只说“选择最佳候选”，没有把严格领先的证据结论转化为执行约束。
- **有效方案**：增加 `unique_evidence_leader()`：只有第一候选至少匹配一个偏好、且匹配数严格高于所有 runner-up 时才锁定；并列时仍交给模型。focused directive 明确给出候选 ID、精确名称和 parent IDs，preflight 拒绝偏离唯一领先候选的 WRITE。
- **验证**：147 项测试通过；B865629 v33 子任务 3 直接创建 P00094“麻辣火锅聚餐4人套餐（含茼蒿/毛肚/鸭血/肥牛/冰汤圆）”，reward=1.0。
- **适用边界**：不能把价格、recency 或微弱 scalar score 当成强制锁定依据；证据并列时不得接管模型选择。
- **能力抽象**：preference evidence to irreversible action。

## E-011：条件偏好和“以后默认”没有进入当前决策

- **状态**：PARTIAL
- **难点**：诸如“四人以上选麻辣、人少选菌汤”“以后吃火锅都要冰汤圆”的偏好不能由普通扁平事实稳定表达。
- **根因**：缺少条件槽和未来默认信号类型，场景条件未在 TaskSpec 编译阶段解析。
- **有效方案**：SignalParser 新增 `conditional_preference` 和未来默认 `explicit_preference`；DecisionCard 根据当前人数/聚餐场景激活相应条件并抑制冲突口味。
- **验证**：单测覆盖四人/小团体条件解析和“以后加冰汤圆”；B865629 子任务 3 的 Decision Card 出现麻辣、茼蒿和冰汤圆并选中同时满足三项的候选。
- **适用边界**：当前场景人数的语言归一化仍较启发式，需要跨用户 blind 证据。
- **能力抽象**：conditional personalization。

## E-012：增量 memory update 后 Decision Card 仍是旧快照

- **状态**：VERIFIED
- **难点**：同一子任务开始前处理新增 interactions 后，Agent 继续使用在 update 前编译的 Decision Card。
- **根因**：stock `process_interactions()` 更新 memory，但 ADAPT 的任务决策快照没有同步刷新。
- **有效方案**：ADAPTAgent 覆写 `process_interactions()`，在增量更新完成后重新编译当前 TaskSpec 的 Decision Card。
- **验证**：回归测试证明 update 后偏好立即刷新；真实 B865629 后续子任务能看到新增的麻辣与冰汤圆证据。
- **适用边界**：刷新只使用当前用户可见 interactions，不读取 evaluator 数据。
- **能力抽象**：memory-to-policy synchronization。

## E-013：非激活历史事实触发错误主动询问

- **状态**：VERIFIED
- **难点**：条件解析已为当前聚餐场景选择麻辣，但主动询问逻辑仍扫描内部完整事实，看到未激活的菌汤后错误询问“麻辣还是菌汤”。
- **根因**：Question Gate 的语义缺口检测绕开了有界、已排序的 Decision Card render 结果。
- **有效方案**：口味冲突检测只扫描真正渲染给模型的 Decision Card；被条件解析抑制的低优先级事实不再参与当前问题规划。
- **验证**：相关主动询问测试通过；B865629 v33 子任务 3 不再错误询问口味，直接搜索并创建正确候选，reward=1.0。
- **适用边界**：如果渲染后的 card 仍存在两个真实冲突族，仍应询问。
- **能力抽象**：missing-information recognition precision。

## E-014：规格词会被误当作商品类别证据

- **状态**：VERIFIED
- **难点**：例如“低咖啡因”中的“咖啡”可能让茶或可可通过“咖啡”商品类别硬约束。
- **根因**：类别判断扫描整条 serialized row，没有区分 product name/tags 与 attributes/specification。
- **有效方案**：类别证据只从商品基础名称和显式 tags 提取；括号内规格、咖啡因等级等属性不能证明实体类别。
- **验证**：错误类别和咖啡因回归测试通过。
- **适用边界**：类别词典需要按通用实体类型扩展，禁止针对具体候选 ID 添加规则。
- **能力抽象**：typed semantic validation。

## E-015：固定五用户仍存在明显长序列性能退化

- **状态**：OPEN
- **难点**：fixed5 v5 Avg@1 仅 0.2108。B865629 前 6 个子任务成功，随后第 7–13 个连续失败，第 14 个短暂成功，最终 0.4375 且 termination=`max_steps`。其他四用户均不超过 0.25。
- **证据**：`data/simulations/adapt_fixed5_v5.json`。
- **已排除**：不是 VitaBench 源码污染；不是 P00094 排名回归；单测与前三子任务 smoke 均通过。
- **当前假设**：长序列中 runtime phase/action 决策、地址/profile 解析、问题门禁和未支付状态处理仍会累积错误；单用户 lesson 数量高但没有稳定转化为策略改善。
- **下一步**：按用户和 temporal index 生成只含可观察信息的 failure clusters，优先修复跨用户高频不变量，并比较前后期 reward gap。
- **禁止捷径**：不得为上述 user ID、subtask ID 或具体候选编写特例。
- **能力抽象**：long-horizon consistency。

## E-016：已授权任务仍错误追问，phase/action routing 不稳定

- **状态**：OPEN
- **难点**：v5 的 51 次 preflight rejection 中，23 次为“direct execution request 已授权选择，但 Agent 仍追问候选”；另有 7 次在 SEARCH phase 错误提问、5 次把问题和工具调用混在同一消息。
- **证据**：`data/traces/adapt_fixed5_v5.jsonl` 的 `preflight_rejected` 事件聚类。
- **根因假设**：目前 QuestionGate 主要在模型生成后否决非法动作，缺少生成前、基于 phase 和信息完备度的确定性 next-action controller。
- **候选方案**：引入 `ActionIntent` / `NextActionController`，在生成前计算 ASK、SEARCH、EXPAND、CREATE、PAY、DONE 中唯一合法动作，并只暴露对应工具或框架问题。
- **有效性要求**：必须降低上述三类 rejection，同时不得压低真正 proactive task 的必要询问召回；需通过针对性测试、1–2 用户 smoke 和固定开发集 paired run。
- **能力抽象**：proactiveness calibration / action routing。

## E-017：把运行时自进化误实现为外部 Coding Agent 改源码

- **日期**：2026-08-23
- **状态**：SUPERSEDED
- **难点**：“自进化 harness”存在两种不同含义：开发期自动修改 Agent 源码，和冻结代码下 Agent 在运行时更新自身程序性策略。
- **尝试过但无效的方案**：构建外部 trace miner、隔离工作区和 coding-agent patcher，根据开发 trace 自动生成并测试源码修改。
- **无效原因**：该方案依赖开发者侧 Coding Agent；被测 ADAPT 本身没有学习。若在 benchmark 用户之间持续改代码，还会让不同用户面对不同版本，破坏固定 Agent 的评测含义。
- **纠正方案**：撤回外部源码变异 harness；将目标限定为 ADAPT 进程内部、冻结源码下的程序性记忆和可执行策略适配。
- **验证**：外部 patcher 已停止，未晋级任何 challenger；新增的外部 evolution 源码、配置和测试已撤回；VitaBench `src/vita` 保持只读。
- **适用边界**：外部 coding harness 仍可作为离线研发工具，但不能声称是被测 Agent 的运行时自进化能力。
- **能力抽象**：evaluation validity / runtime adaptation boundary。

## E-018：运行时 lesson 目前仍是提示词提示，不等于完整自进化

- **日期**：2026-08-23
- **状态**：PARTIAL
- **难点**：现有 `ExecutionLessonStore` 能从用户纠正、工具错误和轨迹异常生成 lesson，但主要作为文本提示注入，不能稳定改变下一步动作；fixed5 v5 中 lesson 数量增长后仍有长序列退化。
- **根因假设**：缺少从可观察事件到 capability-level 规则、再到确定性 action policy 的闭环；模型可以忽略 lesson。
- **候选方案**：在 ADAPTAgent 内增加 `TrajectoryObserver -> CapabilityLearner -> RuntimePolicyStore -> PolicyAdapter`。学习结果只允许更新有类型、可验证、可撤销的动作规则，不修改源码或模型权重。
- **已实现方案**：新增 `agent/runtime/evolution.py`，使用封闭 failure vocabulary 将可见失败编译为 capability-level `RuntimePolicyRule`；规则包含 failure cluster、proposed change、evidence count、confidence、下一子任务激活点和 forbidden specificity。`RuntimePolicyAdapter` 可执行地调整搜索预算、候选到 CREATE 的迁移、支付完成检查和冗余提问门禁。`ExecutionLessonStore` 仅保留为人类可读证据层。
- **状态边界**：每个 benchmark 用户开始时清空；只允许同一用户的后续子任务复用。不得读取 reward、rubric、target/distraction 标记，也不得包含 user/product/task ID。
- **当前验证**：新增测试覆盖下一子任务激活、证据阈值、facet 隔离、切换用户清空、拒绝 evaluator payload、未知 ID-like scope 归一化和确定性 action/question 改变。`adapt_runtime_evolution_smoke1_v2.json` 在 B865629 前三子任务 reward=1.0，记录 3 次 policy apply、0 次 preflight rejection，三个不同 facet 没有发生策略串扰。该次 sidecar 的两次 `unpaid_order` learn 暴露了 E-019 假阳性，已从有效证据中排除。
- **验证要求**：当前 smoke 证明接线和隔离，不证明收益来自自进化，因为三个子任务 facet 不同、学到的规则没有在相同 facet 后续任务激活。仍需同 facet replay 或 paired dev run 证明 capability 指标改善后才能标为 VERIFIED。
- **同 facet paired 结果**：U200109 子任务 4→5 使用相同 seed/model/max_steps，对照 `--no-lessons` 与 runtime evolution 均为 reward=0.0、termination=`max_steps`。自进化版在第 1 个子任务从 4 次候选重问拒绝中形成 `missing_information_detection` 规则，并在第 2 个同为 `delivery/retail` 的子任务激活，证明学习→下一同 facet 应用链路成立；但该规则没有改善主导失败，见 E-020。因此仍为 PARTIAL，不能声称指标收益。
- **能力抽象**：online procedural learning / long-horizon consistency。

## E-019：已询问支付但用户停止，被误判为 Agent 未完成支付

- **日期**：2026-08-23
- **状态**：PARTIAL
- **难点**：运行时 smoke 中 Agent 创建未支付订单后正确询问支付授权，用户模拟器返回 STOP；子任务收尾却生成 `unpaid_order` lesson。
- **证据**：`data/traces/adapt_runtime_evolution_smoke1_v2.jsonl` 出现两次 `unpaid_order` policy learn，同时对话中均已有 `payment_question` 事件。
- **根因**：旧收尾逻辑只检查 `READY_TO_PAY + pending_payment_ids`，没有区分“Agent 未继续”与“Agent 已询问、用户未授权”。
- **有效方案**：新增 `TaskRuntime.has_unresolved_payment_failure()`：已授权但未 PAY、或未授权且从未询问才算 Agent 失败；已询问一次或用户拒绝不产生 lesson。
- **验证**：单测覆盖“支付确认后用户停止不学习”和“已授权却未支付应学习”。尚未重新运行真实长序列 smoke，因此暂标 PARTIAL。
- **适用边界**：支付仍是独立授权边界；自进化不得把用户沉默解释为支付授权。
- **能力抽象**：observable failure attribution / safe online learning。

## E-020：工具已明确返回地址错误，Agent 仍重复相同 WRITE 参数

- **日期**：2026-08-23
- **状态**：PARTIAL
- **难点**：同 facet paired replay 中，CREATE 使用账户完整地址后收到 `Longitude and latitude not found`，随后继续用完全相同地址重复 CREATE 或地址解析，直至 max_steps。
- **证据**：`adapt_runtime_evolution_pair_off.jsonl` 的后续子任务出现 8 次 tool error；`adapt_runtime_evolution_pair_on.jsonl` 出现 7 次。两组聚合 reward 都是 0.0。期间缩短后的地址输入曾成功返回坐标，但未成为后续 WRITE 的参数恢复证据。
- **根因**：当前 `tool_error` 只写入文本 lesson，`RuntimePolicyStore` 没有对应 capability rule；TaskRuntime 也没有错误调用签名账本、参数级失败槽和成功 fallback 参数账本。模型能看到错误，但仍可重复相同不可行调用。
- **尝试过但不充分的方案**：第一版只增加失败 WRITE 上限和“复用已成功短地址”。真实 smoke 将错误限制到 2 次并避免 `max_steps`，但模型没有主动探索出成功替代表示，最终没有 CREATE，reward 仍为 0。说明“等待模型发现 fallback”仍不是执行闭环。
- **已实现方案**：新增并接入通用 `ToolErrorLedger`，记录 `(tool role, normalized arguments, error class)`、实际调用 ID、参数级失败和本子任务成功参数。同一失败 WRITE 签名最多两次；CREATE 地址不可解析后，框架只对显式楼栋/单元/房号后缀生成一次 READ-only 解析探针。只有环境成功解析的、原地址内的前缀才可成为恢复证据。得到证据后，框架重放最近一次失败 CREATE，仅替换 address，冻结候选 ID、商家、数量、时间和备注；切换子任务立即清空全部证据。
- **验证**：新增测试覆盖第三次相同失败 WRITE 被阻断、无成功证据不恢复、探针单次且只读、reset 后不复用、preflight 与档案地址校验兼容，以及恢复重放冻结非失败参数。全量 `164 passed`，VitaBench `src/vita` diff 为空。`adapt_tool_recovery_smoke_v2.jsonl` 记录了 `tool_parameter_recovered`；进一步的 `adapt_tool_recovery_smoke_v3.jsonl` 先观察地址失败，再记录 `tool_parameter_probe`、成功 resolver 和 `recovered_write_replayed`，随后原 CREATE 的非地址参数保持不变、订单创建成功并进入支付确认。同一子任务 tool error 从旧 trace 的 7 次降为 3 次（约 57%），termination 从 `max_steps` 变为 `user_stop`。
- **剩余边界**：该 smoke reward 仍为 0，说明地址执行闭环的改善尚未转化为完整任务指标；当前只能证明 E-020 的循环和参数漂移改善，不能标为 VERIFIED。候选偏好利用问题见 E-021。
- **适用边界**：地址缩短只能作为环境可解析的等价表示，不得推断新的城市或收货地点；跨用户和跨地址禁止复用具体值。
- **能力抽象**：candidate-to-action execution / tool-error recovery。

## E-021：复合软偏好没有稳定落到最终候选

- **日期**：2026-08-23
- **状态**：PARTIAL
- **难点**：地址恢复 smoke 中 Decision Card 已包含角色、Q 版和挂件等可观察偏好证据，搜索结果也包含分别满足这些属性的候选，但初次 CREATE 选择主要受“惊喜/随机款”措辞驱动，没有稳定最大化持久偏好证据；地址恢复完成并成功创建订单后 evaluator reward 仍为 0。
- **证据**：`data/simulations/adapt_tool_recovery_smoke_v3.json` 和 `data/traces/adapt_tool_recovery_smoke_v3.jsonl`。只使用可见 Decision Card、候选名称/标签和工具结果，不读取隐藏 rubric 或 target 标记。
- **根因**：当前 `unique_evidence_leader()` 只在单个候选严格领先时锁定；复合偏好被保存在一个长文本 value 中。更重要的是，在候选返回之前用固定 facet 筛偏好，会让被错分 facet 但能被当前候选明确证明的偏好永久丢失。
- **已实现方案**：新增候选诱导的开放世界 `PreferenceAtom -> CandidateAttributeMap -> EvidenceAlignment`。属性键和值只从本子任务工具返回的 name/dynamic fields/tags 中生成，不枚举品牌、口味、材质、连接方式等领域维度。Decision Card 增加不直接渲染的当前指令源和本用户 active positive fact pool；搜索后只有能落到当前候选属性的原子才进入排序、shortlist 证据和 WRITE 前覆盖检查。相同属性原子跨多条记忆去重，避免重复文本虚增覆盖数。
- **开放世界类别边界**：若某 category 在候选集中至少有一个可观察字面证据，仍执行严格过滤；若它对整批候选都不可落地，则不因上位词/具体类词面不同清空整个 shortlist。该规则基于证据集可落地性，不包含“衣服→卫衣”等领域映射。
- **当前验证**：新增未见属性键、复合原子覆盖、跨候选集隔离、错 facet 记忆在候选证据下恢复、当前指令动态属性、上位类不清空候选，以及原子去重/可观察事实置信度继承等测试；全量 `174 passed`，VitaBench `src/vita` diff 为空。
- **smoke 证据**：修改前的 `adapt_open_world_smoke_Q089190` 两个子任务均 reward=0。第一段因“衣服”与“卫衣/外套”词面不同没有暴露 CREATE；第二段 memory 已含“青岛啤酒经典”，但旧 Decision Card 因 facet 预过滤只保留地址和授权，最终创建了雪花候选。该 smoke 是修复动机，不是修复后收益证据。
- **修复后配对 smoke**：`adapt_open_world_smoke_Q089190_postfix` 已出现 `preference_alignment` 事件和动态键 `tag/品牌/类型/规格`，证明错 facet 事实池已穿透到候选证据层；但该版本仍将池内事实降成等权字符串，`atom_count=41`、shortlist 前 8 项 `best_coverage=4`全并列，最终 reward 仍为 0。因此追加“只接受候选可落地 pool atom + 继承 PreferenceFact confidence + 相同属性跨文本去重”；追加修复后仅完成单测，未再消耗一次模型 smoke。
- **后续风险/下一步**：精确候选词汇只能覆盖字面落地；“秋天”与“秋季”、“不会太冷”与“保暖”这类语义关系仍依赖 policy model。需在另一个开发用户上做修复后 smoke，确认 trace 出现动态 alignment 且 WRITE 使用最大可观察偏好覆盖集，才可升级为 VERIFIED。
- **能力抽象**：preference-to-candidate grounding / preference utilization。

## E-022：弱交互偏好在进入开放世界 grounding 前被丢弃

- **日期**：2026-08-23
- **状态**：PARTIAL
- **难点**：U901652 的体考运动鞋子任务中，当前指令能落地到“运动鞋/体考”，但用户在可观察历史中对某游戏联名的高频浏览、搜索、收藏商品和对话兴趣没有进入 PreferenceFact pool。候选中虽有多个可与该兴趣落地的属性，框架无法使用。
- **证据**：`data/traces/adapt_open_world_smoke_U901652.jsonl` 中 `preference_alignment` 仅有 3 个 atom，dynamic keys 为 `store_name/tag`，shortlist 前 8 项均为 `best_score=6.8`；对话完成一次尺码询问、一次搜索和成功 CREATE，无 preflight rejection、tool error 或重复搜索，但 reward=0。
- **根因**：`SignalParser` 将 `high_freq_browse` 降为 `raw_observation`，而 `ADAPTMemory.update()` 明确不将 raw/search 投影到 FactStore；`favorite` 共用 cart extractor，但 extractor 不识别 `target_name`，导致整个 JSON 成为 value，随后因长度超过 `_valid_fact_value` 限制被排除。因此问题发生在 preference extraction，不是候选 alignment 或模型参数。
- **已实现方案**：将 `favorite/add_to_cart/high_freq_browse/search` 统一为可观察兴趣证据管道；按 `target_name/item_name/product_name/keyword/query` 的通用字段优先级归一化。PreferenceFact 保留 `evidence_types` 和 `decision_eligible`：收藏/加购可作强证据，单次搜索和高频浏览只辅助排序，不单独锁定 WRITE；两种不同弱证据在同一候选 atom 上一致时才升级为 decisive。弱证据不直接渲染为 Decision Card `PREFER`。
- **误匹配防护**：商家/服务名称按原子身份处理，不再拆出“某城店”等公共地名后缀并虚假提高多个候选分数。
- **验证**：实际开发用户 interactions 的无 evaluator 集成检查已恢复 4 条相关事实：两条 search 为弱证据、一条 high-frequency browse 为弱证据、一条 favorite 为强证据，并全部进入 task-local preference pool。新增测试覆盖字段归一化、单弱证据只排序不锁定、favorite 锁定、多源弱证据升级和商家名后缀误匹配防护；全量 `177 passed`，VitaBench `src/vita` diff 为空。
- **同案例配对 smoke**：在 U901652 相同子任务、agent/user/evaluator 模型、seed=42 和 max_steps=12 不变时，修复前 `adapt_open_world_smoke_U901652` reward=0.0，修复后 `adapt_interest_smoke_U901652_postfix` reward=1.0。Decision Card 新增由 favorite 支撑的可观察兴趣；搜索词自动结合任务类别与兴趣属性。`preference_alignment` 从 atom_count=3、best_score=6.8、best_candidate_count=8 变为 atom_count=4、best_score=8.6、best_decisive_score=8.6、best_candidate_count=1；最终 CREATE 从普通体考鞋切换为同时满足任务硬约束和可观察兴趣的候选。
- **剩余问题/下一步**：修复后 trace 仍有 1 次 `candidate_choice_reask` preflight rejection，框架成功重规划且未影响 reward，但说明生成前 action routing 尚未完全代替生成后否决。当前仅为单个开发 bad case 配对改善，状态保持 PARTIAL；需在另一个用户/领域复现兴趣证据打破候选并列，才能宣称泛化。
- **跨用户/跨领域 smoke**：`Q089190/sub_Q089190_13`（instore 服务）使用相同 seed/model/max_steps 运行后，开放世界 alignment 从该用户此前可见的采耳下单和评价行为生成 6 个可落地 atom，在 129 个搜索结果中得到唯一最高分候选（`best_coverage=3`、`best_score=2.05`、`best_decisive_score=2.05`、`best_candidate_count=1`）；最终 CREATE 选择“经典采耳套餐（30分钟）”，而不是搜索结果首位的通用按摩套餐。该证据表明兴趣 grounding 的机制能跨用户和 delivery→instore 迁移，但子任务因创建后未支付得到 reward=0，不能作为端到端泛化成功，且仍不足以将本条升级为 VERIFIED。产物：`data/simulations/adapt_interest_generalization_Q089190.json`、`data/traces/adapt_interest_generalization_Q089190.jsonl`。
- **能力抽象**：preference extraction / preference-to-candidate grounding。

## E-023：提交型自然语言与支付授权边界不一致

- **日期**：2026-08-23
- **状态**：OPEN
- **难点**：用户说“给我团个券”时，TaskSpec 将其识别为 `action=commit`，框架成功搜索并创建团购订单；订单返回 unpaid 后，框架仍固定询问“需要我现在支付吗？”，用户模拟器随即停止，最终 reward=0。
- **证据**：`data/traces/adapt_interest_generalization_Q089190.jsonl` 中 phase 依次为 `search -> wait_create_result -> ready_to_pay`，随后出现 `payment_question`；`data/simulations/adapt_interest_generalization_Q089190.json` 的该子任务 reward=0。分析只使用可见指令、工具状态和聚合 reward，未读取 rubric 或 target 标记。
- **根因**：当前授权模型只有通用的 `commit -> create_authorized`，并将所有 PAY 视为必须二次确认的独立不可逆边界；它没有表达“某些明确完成式购买动词是否同时授权支付”的类型化语义。因而这不是模型漏调工具，而是状态机主动不暴露 PAY 工具。
- **风险**：直接令所有 commit 自动授权支付会扩大不可逆操作权限，并可能损害需要二次确认的订单、预约和高金额场景；不能用单个 benchmark case 改写全局安全规则。
- **候选方案**：引入 typed authorization scope，区分 `select/create/pay`，只从明确的完成式交易表达和领域动作语义推导 scope；同时保留金额、修改、预约等风险门禁。先用可见语句构造正反例单测，再做多个领域的 paired smoke，确认收益和误支付率。
- **验证**：本轮只完成诊断和记录，未改授权策略。
- **能力抽象**：candidate-to-action execution / authorization semantics / proactiveness calibration。

## E-024：统一日期参数校验误伤候选绑定日期的酒店 CREATE

- **日期**：2026-08-23
- **状态**：PARTIAL
- **难点**：OTA 酒店 smoke 已搜索到 11 月 13 日有库存的房型，但 WRITE preflight 连续三次报告 `date argument does not satisfy required value: 11月13日`，最终错误声明没有合规候选且未创建订单。
- **证据**：`data/traces/adapt_interest_generalization_E057330_ota.jsonl` 的 seq 20/22/24；`data/simulations/adapt_interest_generalization_E057330_ota.json` reward=0。候选详情明确返回 `HotelProduct(... date=2027-11-13 ...)`。
- **根因**：TaskSpec 将自然语言日期统一编译为 `ConstraintTarget.ARGUMENT`。但未修改的 VitaBench `create_hotel_order` schema 只有 `hotel_id/room_id/user_id`，入住日期绑定在已观察的 `room_id` 候选上；`_validate_argument_constraint()` 因 WRITE arguments 不含 date 必然失败。这不是日期格式归一化问题。
- **已实现方案**：日期可以由 WRITE 参数直接满足，也可以由所选候选中的显式 `date=...` 字段满足。酒店 CREATE 校验 room candidate 的 date；航班、火车和景点在存在实际 date 参数时，同时校验参数与所选 ticket/seat candidate 的日期，避免只放宽缺失参数而放过错误日期。
- **风险**：不能简单跳过缺失 date 参数，否则会放过选错日期的 room/ticket/seat；必须保留候选来源和精确日期证据。
- **验证**：新增酒店候选绑定正确日期、错误房型日期拒绝、显式航班日期与 seat candidate 冲突拒绝测试；全量 `180 passed`，compileall 通过，VitaBench `src/vita` diff 为空。同 seed/model 的修复后 smoke `adapt_interest_generalization_E057330_ota_datefix` 不再出现任何 date preflight rejection，成功创建 `2027-11-13` 酒店订单并进入 `ready_to_pay`。reward 仍为 0，原因已分离为 E-025 偏好未落地和 E-023 支付授权，因此本条暂标 PARTIAL，等待另一个 OTA 类型回归后再升级。
- **能力抽象**：candidate-to-action execution / typed constraint binding。

## E-025：结构化 OTA 历史中的场景字段未进入 facet 推断

- **日期**：2026-08-23
- **状态**：PARTIAL
- **难点**：同一 OTA smoke 的可见历史含酒店订单、双床房和近地铁标签；当前酒店候选也含“近地铁口”，但每次 `preference_alignment` 均为 `atom_count=0`，无法利用已存在的通用酒店偏好。
- **证据**：`data/traces/adapt_interest_generalization_E057330_ota.jsonl` seq 6/9/11/13/15/17/19。零成本投影检查确认 `SignalParser` 已生成 `双床房4晚（10.1-10.5）` 和 `近地铁`，且它们在 Decision Card 的 task-local pool 中；但对应 PreferenceFact 被标为 `scope=general/facet=general`。
- **已确认的贡献因素**：`fact_from_signal()` 主要靠中文自由文本 marker 推断 facet，没有读取订单中可观察的结构化 `scenario=hotel`。酒店 marker 还包含“大床房”却遗漏“双床房”；当商家名称不含“酒店”时，即使 JSON 明确声明 hotel 且商品为双床房，也会落入 general/general。开放世界 pool 理论上允许错 facet 事实在候选返回后重新落地，因此该误分类本身尚不足以完全解释真实 trace 的 `atom_count=0`；修复时还需单独回放“近地铁”与候选 tag“近地铁口”的 atomization 链路，不能把两个现象草率归为一个根因。
- **已实现方案**：优先解析可观察的结构化 `scenario`，文本 marker 只作 fallback；直接 OTA scenario 映射到对应 facet，`travel_ticket` 再由可观察交通证据细分 flight/train，delivery/instore 继承结构化 scope。候选 atomization 增加同一候选属性键内的单向具体化匹配，使较抽象的历史值可以匹配当前候选的更具体可观察值，而不引入领域同义词表。
- **验证**：通用最小复现使用虚构商家、商品和 ID，证明未知房型名称仍由 `scenario=hotel` 得到 `scope=ota/facet=hotel`，`travel_ticket + 飞机` 得到 flight，且“近地铁”可对齐候选 tag“近地铁口”；既有跨候选集不转移测试保持通过。全量 `183 passed`，compileall 通过，VitaBench `src/vita` diff 为空。
- **修复 E-024 后的隔离证据**：`adapt_interest_generalization_E057330_ota_datefix.jsonl` 已成功 CREATE，但 alignment 仍在全部父候选和房型候选阶段保持 `atom_count=0`，最终选择大床房；说明本问题没有被日期修复掩盖，仍需独立处理。
- **修复后 smoke**：`adapt_interest_generalization_E057330_ota_scopefix.jsonl` 中 Decision Card 恢复“双床房/近地铁”等酒店偏好，alignment 从全程 `atom_count=0` 变为 `atom_count=3`、dynamic key=`tag`、`best_score=2.0`；日期校验继续通过并成功 CREATE。最终仍选大床房且 reward=0，新的主导原因是父候选并列时固定展开的 6 家没有暴露双床子候选，见 E-026；支付停止仍见 E-023。因此本条保持 PARTIAL，不把单案例结果升级为完整泛化成功。

## E-026：偏好只在子候选可见时，固定父候选展开预算可能提前停止

- **日期**：2026-08-23
- **状态**：OPEN
- **通用性判定**：`UNRESOLVED`。当前现象符合通用 hierarchical search failure，但尚未用无数据集实体的酒店/航班/景点父子候选最小复现证明，禁止直接修改展开数量或为双床房增加搜索规则。
- **难点**：父候选只暴露酒店级 tags，房型偏好只在详情工具返回的子候选上可见。当父层偏好证据并列时，框架固定展开前 6 个父候选；若相关子候选只在未展开父项中出现，Agent 会把“尚未观察到”误当成“不可满足”。
- **证据**：`data/traces/adapt_interest_generalization_E057330_ota_scopefix.jsonl` 中父层 `best_candidate_count=8`，随后 `hierarchical_enrichment count=6`；展开后房型候选的最大覆盖仍只有酒店 tag，最终 CREATE 大床房。分析未使用隐藏 target 或 rubric。
- **下一步**：先构造通用父子候选测试，比较“固定 top-k 展开”和“围绕尚未落地的 decisive preference 继续有界展开”。只有在至少两个层级工具形态或一个明确 schema 不变量上成立，才实现自适应展开；预算仍需有硬上限。
- **能力抽象**：preference-to-candidate grounding / hierarchical exploration。
- **能力抽象**：preference extraction / preference scoping / preference-to-candidate grounding。

## E-027：长期记忆容量、召回和主动询问没有共享同一任务槽模型

- **日期**：2026-08-23
- **状态**：OPEN
- **通用性判定**：`GENERAL-EMPIRICAL`。结论来自固定 8 用户开发集的聚合可观察轨迹，并有不含数据集实体的已知房型合成测试；未读取逐案例隐藏字段，也不据此生成用户、商品或商家规则。
- **难点**：FactStore 能保存大量历史事实，但全局事实池、任务召回和 ProactiveEngine 各自使用不同的相关性/槽位口径。结果是长期序列中事实容量接近饱和，当前任务的相关事实可能在 64 条开放世界池之前被截断，同时询问策略仍可能对已有稳定证据的语义槽重复提问。
- **证据**：零模型审计产物 `data/analysis/memory_audit_dev.json` 与 `docs/MEMORY_AUDIT_DEV.md`。8 用户、112 子任务中，7 个用户到达 500 条 FactStore 上限，6 个用户到达 500 条 stream 上限；109/112 个子任务发生 preference pool 截断；结构化可观察召回为 6088/7958（0.765）；43 次通用问题建议中 28 次检测到同槽强证据（0.6512）。最终 active facts 的主体是 product=1942、brand=1342，而真正的 room_type=5、transport=23、safety=1。结构化 scenario scope 检查 3519 次、错配 0 次，说明 E-025 修复有效，当前主导问题已转为容量和任务条件化，而非 scenario scope。
- **审计边界**：审计只读取 `task.id/subtask_id/domain/instruction/interactions`，模型和 evaluator 调用均为 0，不读取 `evaluation_criteria/user_intention/skill_tested/reward/target_product_ids/target-distraction`，只输出聚合结果。召回率是结构性可见性指标，不是 benchmark reward；“同槽冲突”也只说明应先解析已有证据，不代表所有确认问题都错误。
- **根因**：`build_decision_card()` 先对全部 active positive facts 按 confidence/time 全局排序，再截断到 64；高频 product/brand 事实因此占据大部分容量。`ProactiveEngine` 读取格式化文本并使用独立关键词检测，不消费 TaskSpec 与 PreferenceFact 的已解析槽状态。FactStore 的单值维度集合与 DriftDetector 的 predicate 白名单也不一致，导致 31 个单值槽仍有多个 active 值，最终仅 1 条事实被 superseded。
- **已实现方案**：新增 `agent.memory_audit`，可用固定 hash cohort 重放可观察历史，聚合检查 scope、任务相关召回、pool 截断、单值槽冲突、近重复事实、问题维度与已知槽冲突。新增共享 typed-slot resolver，由 Decision Card、ProactiveEngine 和 TaskRuntime 使用同一强证据状态；只有 task-relevant、decision-eligible 且语义值唯一的偏好选择槽可以从历史解析，弱搜索/浏览和多值冲突不能抑制询问。日期、路线、数量、地址和预约时间等任务实例槽禁止由历史自动补齐；当前会话答案不会被历史覆盖。
- **聚合验证**：对照产物 `data/analysis/memory_audit_dev_comparison.json`。同一 8 用户、112 子任务中，建议问题 43→37，已有稳定同槽证据的重复问题 28→0，结构召回保持 0.765；当前完整结果见 `data/analysis/memory_audit_dev.json`。虚构酒店正反例覆盖强房型事实直接解 gap、弱浏览不解 gap、双床/大床冲突仍询问；全量 `188 passed`，compileall 通过，VitaBench `src/vita` diff 为空。
- **尝试过但已撤回的方案**：曾将 64 条 pool 改为“48 task-relevant + 16 fallback”和“56 + 8”。结构召回分别降至 0.7349 和 0.7315，原因是当前词面 TaskSpec domain/facet 与可见 subtask domain 仍可能错位，提前分层会挤掉分类错误但可在候选阶段落地的开放世界事实。两版 runtime 改动和对应强制测试均已撤回，不以单 facet 改善掩盖整体回退。
- **后续处理**：固定 pool 分层保持撤回；候选返回后的二阶段 memory retrieval 已由 E-028 实现。product/brand 历史仍需按实体证据合并并设置分层容量；drift 仍需直接使用 `(scope, facet, dimension, category)`，仅由同槽明确新证据确认 supersede。
- **反例门禁**：弱浏览/单次搜索不能阻止必要询问；已有偏好与当前明确指令冲突时仍以当前指令为准；不同 facet 的 room type/transport/brand 不能互相覆盖；负向安全集合不能因容量压缩丢失；开放世界未知字段仍必须能在候选 schema 出现后参与 grounding。
- **能力抽象**：preference updating / missing-information detection / long-horizon consistency / preference-to-candidate grounding。

## E-028：搜索前固定 memory pool 无法利用候选刚暴露的开放世界字段

- **日期**：2026-08-23
- **状态**：PARTIAL
- **通用性判定**：`GENERAL-INVARIANT`。检索仅由当前工具返回候选的可观察 name、dynamic field 和 tag 诱导词汇，不依赖 domain 词典、用户/任务/候选 ID、rubric、reward 或 target 标记。
- **难点**：FactStore 最多有 500 条事实，而搜索前 Decision Card 只能携带 64 条。若相关事实因置信度/时间或错误 facet 没进入初始 pool，即使搜索结果明确暴露了可匹配字段，CandidateRanker 也永远看不到该事实。提前按 TaskSpec 分层又会受词面 domain/facet 错分影响。
- **已实现方案**：新增 candidate-induced second-stage retrieval。每次 SEARCH/READ 返回后，从当前 CandidateLedger 的非技术字段构造本候选集局部词汇，回查完整 active PreferenceFact；正向事实按可落地字段数、匹配具体度、置信度和时间排序，最多 64 条并替换 pre-search pool；负向事实若直接落到候选字段，则补充为 candidate exclusion。随后在同一轮重新计算 alignment、shortlist 和 execution readiness。单次弱证据保持非 decisive，同一值的多种弱证据继续合并 evidence types 后才升级。
- **正反例验证**：虚构测试用 100 条高置信 OTA 历史挤掉一个低置信且错 facet 的零售事实；候选返回精确 name 后该事实被恢复、全部 OTA 噪声被排除，并产生唯一 evidence leader。另一测试证明错 facet 的“近地铁”可匹配更具体 tag“近地铁口”，候选上的负向“临街嘈杂”会被过滤；第三个测试证明 search + high_freq_browse 才能合并为 decisive。所有实体和 ID 均为虚构。
- **性能边界**：合成上限检查使用 500 facts、128 ledger candidates，二阶段检索耗时约 14.06ms，返回 64 个 bounded positive facts；不产生模型调用。
- **真实 smoke**：`data/traces/adapt_candidate_retrieval_Q089190.jsonl` 记录一次 `candidate_memory_retrieval`：137 candidates 中仅 9 个正向历史值落地；随后 alignment 为 6 atoms、dynamic keys=`name/tag`、best coverage=3、best score=4.6、唯一最高候选。Agent 一次搜索后 CREATE 经典采耳套餐，无重复搜索、问题或 preflight rejection。`data/simulations/adapt_candidate_retrieval_Q089190.json` reward 仍为 0，轨迹明确停在 CREATE 后的支付授权询问，因此该结果只证明 retrieval/grounding 接线和有界筛选，不声称端到端收益。
- **验证**：全量 `191 passed`，compileall 通过，VitaBench `src/vita` diff 为空。
- **适用边界**：只有候选实际暴露的字段才能恢复事实；语义同义但无词面包含关系仍交给模型，不能伪造词典。已被 FactStore 500 条上限提前 prune 的事实无法由本层恢复。父候选不暴露子候选属性时仍属于 E-026 层级探索问题。
- **下一步**：在另一工具形态上做真实 smoke，确认 candidate retrieval 不会因通用短属性造成错误唯一 leader；随后再处理 product/brand 证据压缩，降低事实进入 500 条上限前的污染。
- **能力抽象**：preference-to-candidate grounding / long-horizon consistency / candidate-to-action execution。

## E-029：混合 FactStore 的 500 条容量被实体历史占满

- **日期**：2026-08-23
- **状态**：PARTIAL
- **通用性判定**：`GENERAL-EMPIRICAL`。固定 8 用户开发集只做零模型反事实重放；策略按事实字段角色、证据类型和动态 `(scope, facet, dimension, category)` 桶定义，不包含用户、商品、品牌或商家特例。
- **难点**：当前 PreferenceFact、product/brand entity evidence 和弱行为证据共用同一个 500 条 FactStore。开发集中 7/8 用户到达上限，当前 prune 虽保住了已知 protected facts，但丢失了部分之后本可由候选字段恢复的实体证据。
- **审计方法**：新增 `agent.compaction_audit`，从同一份未截断可观察事实宇宙比较三种策略：当前混合 500、保护 PreferenceStore + 精确规范化 EntityEvidenceIndex、以及在此基础上对实体索引按动态 facet/category 桶 round-robin 公平保留。结构化历史中的实体字段被重放为候选词汇，并复用 E-028 grounding 计算召回。只读取 `task.id/subtask_id/domain/instruction/interactions`，模型/evaluator 调用为 0，不输出逐用户结果。
- **结果**：产物 `data/analysis/memory_compaction_audit_dev.json` 和 `docs/MEMORY_COMPACTION_AUDIT_DEV.md`。未截断宇宙含 4413 个 active facts、3977 个精确规范化实体项。当前策略 PreferenceStore 饱和用户为 7/8，候选可落地召回 3738/4160=0.8986；无界聚合策略为 0/8、召回 1.0、单用户最大 live entries=608；facet 公平且 entity cap=500 的策略为 0/8、召回 4034/4160=0.9697、单用户最大 live entries=559。三种策略的 safety、explicit、conditional 保留率均为 1.0；known-slot resolution 均为 89，重复已知槽询问均为 0。
- **选择**：推荐 `protected_aggregate_fair`。它满足全部预设门槛，同时相对当前策略把可落地事实召回提高约 7.1 个百分点；相比无界策略牺牲约 3.0 个百分点代理召回，换取每用户实体索引硬上限和较低的最大 live records。所有未知/未来非实体 dimension 继续进入 PreferenceStore，不会因 schema 未知被静默删除。
- **合并边界**：实体只在相同 typed slot 内做去标点/大小写后的精确规范化合并；不使用包含关系或 fuzzy resolution。公平保留只在实际观察到的桶间 water-fill，桶内再按 decision eligibility、证据类型多样性、source strength、confidence、recency 排序。
- **runtime 接入**：`ADAPTMemory` 默认启用双层存储；非实体事实进入受保护的 `PreferenceStore`，product/brand/searches/like 按精确 typed slot 进入每用户上限 500 的 `EntityEvidenceIndex`。`CombinedFactView` 保持原有 Decision Card、typed slot 和 E-028 候选二阶检索接口；runner 默认开启，并保留 `--no-tiered-compaction` 回退开关。
- **runtime audit**：8 用户/112 子任务重放中，PreferenceStore 到达 500 上限的用户从 7/8 降为 0/8；实体索引到达独立上限为 3/8。safety/explicit/conditional 保留率继续为 100%，known-slot 重复询问为 0。反事实 candidate-groundable recall 从 0.8986 提高到 0.9697。需要区分的是，搜索前结构召回从旧 audit 的约 0.765 小幅降至 0.7612；这不是候选返回后的可落地召回，也不被隐藏。
- **真实 smoke**：`data/traces/adapt_tiered_compaction_U901652.jsonl` 在 delivery/retail 工具形态下仅搜索一次；143 个候选诱导恢复 5 个正向历史值，alignment 得到唯一最高候选，随后 CREATE 成功并进入支付授权询问；无重复搜索、工具错误或 preflight rejection，`data/simulations/adapt_tiered_compaction_U901652.json` reward=1.0。该个案只证明非 instore runtime 接线正常，不单独宣称开发集收益。
- **适用边界**：candidate-groundable recall 是基于历史可观察实体字段的零模型代理，不代表对未见 catalog 同义词的真实召回，也不等于 benchmark reward。独立 entity cap 仍会裁剪高频实体历史，但不再挤占安全、显式和条件偏好的容量。
- **验证**：新增单测覆盖 520 实体硬上限、小 hotel facet 不被大 retail 桶挤光、feature flag 回退和多弱证据精确聚合。全量 `198 passed`，compileall 通过，VitaBench `src/vita` diff 为空。
- **下一步**：保持默认开启，不立即为个别用户或实体调整；下一个里程碑 dev 评测时按聚合错误类对比开关，如果 reward 或 candidate-to-action 指标回归，直接用回退开关做同 cohort 归因。
- **能力抽象**：preference updating / long-horizon consistency / preference-to-candidate grounding。

## E-030：自建指标口径与官方不一致，导致 Pass@4/Pass^4 被误报为 0

- **日期**：2026-09-10
- **状态**：VERIFIED
- **难点**：8 用户 × 4 trial 的 dev 基线跑完后，自写工具 `agent/trace_metrics.py` 报出 `pass_at_4 = 0`，而官方论文表格中同类任务存在非零 Pass 值，容易被误读为"agent 完全没通过任何任务"。
- **证据**：`data/simulations/stock_avg4_8u.json`（32 次模拟全部可评分）。`trace_metrics._pass_at_four` 按**用户级**分组，仅当 4 次 trial 的 reward 全部 ≥ 1.0 时判定通过；该数据单次 trial 最高 0.4545，从未达到 1.0，故通过数恒为 0。
- **根因**：口径与粒度双重不一致。(1) 粒度：官方 personalization 指标以 `(task_id, subtask_index)` 为评估单元，`trace_metrics` 以整个用户为单元；(2) 实现：官方 `vita.metrics.agent_metrics` 使用无偏估计 `pass@k = 1 - C(n-c,k)/C(n,k)` 与 `pass^k = C(c,k)/C(n,k)`，`trace_metrics` 用简化布尔。
- **有效方案**：对外汇报一律走官方入口 `vita.metrics.agent_metrics.compute_metrics(results)`（构造原生 `Results` 对象），并明确标注 task 级 / subtask 级；`trace_metrics` 降级为开发期粗筛工具，不用于最终指标。
- **验证**：同一次运行的两条独立路径数值一致——直接调用 `_compute_subtask_pass_metrics` 与官方完整 `compute_metrics` 均得 subtask 级 Avg@4 = 0.2925、Pass@4 = 0.4000、Pass^4 = 0.2000（100 个单元）；task 级 Pass 为 0。
- **适用边界**：官方 task 级 pass 同样要求 reward == 1.0，在本任务形态下长期接近 0，不应作为主指标；对外汇报必须写明粒度，避免"全是 0"式误读。
- **能力抽象**：evaluation validity / metric reporting。

## E-031：stock 基线的系统性失败集中在"搜索→落地"断链，而非支付或参数错误

- **日期**：2026-09-10
- **状态**：OPEN
- **通用性判定**：`GENERAL-EMPIRICAL`。结论来自固定 8 用户的聚合可观察轨迹，并设 20 个"4/4 全对"单元作对照组；只使用工具调用名、工具返回状态、错误标记、对话长度与终止原因等可观察事件，不读取 rubric、不把 evaluator reward 作为学习信号，也不使用 target/distraction 标注。
- **难点**：stock 在同配置下的 8 用户 Avg@4 仅 0.2925；100 个子任务单元中 60 个"4 次全错"、仅 20 个"4 次全对"，失败呈**系统性**（不是采样抖动）。因此提升必须靠确定性控制，而不是靠重采样。
- **证据**：`data/simulations/stock_avg4_8u.json` 与 `scripts/_t2_cluster.py`。永错组 vs 全对组（每 trial 均值）：搜索 4.99 vs 1.80（2.8×）、写操作 0.87 vs 1.24（−30%）、对话长度 19.6 vs 15.0。按域：delivery 58 单元（29 永错 / 18 全对）、instore 26（19 / 1）、ota 16（12 / 1）；搜索:写 比为 delivery 3.5、instore 10.1、ota 6.9。
- **已排除**：不是工具报错（永错 0.02 vs 全对 0.00）；**不是"下单未付款"**——全对组的 unpaid 比例（0.562）反而高于永错组（0.342），说明子任务级的"未付"不必然失分；提问次数亦无判别力（永错 2.35 vs 全对 2.77，方向相反），因此"问太多"不能作为本批的主因结论。
- **根因假设**：在多步/层级工具形态（instore 服务预约、ota 酒店/机票/景点）下，缺少"候选已足够即应落地下单"的确定性迁移控制，模型倾向继续搜索；长对话又强化继续搜索的锚定（与 E-007、E-008 同类）。
- **候选方案**：(1) 生成前的确定性 next-action 路由：候选完备即禁止继续搜索，授权已给即禁止再问；(2) 候选级搜索预算与父→子候选的有界展开（呼应 E-026）；(3) 支付闭环降级为次级项，并在**用户级**（而非子任务级）单独验证其影响。
- **有效性要求**：先在 instore/ota 上做 1–2 用户 smoke，再跑同 8 用户 × 4 trial 的 paired 对比；要求搜索:写 比下降、永错单元数下降、官方 Avg@4 提升，且不得为具体用户、子任务或候选写规则。
- **能力抽象**：bounded exploration / action routing / preference-to-action grounding。

---

## E-032：框架级推荐消息重复发送，把对话活锁到 max_steps

- **日期**：2026-09-10
- **状态**：VERIFIED
- **通用性判定**：`GENERAL-MECHANISM`。由框架自身的发送条件缺失导致，与用户、任务、候选无关。
- **难点**：某个 instore 子任务出现 101 条消息、termination=`max_steps`，但模型并没有报错——是框架每轮重复发送同一条推荐文本，用户端反复收到同一句话后停止响应。
- **证据**：`data/simulations/adapt_smoke_search_budget.json` 中该子任务的消息数与终止原因；debug 事件流中 `recommendation_finalized` 出现 58 次。
- **根因**：`_framework_recommendation` 只判断 `action == "recommend"` 与 `phase == SELECT`，没有"本子任务已发送"标记。重复的推荐既不能带来新信息，也不会改变 phase，于是每一轮都满足发送条件。
- **有效方案**：新增 `self._recommendation_delivered`，在 `set_current_instruction` 中按子任务重置，保证每个子任务最多发送一次。
- **验证**：单测覆盖"第二次调用返回 None"；smoke 复跑同一子任务消息数从 101 降到 8，无 `max_steps`。
- **适用边界**：只约束框架自身的兜底消息，不影响模型正常输出；模型仍可在收到用户新答复后继续对话。
- **能力抽象**：long-horizon consistency / loop safety。

## E-033：用户直接要求成交时 phase 被推回 SEARCH，CREATE 工具全部不可用

- **日期**：2026-09-10
- **状态**：VERIFIED
- **通用性判定**：`GENERAL-MECHANISM`（按授权状态与阶段迁移定义，不涉及具体用户/任务）。
- **难点**：用户已明确要求下单，运行时却回到 SEARCH；SEARCH 阶段不暴露任何 CREATE 工具，模型无合法动作，只能反复尝试被拒绝，最终以终局拒绝结束。
- **证据**：debug 事件 `preflight_rejected`：`tool create_instore_product_order is not allowed in phase search`（同一子任务连续 3 次）。
- **根因**：`observe_user` 的 `_advance_from_observation()` 无条件把非终态阶段拉回 SEARCH，而"完成型下单请求（如"给我团一张"）"在语义上已经同时满足"成交授权 + 由框架选择候选"。
- **有效方案**：在 `observe_user`／`observe_candidates` 内做确定性提升：当 `create_authorized` 且（用户委托选择 或 完成型请求授权候选 或 已选择 或 学习到的强制决策）且 `execution_ready` 且已观察到候选时，直接进入 `READY_TO_CREATE`，不再等待下一轮搜索。
- **验证**：单测 `test_explicit_purchase_request_promotes_without_another_search`、`test_user_turn_without_purchase_intent_does_not_promote`；smoke 复跑中该子任务不再出现"phase search 拒绝 CREATE"事件。
- **适用边界**：不覆盖"用户只是询问/浏览"的轮次；缺少成交授权或候选未观察时提升条件不成立，仍停留在 SELECT/SEARCH。
- **能力抽象**：execution / action routing。

## E-034：下单措辞未覆盖，把"帮我定张车票"编译成推荐任务

- **日期**：2026-09-10
- **状态**：VERIFIED
- **通用性判定**：`GENERAL-EMPIRICAL`。语料来自可观察的用户指令与框架事件，不含任何评测标注。
- **难点**：同一句下单指令，规范层判定为"推荐"、运行时层也没有授权写入，于是框架直接以"推荐完成"结束该子任务；用户随后再次表达成交意愿时，框架回复"操作已成功完成。"，而整个子任务**没有任何写工具调用**。
- **证据**：`data/simulations/smoke_fix2.jsonl`：该子任务仅有 `read`/`search` 类 `tool_proposal`，`recommendation_finalized` 后 phase=done，随后一次 `runtime_completed`。可观察指令为"周六要去绵阳找朋友，帮我定张车票"。
- **根因**：规范层（`TaskSpec.compile` 的 commit 关键词表）与运行时层（`_CREATE_MARKERS` + `_CREATE_INTENT_RE`）是两张独立的字面量表，都没有覆盖"定＋量词＋名词"等常见说法，两者逐渐漂移。
- **有效方案**：新增 `agent/intent.py` 作为唯一词表：`TRANSACTION_MARKERS`（明示交易短语）与 `COMPLETION_INTENT_RE`（动词＋量词/单位/名词）。三层口径统一由它派生。为避免"订单状态"这类复合名词误判，纯单位不足以成立，必须是 量词＋单位／单位＋名词／名词 三种形态之一；"就选第一个""推荐一个采摘园"明确不匹配。
- **尝试过但无效的方案**：只在 `TaskRuntime` 里继续加关键词——规范层仍是 recommend，框架照样会抢先以推荐结束。
- **验证**：`agent/tests/test_order_intent.py` 20 个正负例；全量 277 单测通过。smoke 复跑确认：同一子任务由"无写操作 + 框架回复已成功完成"变为 `train_ticket_search` → `get_ota_train_info` → `create_train_order`（订单落库，未付款），且不再出现虚假完成语。机制层达标；**具体车次是否正确属于候选质量议题，不在本条目内**。
- **附带修正**：未真正写入成功时不再回复"操作已成功完成。"；DONE 状态下收到新的成交请求或候选认可时重新进入 SEARCH（避免框架终态吞掉后续下单）。
- **适用边界**：词表只做"是否要求执行交易"的二分类，不判断具体商品；量词表是封闭集合，罕见说法仍会漏判，需要靠 trace 继续扩充。
- **后续风险/下一步**：需要在 smoke 中确认该子任务确实进入 CREATE 分支；同时评估"定／订"扩表是否引入误授权（负例测试已覆盖常见信息型请求）。
- **能力抽象**：preference-to-action grounding / execution。

## E-035：execution_ready 只看"任一 CREATE 可用"，模型用记忆里的 product_id 下单

- **日期**：2026-09-10
- **状态**：VERIFIED
- **通用性判定**：`GENERAL-MECHANISM`（按工具 schema 的必需实体类型定义，不涉及具体用户/任务/候选）。
- **难点**：商家搜索之后，模型直接用一个**来自长期记忆、本次未由任何工具返回**的 `product_id` 去下单，连续 3 次被确定性校验拒绝，随后以终局拒绝结束；整个子任务没有发生一次商品级搜索。
- **证据**：`data/simulations/smoke_fix2.jsonl` 中 3 次 `preflight_rejected`，内容为"`product_id=… was not returned by a tool in this subtask`"；同子任务的 `tool_proposal` 只有商家搜索。
- **根因**：三层叠加。(1) `ToolRegistry.execution_ready` 的语义是"存在某个 CREATE 工具、其必需 ID 实体都已观察"——到店域里 `instore_reservation` 只需要 `shop_id`，于是商品完全没观察也会判定 ready；(2) `READY_TO_CREATE` 把**所有** CREATE 工具都暴露给模型；(3) 没有任何机制去补一次缺失实体类型的搜索。
- **有效方案**：(1) 新增 `create_gaps`／`usable_create_tools`，按**每个** CREATE 工具计算缺失实体类型（`room/ticket/seat` 归一为 `product`）；(2) `READY_TO_CREATE` 只暴露必需实体齐备的 CREATE 工具，缺失实体的工具保持隐藏（校验器仍是最后一道闸）；(3) 框架新增一次有界的"缺失实体搜索"：仅当任务确实指向商品级对象（可观察词表）且域内存在名字包含该实体类型的搜索工具时触发，关键词只取可观察来源——当前指令的 MUST 原子与自己上一次搜索用过的 keywords，每个子任务每个搜索工具最多一次，并照常计入候选级搜索预算。
- **尝试过但无效的方案**：第一版只判断"存在缺失实体"就发起搜索，没有要求"该 CREATE 的其它必需实体已观察"。结果在 ota 子任务里，任何搜索都还没发生时框架就替模型去调用 `hotel_search_recommand`／`attractions_search_recommend` 等（参数不全，全部报 `unexpected keyword argument`／`missing required argument`），连续 9 次把搜索预算烧光，原本能正常下单的酒店子任务直接崩掉。修正后收紧为三条同时成立：ledger 已有候选、该 CREATE 的**其它**必需实体全部已观察、缺失实体恰好只剩一种；并在发出前用 `validate_required` 自检参数，避免框架自己制造无效调用。
- **验证**：`agent/tests/test_entity_gap_search.py` 11 个单测（缺实体、按工具就绪、READY_TO_CREATE 可见性、关键词来源、只触发一次、未授权不触发、空 ledger 不触发、缺失多种实体不触发、参数不全不触发）。smoke 复跑确认：`entity_gap_search` 事件恰好触发一次（`missing=["product"]`），随后模型用**本次工具返回的** product id 成功下单，不再出现"product_id 未由工具返回"的拒绝。
- **适用边界**：仅当域内存在"名称包含缺失实体类型"的搜索工具时才触发（delivery/instore 有商品级搜索；ota 酒店/机票没有，仍走既有的父候选展开路径）。只做一次，不会变成新的搜索 thrash。
- **后续风险/下一步**：需要在 smoke 中确认商品搜索返回后能进入真实写操作，并观察是否出现"该搜索反而拉入无关商品"的副作用。
- **能力抽象**：execution / bounded exploration。

## E-036：SELECT 阶段认可候选后，唯一的合法动作（确认执行）被 gate 拒绝

- **日期**：2026-09-10
- **状态**：VERIFIED
- **通用性判定**：`GENERAL-MECHANISM`（由阶段与授权状态决定，不涉及具体用户/任务）。
- **难点**：框架已经给出推荐列表，用户认可其中一项；此时运行时停在 SELECT，写入未获授权，模型想确认"是否需要我帮你预订？"却被 question gate 以"questions are not allowed in phase select"连续拒绝 3 次，最后输出终局拒绝文本。用户看到的是"推荐之后莫名其妙拒绝服务"。
- **证据**：`data/simulations/smoke_fix2.jsonl` 中 3 次 `preflight_rejected`（`question gate: questions are not allowed in phase select`），随后一条 `现有候选无法满足硬约束，我没有执行下单。`
- **根因**：question gate 只在"尚未选择"时允许候选选择类提问；一旦 `selection_made` 为真且没有成交授权，gate 落到底部"该阶段禁止提问"，而该阶段又不暴露任何 CREATE 工具——模型没有任何合法动作，终局拒绝成为唯一出口。
- **有效方案**：新增一个受预算约束的提问维度 `execution_confirmation`：当 `phase == SELECT` 且 `selection_made` 且未授权写入时，允许**一次**执行确认提问；该提问通过 `QuestionGate.commit` 置位 `execution_confirmation_pending`，下一轮用户回复只要不含拒绝词就视为成交授权（`create_authorized = True`），随后由 E-033 的提升条件进入 READY_TO_CREATE。
- **验证**：`agent/tests/test_execution_confirmation_is_allowed_once_after_endorsement`、`test_confirmation_answer_authorizes_execution_and_promotes`、`test_declining_the_confirmation_does_not_authorize_execution`、`test_finalized_recommendation_reopens_for_a_follow_up_order`。smoke 复跑确认：推荐 → 用户认可 → 框架提一次"要不要预约" → 用户答"周六下午吧，你看着办" → `instore_reservation` 落库并输出完成语，全程无终局拒绝。
- **尝试过但无效的方案**：只放开提问、不把回复接成授权。smoke 中模型确实问了"你想预约周六还是周日"，用户回答"周六下午吧，你看着办"之后，`choice_delegated` 分支又把提问全部拒掉，而 SELECT 阶段仍不暴露 CREATE —— 仍然以终局拒绝结束。提问与授权必须成对接地。
- **适用边界**：只在"用户明确认可了某个候选、但尚未要求交易"时生效；已授权成交或用户委托选择时，既有分支仍然禁止重复确认。拒绝词表要短且明确（不用/不要/算了/取消…），避免把"周六下午"这类回答误判为拒绝。
- **后续风险/下一步**：确认它不会演变成"每次都要多问一轮"；若确实多问，应在已有成交授权时保持沉默。
- **能力抽象**：proactiveness / execution。

---

## E-037：evaluator 输出归一化只在离线重评路径生效，在线运行把可评分子任务记成 evaluation_failed

- **日期**：2026-09-10
- **状态**：VERIFIED
- **通用性判定**：`GENERAL-MECHANISM`。与用户、任务、候选无关，是框架接线问题。
- **难点**：肉眼可见已经正确完成的子任务（搜索 → 创建订单 → 进入待支付）在运行结果里 reward 恒为 0.0，并被标记 `evaluation_status: evaluation_failed`；同一批里其它子任务评分正常，容易误判成"agent 做错了"。
- **证据**：`data/simulations/adapt_smoke_fix4.json`（可复现）：某 ota 子任务 3 次评估尝试全部以 `Evaluation error: 'list' object has no attribute 'get'` 失败，`evaluation_attempts: 3`。
- **根因**：`normalize_evaluator_result` / `patch_evaluator_extracter` 只被 `agent/reevaluate_guarded.py` 调用（`--normalize-extracter`），`agent/vitabench_runner.py` 从未安装它。evaluator 偶尔返回"嵌套列表 + 夹杂标量"的 JSON，vendored `_evaluate_window` 直接对非 dict 项调用 `.get`，于是整窗抛错、重试三次后放弃，子任务被判为不可评分。
- **有效方案**：在 runner 的 `main()` 里默认安装 `patch_evaluator_extracter()`（同时 patch `vita.utils.utils` 与 `vita.evaluator.evaluator_traj` 两处绑定），并新增 `--no-normalize-extracter` 作为对照开关。归一化只改变 evaluator 载荷的**形状**（递归收集 dict、丢弃标量、`"true"/"false"` 归一为布尔、剔除非法 `meetExpectation`），不动 rubric 文本与 reward 语义，因此不违反配置对齐。
- **验证**：`agent/tests/test_evaluator_normalize.py` 覆盖各形状；离线路径此前已用同一归一化把 12 个失败 trial 全部恢复为可评分。在线 smoke 复跑待确认（见"后续风险"）。
- **适用边界**：只对 evaluator 的输出解析生效，不改变评分标准；若 evaluator 返回的确实是无法解析的内容，仍会如实记录为 `evaluation_failed` 而不是伪造 reward。
- **后续风险/下一步**：确认在线运行中不再出现 `evaluation_failed`；同时保留 `--no-normalize-extracter` 以便必要时复现原始错误。
- **能力抽象**：evaluation validity。

---

## 本轮开发 smoke 记录（2026-09-10，4 个 instore/ota 子任务）

固定同样 4 个子任务（E941775 酒店 + 采摘园推荐、P722245 动车票 + 团购券），
逐轮加入确定性控制后观察**可观察行为**的变化。reward 在最后一轮仍为 0，但
失分点已从流程转移到"选哪个候选 / 有没有把选择讲出来"；下表的意义在于把
**流程性失败**逐条消掉。

| 轮次 | 加入的机制 | E941775 酒店 | E941775 采摘园推荐 | P722245 动车票 | P722245 团购券 |
| --- | --- | --- | --- | --- | --- |
| fix2 | E-032/E-033 | 建单成功、可评分 | 推荐后认可 → 3 次提问被拒 → 终局拒绝 | 编译成推荐 + 虚假完成语，无写操作 | 用记忆里的 product_id → 3 次拒绝 → 终局拒绝 |
| fix4 | E-034/E-035/E-036 | 建单成功，但 evaluator 抛错 → `evaluation_failed` | 允许一次确认提问，第二轮仍终局拒绝 | **真实 `create_train_order` 落库** | **`entity_gap_search` 补商品搜索 → 用真实 product_id 下单成功** |
| fix5 | 确认回复接成授权 + E-037 | 建单成功、正常评分 | **确认 → `instore_reservation` 落库**、无拒绝 | 同上（正常评分） | 同上（正常评分） |
| fix9 | E-038 距离/评分先验 | 建单成功 | 推荐 + 预约落库（商家仍非期望） | 下单 G2054（车次类型错） | 下对目标商品（仍 0 分） |
| fix10 | E-039 相对日期 | 建单成功（2025-01-18） | 预约 2025-11-08 落库 | 下单 2024-05-25（日期正确） | 先确认 2024-12-25 再下单 |
| fix11 | E-040 订单品类标签 | — | — | 选到 D 车次，但车次详情里二等座售罄 → 退到一等座 | — |
| fix12/13 | E-041 票务词表 | 建单成功 | 推荐 + 预约落库 | **`D1835 / 二等座 / 2024-05-25 / 数量1` → reward 1.0** | 下对目标商品（仍 0 分） |

最终一轮（fix10）在**世界状态**层面的逐项对照（`states.new_states`，仅离线分析）：

- 车票：订单为 `G2054 / 二等座 / 2024-05-25 / 成都→绵阳 / 数量 1` —— 日期、座位、起止、数量全部符合，**唯一失败项是"车次类型"**（应为动车 D）。
- 团购券：订单为 `通络堂养生按摩(南湖西路店) I00021 + 全身经络舒缓按摩90分钟团购券 ¥188 × 1` —— **正是该子任务期望的商品**，且写作前已确认 2024-12-25 是工作日；剩余失败项与"以文本形式把选定的商家讲给用户"有关。
- 酒店 / 采摘园：写入成功、日期正确，失败项在**商家选择**（哪家酒店、以及"离用户近、评分高"这一维度）。
- 车票子任务在 fix12/fix13 两轮独立运行中稳定拿到 **1.0**，机制链可观察：词表修正让 `domain=ota, facet=train` → `hierarchical_enrichment count=3` 展开 3 个父车次 → 记忆里的"动车/二等座"接地后把 D 车次与二等座座位排到前面 → 写入 `D1835 / 二等座`。这是本轮第一个从"4/4 全错"变为"稳定通过"的单元。

---

## E-038：候选排序不含"用户住在哪"和"商家评分"，平票时由观察顺序决定

- **日期**：2026-09-10
- **状态**：PARTIAL（机制已生效，聚合效果待配对验证）
- **通用性判定**：`GENERAL-INVARIANT`。只用两串可观察文本——运行时的用户注册住址、工具返回的候选自身地址/评分；不含用户、任务或候选 ID，不读 rubric/reward/target。
- **难点**：推荐/选择阶段的排序只看"偏好证据分"，证据相同时由**观察顺序**决定名次。于是框架把一家距用户 38km 的商家列为"首选"，用户在文本里看到的推荐完全无法反映"离我家近不近、评分高不高"。
- **证据**：
  - 离线核对：该子任务用户注册住址为"四川省成都市温江区寿安镇"，而被推为首选的候选位于青白江区（直线约 38km）。
  - `data/simulations/smoke_fix7.jsonl` 的 `recommendation_ranking` 事件：`home_tokens=['成都市','温江区','寿安镇']`，参与排序的 8 个候选中，本区候选 `proximity=2`（距家最近的 4.9 分商家）却排在 `proximity=1` 的 4.5 分候选之后。
  - 另有一条同镇不同县的假匹配：温江区"寿安镇"与蒲江县"寿安街道"同名，最初被当成同一地点（已按"只有区/县可省略后缀"收紧）。
- **根因**：`CandidateRanker.rank` 的排序键是 `(证据分, 观察轮次, -价格)`；既没有位置维度，也没有"工具已公布评分"维度。位置信息其实一直可见：用户档案里有住址，候选行里有 `location=address=...` 与 `score=`。
- **有效方案**：(1) 新增 `agent/runtime/location.py`：从用户注册住址切出行政区划单元（省市区县镇街道），对候选的 name+raw 做有序匹配——同区/县/镇/街道记 2，仅同市记 1，否则 0；只有区/县允许省略后缀匹配（避免同名乡镇跨县误配）。(2) `CandidateRanker.rank` 的排序键变为 `(证据分, 邻近度, 评分, 观察轮次, -价格)`：证据仍是主键，邻近与评分只在同分时起作用。(3) 商品行没有自己的地址，改为继承其父商家（`parent_ids`）的邻近度。(4) 推荐过滤器只在**决定性**偏好证据存在时才收窄候选；若只有非决定性（弱池）匹配，则直接呈现排序结果，避免一次偶然子串命中盖过"更近、评分更高"的候选。
- **验证**：`agent/tests/test_location_prior.py` 13 个单测（行政区划切分、省市区三级邻近度、同名乡镇不误配、评分读取、平票按邻近再按评分、邻近不得越过偏好证据、无住址上下文时排序不变、商品继承父商家邻近度、非决定性匹配不遮蔽更近候选、决定性偏好仍保持优先）。
- **适用边界**：邻近度只在**同分**时生效，因此跨域任务（异地酒店、异地车次）不受影响；本条目**不**包含"多远算太远"的阈值判断——该阈值无法从可观察数据推导。
- **后续风险/下一步**：仍有一个单元的记忆里**确实**存在"绿野仙踪草莓采摘园"这一决定性历史偏好，而该商家距离较远；框架按既定优先级保留了它。要让"距离"越过用户自己的历史偏好，需要引入工具化距离测量（`address_to_longitude_latitude` + 候选行的经纬度）并把可达性提升为强约束——那是一次策略变更，需单独决策与验证。
- **能力抽象**：preference utilization / execution。

---

## E-039：相对日期靠模型心算，写操作前不一定确认过"今天是哪天"

- **日期**：2026-09-10
- **状态**：VERIFIED
- **通用性判定**：`GENERAL-INVARIANT`。输入只有两样可观察数据：环境给出的 agent 时钟（`self.time`，形如"2024-12-24 20:00:00 星期二"）与用户指令中的相对时间词；输出是纯日期算术，不含用户/任务/候选特例。
- **难点**：同一批 smoke 里，有的子任务模型自发查了日期，有的直接把"明天"心算后就去选带时效限制的商品；同一句"周六要去绵阳"在不同轮次也可能落到不同日期。是否需要"先确认日历日"变成了模型的随机行为，而不是运行时的确定性保证。
- **证据**：`data/simulations/adapt_smoke_fix9.log` 中同一批 4 个子任务：车票子任务调用了 `get_date_holiday_info`，而养生团购券与采摘园子任务全程没有查询任何日期工具，却都发生了与"哪一天"相关的选择；本地 `Deterministic runtime` 块此前只有阶段与授权状态，没有任何日期字段。
- **根因**：`TaskSpec.compile` 只从指令里抽**绝对**日期（`2025-01-15`、`11月8日`、`8号`），相对表达（明天/后天/周末/周六/下周三/N天后/月底）完全交给模型从系统提示里的时钟自行推算；运行时既不发布解析结果，也没有"写操作前必须确认日期"的门。
- **有效方案**：(1) 新增 `agent/runtime/schedule.py`：纯日期算术解析相对表达（明确的日偏移优先于"周末"优先于"周X"；`周X` 取最近的那个，当天且已过中午则顺延一周；`下周X` 固定落在下一周），并附带时段提示（早上/上午/中午/下午/傍晚/晚上）。(2) `TaskRuntime` 新增 `resolved_date`/`date_evidence`/`date_time_hint` 并在 `Deterministic runtime` 块里发布，模型拿到的是已解析的绝对日期而不是要自己换算。(3) 解析出的日期同时进入 `task_spec.resolved_slots["date"]`，参与既有的参数校验（写操作里的日期参数必须落在已知值上）。(4) 新增框架钩子 `_framework_date_grounding`：当指令含相对时间、且仍处于搜索/选择之前时，**只发一次** `get_date_holiday_info(已解析日期)`，把"今天是哪天、是否节假日"变成对话里可观察的事实，之后不再重复。
- **验证**：`agent/tests/test_schedule.py` 16 个单测（时钟解析、8 类相对表达、跨月/跨年、已过时段的顺延、时段提示、运行时渲染、钩子只发一次、无相对表达不发、进入执行阶段后不发）。smoke 复跑（`data/simulations/smoke_fix10.jsonl`）确认 4 个子任务各自**恰好一次**解析 + 恰好一次确认，且解析结果与最终写入的日期一致：周六→2025-01-18（酒店下单日）、周末→2025-11-08（预约日）、周六→2024-05-25（车票日期）、明天→2024-12-25（工作日、晚上）。
- **适用边界**：只做"相对→绝对"的确定性与一次确认，**不**包含"哪一天适合做什么"的业务判断（例如某类券是否只在工作日可用）；解析失败时保持原状，不猜测。
- **后续风险/下一步**：需要 smoke 确认钩子确实在选品之前发出、且不会与模型自发的日期查询重复；若某域没有 `get_date_holiday_info` 则自动跳过。
- **能力抽象**：proactiveness / execution grounding。

---

## E-040：订单历史里的"品类/等级"标签被丢弃，用户的乘车习惯对候选排序不可见

- **日期**：2026-09-10
- **状态**：VERIFIED
- **通用性判定**：`GENERAL-EMPIRICAL`。只用 agent 本来就能拿到的 `interactions`（订单行为记录）中的 `tags`；不读 evaluator、rubric、target，也不读基准刻意不提供给 agent 的字段（见下"边界"）。
- **难点**：同一位用户的订单历史里明确写着 `tags: ["动车", "二等座"]`（商品名 `D2372 成都东-黄山北 二等座`），但候选里既有 `G…`（tags `['高铁',…]`）也有 `D…`（tags `['动车',…]`）时，框架看到的"偏好证据"完全相同，模型于是随手选了 G 车次。
- **证据**：`data/simulations/adapt_smoke_fix10.json` 的世界状态里，该子任务订单为 `G2054 / 二等座 / 2024-05-25 / 成都→绵阳 / 数量 1`——日期、座位、起止、数量全部符合评测期望，**唯一不符的是车次类型**；而该用户的可观察行为记录中确实存在动车+二等座的订单标签。
- **边界（重要）**：VitaBench 另有一份结构化档案（形如 `"出行方式倾向": ["动车"]`），但它**只在 `GroundtruthMemory` 基线下注入**，且 vendored 代码明确注释 `get_user_historical_behaviors removed — leaks ground-truth preference_memory to agent, bypassing memory module`。因此本条目**不**使用该字段：那等于直接读取被刻意保留的答案，会让 ADAPT 与 stock 的比较失去意义。这里只用 `interactions` 里人人可见的订单标签。
- **根因**：`agent/memory/signals.py` 只把订单 `tags` 映射到两张手写白名单（`TASTE_DIMENSIONS`／`SERVICE_ATTRIBUTE_DIMENSIONS`，覆盖口味、温度、甜度、少量服务属性）。"动车/二等座/免费停车/景区附近"这类**品类与等级**标签不在白名单里，于是被整条丢弃，从不进入事实库。
- **有效方案**：新增 `_extract_order_class_tags`：把订单 `tags` 中"短（2–8 字）、不是商家名（不重复 `merchant_name`）、非纯数字/营业时间"的标签提升为 `attribute_preference` 信号；下游仍由 `ground_facts_to_candidates` 把关——只有能落到**当前实际候选**字段上的事实才会进入 Decision Card，因此无关历史保持惰性。
- **验证**：`agent/tests/test_order_class_signals.py` 8 个单测（标签提升、商家名不提升、数字/营业时间跳过、白名单不重复、仅对活跃候选接地、"二等座"在无座位候选时不接地、接地后排序把 D 车次排到 G 之前、事实库里保留该值）；全量 320 单测通过。smoke 复跑确认：同一子任务订单由 `G2054` 变为 `D1835 / 二等座 / 2024-05-25 / 数量 1`，**reward 由 0.0 变为 1.0**（`data/simulations/adapt_smoke_fix12.json`）。
- **适用边界**：只提升订单标签这一层，不引入任何域词典；标签必须能被当前候选字段接住才生效，所以不会把"某次买过某店"变成跨域偏好。
- **后续风险/下一步**：新标签原子是否会挤占 Decision Card 的 8 条/1200 字上限，需要在聚合运行后复查 `decision_card_refreshed` 的 must/avoid/prefer 计数。
- **能力抽象**：preference extraction / utilization。

## E-041：票务类词表缺失，车票请求被编译成 delivery/retail，父候选展开从不发生

- **日期**：2026-09-10
- **状态**：VERIFIED
- **通用性判定**：`GENERAL-INVARIANT`。只补词表（"车票/火车票/高铁票/动车票/民宿/客栈…"），不涉及任何用户、任务或候选标识。
- **难点**：`周六要去绵阳找朋友，帮我定张车票` 被编译成 `domain=delivery, facet=retail`。框架的父候选有界展开（`_framework_enrichment`）按 facet 取工具映射，facet 不在映射里就整段跳过——于是整条轨迹只展开**一个**车次的座位，而那个车次的二等座恰好售罄，模型只能退到一等座，与用户"二等座"的历史偏好冲突。
- **证据**：`data/simulations/smoke_fix11.jsonl` 的 `runtime_policy_applied` 事件显示 `domain="delivery", facet="retail"`，且整轮没有任何 `hierarchical_enrichment` 事件；同轮订单为 `D1783 / 一等座`，而该车次详情里 `二等座 quantity=0`。
- **根因**：`_DOMAIN_MARKERS["ota"]` 与 `_FACET_MARKERS` 都只收"高铁/火车/动车/机票/酒店"等词，**没有收"车票"**这一最常用的说法；两者都缺失时回落到 delivery 默认值。
- **有效方案**：把可观察的票务/住宿说法补进两张词表（ota 域：车票、火车票、高铁票、动车、航空、民宿、客栈、宾馆、度假村、出行；train facet：车票、火车票、高铁票、动车票、列车、车站；hotel facet：住宿、客栈；flight facet：航空）。词表补全后 `_framework_enrichment` 按既有预算展开 3 个父候选。
- **验证**：`agent/tests/test_order_intent.py::test_ticket_vocabulary_compiles_to_the_right_domain` 覆盖 6 种说法；smoke 复跑（`data/simulations/smoke_fix12.json`）中 `runtime_policy_applied` 变为 `domain="ota", facet="train"`、`hierarchical_enrichment count=3`，订单为 `D1835 / 二等座`，**reward 1.0**。
- **适用边界**：只影响规范编译的分类与框架展开预算的适用性；不改变任何写入校验规则。
- **后续风险/下一步**：需要复查其它域是否也存在"常用说法缺词表"的同类问题（例如到综的"团券/搓背/养生"、delivery 的"闪购/跑腿"），可以用同样的可观察词表补齐方式处理。
- **能力抽象**：execution grounding / long-horizon consistency。

---

## E-042：ADAPT 相对 stock 净负，根因是控制层"替代"了模型的能力（自伤性退化）

- **日期**：2026-09-10
- **状态**：OPEN（修复已实现并单测通过，A/B 验证进行中）
- **通用性判定**：`GENERAL-EMPIRICAL`。结论来自同 8 用户、同模型、同端点下的**配对轨迹对比**（ADAPT 1 trial vs stock 4 trial 均值），只用可观察的工具调用/消息/终止原因，不读 rubric、不用 reward 作学习信号。
- **难点**：本轮修复了 E-032…E-041 十类"必然失败"之后，ADAPT 在已完成的 3 个用户上仍显著落后：**27 个单元 ADAPT 0.1111 vs stock 0.2870（同单元）**，形态是"stock 偶尔做对、ADAPT 从不做对"（LOST 7 : GAINED 1）。用户级：E057330 1/13（stock 3.75/13）、E941775 2/14（stock 4/14）、J365414 1/3 起（stock 4.5/11）。
- **证据**（`data/simulations/adapt_avg1_8u.json` vs `stock_avg4_8u.json`，同一批用户）：
  1. **重复下单**：某单元把同一订单创建 **14 次**；框架 `payment_question` 事件 58 次/27 单元。机制：CREATE 成功 → `observe_tool_result` 重置 `payment_question_sent` → 框架每轮再问支付 → 用户回一句话 → `observe_user` 回落到 SEARCH → **E-033 的提升条件再次成立** → READY_TO_CREATE → 模型再 CREATE。E-033 缺少"写入成功后不得再次提升"的不变量。
  2. **记忆查询能力被移除**：stock 在同批单元调用 `query_preference_memory` **30 次**（常作为第一步，用来生成搜索词与推荐理由），ADAPT **0 次**——`ToolRegistry` 把 MEMORY 角色工具视为框架内部而不暴露。
  3. **探索被压死**：ADAPT 每单元搜索 **1.00 次**（最大 2），stock **3.11 次**（最大 50）；`search_budget_rejection` 的"存在合规候选即停"在第一次搜索后就阻断后续关键词族探索。
  4. **"先说话再动手"被短路**：stock 在 108 个单元中有 **9 个零写操作拿满分**（推荐+理由本身就满足评测）；ADAPT 在 `action=commit` 任务里直接搜索→下单，偏好推理从未出现在对话中，而框架的"推荐定稿"只在 `action=recommend` 时触发且仅列出店名。
- **根因**：控制层的设计取向是"框架替模型决策"（充分性停搜、框架推荐定稿、按阶段裁剪工具、隐藏记忆工具），在 27B 模型上净负：它用规则替代了模型本来就具备、且在 stock 下被证明有效的能力（按需查记忆、多关键词族探索、先解释后执行）。
- **有效方案（"护栏而非治理"）**：(1) `observe_user`/`observe_candidates` 的提升条件加入 `not write_succeeded`；(2) `observe_tool_result` 不再重置支付追问标记，且 `_preflight` 用 `attempt_signature` 拒绝**完全相同的已成功写入**；(3) 恢复 `query_preference_memory`/`read_preference_memory` 为可被模型调用的 READ 工具（ADAPTMemory 新增对应 `@is_tool`）；(4) 探索额度从"存在候选即停"改为"族内已用 >3 次不同查询才停"，族预算 2→3、族上限 4→6；(5) 框架推荐降级为兜底（模型在 SELECT 先有 2 轮机会），并在推荐里附上命中的偏好证据；READY_TO_CREATE 指令要求"先用一句话说明选了什么、满足了哪条偏好，再下单"。
- **验证**：`agent/tests/test_no_self_inflicted_loops.py` 7 个单测（不再提升、支付仅一次、重复写入被拒、不同写入仍允许、记忆读工具可见而记忆写工具仍内部、记忆查询返回有界读取、推荐兜底延后）；全量 327 单测通过。A/B（E057330 1 trial，对照其上一版 1/13 与 stock 3.75/13）进行中。
- **适用边界**：这些改动只撤销"框架替代模型"的部分，保留全部写前校验、实体义务、日期算术与完整性护栏；探索额度仍受族预算约束，不会回到 E-031 的搜索 thrash。
- **后续风险/下一步**：A/B 若仍低于 stock，则按"护栏化"继续下探（例如把阶段裁剪改为全工具暴露+写前校验），并按 P1/P2 的顺序做带开关的消融。
- **能力抽象**：preference utilization / execution / long-horizon consistency。

---

## E-043：档案里同时有"常住地"和"常住住址"，框架取了城市当送货地址

- **日期**：2026-09-10
- **状态**：VERIFIED
- **通用性判定**：`GENERAL-INVARIANT`。按键的语义（地点 vs 街道）取值，不涉及具体用户或任务。
- **难点**：配送单要么被**误判为地址不合规**，要么被**填成一个无法地理编码的城市名**，两种都由同一个取值错误引起，最终都走到终局拒绝。
- **证据**：`data/simulations/ab_guard2_E057330.log` 中同一个水果拼盘子任务连续两次 `Error: Longitude and latitude not found for address 河南省郑州市`（我新加的地址补全把城市名填了进去）；同轮 preflight 拒绝里 9 次与地址相关（`address does not resolve to the user's home address`、`address argument does not satisfy required value`）。
- **根因**：`_profile_address` 以标记 `常住` 匹配键，而档案里 `常住地`（城市）排在 `常住住址`（街道）之前，于是：
  (1) 校验器把"模型的完整街道地址"与"城市"比较，互不包含 → 误拒正确地址；
  (2) 地址补全用同一个函数取值 → 把正确地址覆盖成城市 → 环境无法地理编码。
- **有效方案**：`_profile_address` 跳过纯地点键（`常住地`/`籍贯`/`所在地`/`城市`/`city`/`province`/`省`），只在街道级键（`住址`/`地址`/`street`/`detail`）中取值，并对街道级键加权优先。
- **验证**：`agent/tests/test_no_self_inflicted_loops.py` 新增（街道地址胜过城市、仅城市时返回空、校验器接受完整街道地址，以及补全与不覆盖正确地址）；全量 341 单测通过。修复后地址补全事件在 A/B 中触发 10–11 次且不再报地理编码错误。
- **适用边界**：只影响"档案地址解析"这一处；不改变任何写前约束的语义。
- **后续风险/下一步**：不同数据源的地址键命名可能不同，新增键名时应只补街道级键。
- **能力抽象**：execution grounding。

## E-044：用户在历史里说过"哈密瓜过敏"，记忆层没提取出来

- **日期**：2026-09-10
- **状态**：VERIFIED
- **通用性判定**：`GENERAL-MECHANICAL`。只依赖句式（把物品放在避让词之前），与用户、任务、品类无关。
- **难点**：对一位**已确诊食物过敏**的用户，ADAPT 推荐并下单了含该过敏原的商品；同一单元 stock agent 明确避开并拿到分数。
- **证据**：该用户的可观察行为记录里有原话——"活了快 30 年，才知道自己哈密瓜过敏…我们科室医生让我去查了过敏源，果然中招了"；`data/simulations/ab_guard3_E057330.log` 中 ADAPT 的卡片显示 `AVOID: 烧烤`（无哈密瓜）并下单含哈密瓜拼盘；修复后 `data/simulations/ab_guard4.log` 的卡片显示 `AVOID: 哈密瓜`，模型主动说明"不含哈密瓜"，并与 stock 选中**同一家店**。
- **根因**：避让模式只匹配"不吃X/忌口X"这类**前置**说法；中文更常说"**X过敏**"。而"过敏"被当成前置标记时，会把**它后面的整句话**当成避让对象（抓出"，我就说为什么每次吃哈密瓜"这类垃圾），真对象反而丢失。
- **有效方案**：新增后置模式（`X过敏`/`X忌口`/`X不能吃`/`X吃不了`/`X不碰`），并加一个跨度清洗器：剥掉前导虚词（`才知道自己哈密瓜过敏` → `哈密瓜`），对含标点或虚词的整句跨度直接丢弃（`…让我去查过敏源` → 不产出）。既有维度映射保留：过敏 → `safety`（硬约束，真排除候选），普通不吃 → `avoid`。
- **验证**：`agent/tests/test_avoidance_signals.py` 9 个单测（真实原句、四种常见句式、三条负例不误报、`safety` 维度、卡片上出现硬 `EXCLUDES` 约束并改变候选排序）；全量 341 单测通过。
- **适用边界**：只做"避让对象"的抽取与归一；不含任何品类词表，因此对未见过的食物同样适用。
- **后续风险/下一步**：误报风险主要来自叙述性提及，已用"跨度含标点/虚词即丢弃"覆盖；仍需在更多用户上观察。
- **能力抽象**：preference extraction / safety。

## E-045：偏好领先者锁死候选、委托后禁止一切提问——两处"治理"仍然扣分

- **日期**：2026-09-10
- **状态**：PARTIAL（机制已验证生效，A/B 未见增益）
- **通用性判定**：`GENERAL-EMPIRICAL`。结论来自同用户同 seed 的配对 trace 与 preflight 拒绝统计。
- **难点**：在 27 个开发子集单元里，34 次 preflight 拒绝中有 6 次是"偏好分领先者否决模型选择"，16 次是"用户已委托选择 → 一切提问被拒"（每次拒绝耗掉三轮重规划，有时直接走到终局拒绝）。
- **证据**：`data/simulations/ab_guard4.jsonl` 的拒绝原因统计；修复后同一统计中委托类拒绝从 16 降到 6，并出现 `preference_leader_diverged` 4 次。
- **根因**：两者都是"框架替模型决策"的残留。(1) 偏好原子计数含噪（E-038 已证明共享命中不具判别性），却用它否决候选；(2) 委托语义本该只禁止"再问选哪个"，实现上却禁止了该阶段的一切提问。
- **有效方案**：(1) 领先者降级为建议——`validate_ranked_choice` 不再因"未选到分最高者"拒绝，只在 preflight 记录 `preference_leader_diverged`；**用户明确指定**仍然硬锁。(2) 委托与"用户认可候选"一样保留一次 `execution_confirmation` 逃逸口，回答非拒绝即视为授权。
- **验证**：`agent/tests/test_no_self_inflicted_loops.py` 与 `test_agent_architecture.py` 更新（领先者建议语义、显式选择仍锁定、委托逃逸口）；全量 341 单测通过。A/B：`guard5` 未见增益（见"评测口径"表），**不声称有效**，标记 PARTIAL 待 56 用户测量。
- **适用边界**：仍保留全部硬约束与写前校验；只撤销"以噪声分数否决选择"和"以委托为由禁止一切提问"。
- **后续风险/下一步**：单 trial 方差（±1 单元）掩盖了效应，需要多 trial 或更宽的用户范围才能判定。
- **能力抽象**：execution / preference utilization。

---

## E-046：相对 baseline 的净负来自"表示 + 自由度"，不来自缺失的机制补丁

- **日期**：2026-09-10
- **状态**：OPEN（隔离实验已启动：R2/R3/R4）
- **通用性判定**：`GENERAL-EMPIRICAL`。结论来自代码结构与同用户同 seed 的配对轨迹对比，不读 rubric。
- **难点**：本轮修掉 E-032…E-045 十四个"必然失败"后，ADAPT 在开发子集上仍只有 baseline 的 50–75%，且每加一处机制都在噪声内抖动（"打补丁—回退"循环）。需要回答的是**为什么 baseline 强**，而不是继续补机制。
- **代码级证据（baseline 为什么强）**：
  1. `PersonalizationAgent.system_prompt` 只拼三段：VitaBench 自带 `domain_policy`（很短）+ `## 当前用户基础信息`（完整注册档案）+ `## User Preference Memory`（`memory.read(query=当前指令)`）。**没有** ADAPT 附加的 policy / 决策卡 / 运行时状态 / lessons / 候选账本。
  2. `RewriteMemory` 的记忆是 **LLM 重写**的：`memory_update_prompt.yaml` 明确要求"保留有效偏好 / 更新矛盾偏好 / 新增偏好 / 按饮食-消费-时间-地点-服务等维度结构化"。因此它输出的是**维度级结论**（"喜欢冷色调""对哈密瓜过敏"），而 `ADAPTMemory` 关闭了摘要改写（`enable_summary_rewrite=False`），只产出**条目级事实**（"冰蓝色系眼影盘""蓝色星空十字绣"）——模型必须自己做两跳概括，实测它没做。
  3. baseline **不裁剪工具**、无阶段机、无终局拒绝路径；ADAPT 的 `allowed_tools` 按 phase 裁剪，且 `_generation_messages` 在 `READY_TO_CREATE/READY_TO_PAY` 会把消息历史**替换**为"system + 最后一条 user + 指令"，等于拿走模型自己的工具观察。
  4. 探索与发言：baseline 每单元搜索 3.1 次（ADAPT 1.0）；baseline 108 单元里 **9 个零写操作拿满分**（推荐+理由本身得分），ADAPT 在 commit 类任务里直接下单，偏好推理不出现在对话中。
  5. 分数结构：baseline 的单元分普遍是 0.1–0.5 的**部分分**，ADAPT 呈两极（0 或 1.0）→ 目标函数错位：baseline 优化"可见地满足偏好"，ADAPT 优化"任务完成、不犯错"。
- **根因**：机制层的收益在 E-032…E-045 中已基本取尽（0.06 → 0.15–0.21 全部来自"消除自己制造的故障"）；剩余差距在**记忆表示**（归纳 vs 条目）、**prompt 税**（多几千字符且替换历史）、**动作空间**（阶段裁剪）。任何新增门禁都在用规则替换 27B 的判断力，因此表现为净负。
- **有效方案（隔离实验设计）**：一次只变一个因子，全部与 baseline 同用户同 seed 对比：
  | 变体 | agent | prompt | 记忆 | 工具 |
  | --- | --- | --- | --- | --- |
  | R1 baseline | stock | stock | RewriteMemory | 全量 |
  | R2 | stock | stock | **ADAPTMemory**（`--memory-type adapt`） | 全量 |
  | R3 | adapt | **仅 stock prompt**（`--no-adapt-prompt`） | ADAPTMemory | 全量 |
  | R4 | adapt | stock + ADAPT 附加 | ADAPTMemory | **全量**（`--no-phase-gating`） |
- **验证**：`agent/tests/test_isolation_rig.py` 3 个单测（默认裁剪 vs 全暴露、框架内部记忆写工具始终隐藏、`--no-adapt-prompt` 时 prompt 与 stock 完全一致）；全量 344 单测通过。
- **适用边界**：隔离开关只用于对照实验，不改变默认行为；产出的结论用于决定下一轮做哪一项结构性改造。
- **后续风险/下一步**：按隔离结果排序改造优先级（预期：① 记忆回到"有界 LLM 归纳 + 候选接地过滤"；② 去 prompt 税并恢复完整历史；③ 全工具暴露、只保留可修复校验）。**验收规则**：任何改动必须在固定配对装置上胜过对照，否则默认回退。
- **能力抽象**：preference extraction / utilization / long-horizon consistency。

---

## 新记录模板

以后遇到新问题时复制以下模板。首次发现时标为 OPEN；只有证据满足要求后才能更新为 PARTIAL 或 VERIFIED。

```markdown
## E-XXX：一句话描述可复现难点

- **日期**：YYYY-MM-DD
- **状态**：OPEN | PARTIAL | VERIFIED | SUPERSEDED
- **难点**：观察到什么行为，不写隐藏答案。
- **证据**：trace 事件、测试名、聚合指标或产物路径。
- **根因**：能够被代码或对照实验支持的原因；不确定时写“假设”。
- **尝试过但无效的方案**：保留失败路线及原因。
- **有效方案**：通用机制，不包含 user/task/candidate 特例。
- **验证**：单测、smoke、dev、blind 中实际通过了哪些门禁。
- **适用边界**：在哪些场景下不应使用该方案。
- **后续风险/下一步**：仍缺少什么证据。
- **能力抽象**：对应 preference extraction / updating / utilization / proactiveness / execution / long-horizon consistency 中哪一类。
```

## 维护纪律

1. 每次出现新的可复现失败，先追加或更新 OPEN 记录，再修改架构。
2. 同一根因只维护一个主记录；后续复发添加证据，不创建用户特例条目。
3. 记录无效尝试，因为它们能阻止未来重复消耗。
4. 所有数字必须链接到当前存在的结果文件或测试输出。
5. 开发集可以记录逐案例证据；blind/final 只记录允许查看的聚合信息。
6. runtime lesson 与工程演进记录严格分离：前者属于单用户 Agent 状态，后者属于离线开发过程。
7. 只有冻结版本在未查看逐案例 trace 的 holdout 上复现，才能声称具有泛化效果。
8. 修改 Agent 代码前必须通过“通用性门禁”；未通过的 bad case 只记录，不修改。

### 通用性门禁

每个候选修改必须先回答以下问题：

1. **失败对象是否是能力类型**：根因必须落在 schema/字段角色、约束绑定、状态迁移、证据强度、候选关系或工具错误类别；不能落在 user/task/subtask/product/merchant ID。
2. **能否构造无数据集实体的最小复现**：使用虚构 ID、虚构候选和通用字段仍能复现，才允许进入单测和实现。
3. **修复是否由结构决定**：优先依据工具 schema、可观察结构化字段和候选父子关系；不得依据某个用户喜欢的具体商品、品牌、酒店或固定答案。
4. **是否有反例门禁**：同时测试不应触发修复的场景，尤其是当前指令冲突、不同 facet、错误日期、错误父候选和未授权 WRITE。
5. **是否跨案例成立**：结构性 bug 可由两个以上工具形态或一个 schema 不变量证明；经验性策略至少需要两个开发案例。单用户 reward 改善只能标为 PARTIAL。
6. **是否保持评测边界**：修改不得读取 reward、rubric、target/distraction 标记或 blind/final 逐案例信息，也不得修改 VitaBench。

判定结果固定为：

- `GENERAL-INVARIANT`：由 schema/类型不变量证明，可修改静态框架。
- `GENERAL-EMPIRICAL`：由多个开发案例支持，可修改受约束策略并继续 paired 验证。
- `CASE-SPECIFIC`：只解释单个用户、任务、商品或措辞；只记录，禁止修改。
- `UNRESOLVED`：证据不足；先做无 evaluator 的最小复现或另一个开发 smoke，禁止直接修改。

例如，E-024 的“酒店日期绑定在 room candidate 而不在 CREATE 参数”属于 `GENERAL-INVARIANT`；E-025 中“读取结构化 scenario/字段角色”可能属于 `GENERAL-INVARIANT`，但“该用户应选双床房”本身是案例结论，不能成为规则；E-023 的提交措辞是否同时授权支付目前仍为 `UNRESOLVED`，在获得跨场景安全证据前不得放宽支付授权。

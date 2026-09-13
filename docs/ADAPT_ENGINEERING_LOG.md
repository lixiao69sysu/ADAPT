# ADAPT 工程演进账本

本文档持续记录 ADAPT 在开发过程中遇到的可复现难点、根因、有效方案和验证证据。它服务于三件事：

1. 防止重复走已经证明无效的路线。
2. 将一次案例修补抽象为可跨用户、跨领域复用的 Agent 能力。
3. 为后续 trace-driven champion–challenger harness 提供结构化历史。

本文档不是 VitaBench 测试数据的副本。不得记录或向运行时暴露隐藏 rubric、reward、target/distraction 标记或目标 ID。当前反复查看过 trace 的用户均属于开发用途，其结果不能表述为无偏测试成绩。

## 设计主线（本项目的唯一主线，先读这一节）

> **ADAPT = 一个"不凭空断言"的控制器 + 一个"能直接用的证据可追溯数据层"。**

controller 只被允许做三件事：**传递观察到的值**、**扣住动作**（把不可逆动作留在还能提问/观察的状态）、**把问题交回用户**。
它绝不允许做三件事：**凭空生成值**、**替用户或模型做决定**、**替模型说话**。

这条主线不是事后总结，而是唯一能解释全部实验的假设：R2/R5a 证明**数据层是最大单项**（只换记忆表示 ±0.11），
R3/R4 证明**"少一点控制"本身不是答案**（去掉 prompt 块或放开工具裁剪都更差），
而 E-032…E-051 这二十条**全部是"框架断言了它无法知道的事"**——模型据此行动，失败看起来像模型能力问题，实际是框架制造的。

### 五条不变量（controller）

| 编号 | 不变量 | 违反实例（账本条目） |
| --- | --- | --- |
| **I1** | 框架写入 prompt / 工具参数的每个值都必须可追溯到观察（用户原话、档案字段、工具返回）。缺失就**扣住或提问**，不得推演 | E-006、E-012、E-014、E-028、E-039、E-040、E-043、E-051 |
| **I2** | 不可逆动作只由**已结算的可观察证据**触发；授权 ≠ 知道买哪个 | E-016、E-023、E-033、E-035、E-042、E-049 |
| **I3** | 框架不替模型发言，也不堵死模型的合法动作（每一轮至少留一个合法动作，否则必成终局失败） | E-007、E-008、E-010、E-032、E-036、E-048、E-050 |
| **I4** | 只有**用户的硬约束**可以否决动作；其余一律降级为建议或控制信号 | E-020、E-024、E-026、E-045、E-031 |
| **I5** | 学习信号不得编码假设；pure 读取不得有副作用 | E-005、E-013、E-017、E-018 |

### 第二个半边：数据层（D）

| 编号 | 主张 | 证据 |
| --- | --- | --- |
| **D1** | 记忆必须给出**可直接使用的维度结论**，不能只给需要模型自己两跳概括的条目 | R2 0.185 → R5a 0.296；E-003、E-011、E-021、E-022、E-044 |
| **D2** | 每条结论必须带证据与作用域，漂移只在真正的单值同域维度上替换 | E-004、E-027、E-029 |
| **D3** | 事实还要能驱动**写前校验**（ID 溯源、AVOID、日期、地址），不只是进 prompt | E-035、E-043、E-044、E-051 |

### 方法与评测边界（M，不属于上述不变量）

E-001、E-009、E-015、E-019、E-030、E-037、E-042、E-046、E-047、E-053：评测纯度、指标口径、隔离实验与归因口径。
E-053 额外确立一条方法论纪律：**审计得到的相关性不得直接当作瓶颈**——"从不写入的单元通过率低"是症状；只有**预注册的干预实验**才能判定它是不是原因，而该实验给出的答案是"不是"。
凡属 M 的结论一律标注"开发子集测量"，不得表述为 56 用户正式基准。

### 当前审计余项（全部有编号，不再随时新增）

| # | 编号 | 位置 | 现象与计数 | 处理 |
| --- | --- | --- | --- | --- |
| A1 | I3 | `getting_next_message` 兜底文案 | 非 commit（recommend）任务也会输出"无法满足硬约束/没有执行下单"；R9 的 u9/u10/e1/e21 这类单元因此没有用户可见答案 | 待修 |
| A2 | I4 | `ToolErrorLedger` 工具失败护栏 | 可修复的参数（地址）失败两次后永久锁死该写，单元 8 在 R8/R9 都因此终局拒绝 | 待修（改为强制走修复路径） |
| A3 | I4 | `CandidateLedger` 搜索预算 | R8/R9 单元 2 出现 7 次 distinct 查询被拦；但被拦单元多为"谁都拿不到"的单元 | 观察，R10 后决定 |
| A4 | I2 | 学习到的 `force_decision_after_candidates` | 学习信号已修正（E-049），但该控制本身的独立效果从未单独测量 | 待测量 |
| A5 | I1 | `_write_phase_directive` 指名框架候选 | 值可追溯到偏好原子，合规；但它是控制层最后的"强提示"，与"只建议"之间存在差异 | 待测量 |
| A6 | D1 | 画像摘要默认关闭 | 所有已测量配置都显式传 `--profile-summary`，而文档里的标准命令不传 → "默认 ADAPT"少了数据层的一半（实测值 ±0.11） | 与 56 用户命令一起改默认（对齐已测量的配置，不引入新行为） |
| A7 | I1 | `_DATE_RE` 对日期列表只取最后一天 | 「先定30和31号的」只生成 `31号`，卡片把"订到31号"变成硬约束 | **已修（E-052）** |
| A8 | I4 | 派生约束可以无限否决 | J365414 单元 2：103 次同一拒绝、78 步、35 个用户回合、终局拒绝文案重复 12 次直到步数耗尽 | **已修（E-052）** |

### 变更协议（每次改动必须带这四项，否则不进主干）

1. **不变量编号**（I1–I5 / D1–D3 / M）；
2. **代码位置**：被删掉的"凭空断言"具体在哪一行；
3. **可零模型复现的证据**：单元号 + 现场字符串或计数（`scripts/_unit_dialogue.py`、`_unit_events.py`）；
4. **配对测量**：固定装置上该单元是否翻转（同用户同 seed），或明确写"仅机制验证，分数待测"。

**停止规则**：审计余项清空（或全部转为"待测量"）后，**不再动控制层**；下一个杠杆是数据层（D1：预算与归一化，单项实测 +0.11）。
任何新增控制若要进主干，必须指出它恢复了哪条不变量并在配对装置上翻转过至少一个单元；只落在噪声内的改动默认回退。

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

隔离实验（同 2 用户、同 seed、1 trial，`data/simulations/iso_*.json`；R1 = 上表 baseline 行）：

| 轮次 | 配置 | E057330 | E941775 | 合计 |
| --- | --- | --- | --- | --- |
| R1 baseline | stock + RewriteMemory | 0.308 | 0.286 | **0.2963** |
| R2 | stock + ADAPTMemory（条目级） | 0.1538 | 0.2143 | 0.1852 |
| R3 | ADAPT + 仅 stock prompt | 0.0000 | 0.1429 | 0.0741 |
| R4 | ADAPT + 全工具暴露 | 0.0769 | 0.1429 | 0.1111 |
| guard5 | ADAPT 全量（条目级记忆） | 0.1538 | 0.1429 | 0.1481 |
| **R5a** | stock + ADAPTMemory **+ 有界 LLM 画像** | 0.3846 | 0.2143 | **0.2963** |
| R5b | ADAPT 全量 + 画像 | 0.1538 | 0.0714 | 0.1111 |
| R6a | R5b + 保留写阶段完整历史 | 0.1538 | 0.0714 | 0.1111 |
| R6b | R5b + 放大上下文预算 | 0.1538 | 0.0714 | 0.1111 |
| R7 | ADAPT 全量 + 画像（E-048 默认） | — | — | 未完成（见下） |
| **R8** | ADAPT 全量 + 画像 + E-048 + E-049 | 0.1538 | **0.2857** | **0.2222** |
| **R9** | R8 + E-050（未决槽不再是死路） | 0.1538 | 0.2857 | 0.2222 |

R8 逐单元（`data/simulations/iso_R8_choice_settlement.json`，`scripts/_unit_rewards.py`）：

| 用户 | 得分单元 | stock 四次 | R5a（stock+我们的记忆） | R5b（E-048 前） | R8 |
| --- | --- | --- | --- | --- | --- |
| E057330（13 单元） | 1、5 | 0.2885 | 0.3846 | 0.1538 | 0.1538 |
| E941775（14 单元） | 7、12、13、14 | 0.2857 | 0.2143 | 0.0714 | **0.2857** |

两个用户合计（27 单元）：stock 0.2870、R5a 0.2963、R5b 0.1111、R8 0.2222、**R9 0.2222**。
E941775 上 R8 追平 stock，且拿到**没有任何 ADAPT 配置拿过的单元 13、14**（单元 14 是 stock 4/4 全对的单元），
其中单元 12/13 是 `create_instore_product_order` 成功落单——本文件此前记录的"ADAPT 从不出 venue 级写操作（0/20）"这条负债在 R8 中消失了。

R5a 扩展（4 个新用户、1 trial、`iso_R5a_ext.json`，对照为同用户同 seed 的 stock 缓存）：

| 用户 | stock + RewriteMemory | stock + ADAPTMemory + 画像 |
| --- | --- | --- |
| J365414 | 0.3636 | **0.5455** |
| M793481 | 0.2727 | 0.1818 |
| P722245 | 0.3636 | 0.3636 |
| Q089190 | 0.2857 | 0.2857 |
| 合计 | 0.3191 | **0.3404** |

**结论口径**：ADAPT 目前约为 baseline 的 50–75%（单 trial，方差约 ±1 个单元）；
`guard5` 相对 `guard4` 的下降无法与噪声区分，因此**不声称 E-045 带来增益**。
R6a/R6b 与 R5b 完全同分（三条轨迹互不相同），**推翻"prompt 税/上下文裁剪"假设**：裁剪不是亏损来源。
R5a 在 6 个用户上的净差为 +0.016（±1 单元的噪声量级）→ 记忆改造达到**持平**，**不声称超过 baseline**。

R7（ADAPT 全量 + 画像，即 E-048 默认配置）在跑到 `E057330` 第 8/13 单元时按要求终止，前缀为 2/8 全对
（同用户 `R5b` 全程 2/13）；`E941775` 未开始，**因此 R7 没有用户级分数，只有前缀观测**。
前缀观测到的行为变化是：模型自己提问"你想选哪款？还是就来最经典的瑞士莲牛奶巧克力100g（¥29.9）？"，
用户回答"随便，你看着办吧"——即 E-048 之后模型确实会先问再决定，这正是 E-049 要固化的形状。

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
- **隔离结果（同 2 用户、同 seed、1 trial，`data/simulations/iso_*.json`）**：

  | 变体 | prompt | 门禁 | 记忆 | E057330 | E941775 | 合计 |
  | --- | --- | --- | --- | --- | --- | --- |
  | baseline | stock | 无 | RewriteMemory | 0.308 | 0.286 | **0.296** |
  | R2 | stock | 无 | ADAPTMemory | 0.154 | 0.214 | **0.185** |
  | R3 | 仅 stock（去全部附加） | 有 | ADAPTMemory | 0.000 | 0.143 | 0.074 |
  | R4 | stock + ADAPT 附加 | 全暴露 | ADAPTMemory | 0.077 | 0.143 | 0.111 |
  | 全量 ADAPT | stock + ADAPT 附加 | 有 | ADAPTMemory | 0.154 | 0.143 | 0.148 |

- **结论（两处推翻本条目最初的假设）**：
  1. **记忆表示是主因**：仅替换记忆后端（R2，其余全为 stock）即从 0.296 掉到 0.185，约 0.11 的差距来自记忆；
  2. **"prompt 税"不成立**：去掉全部 ADAPT 提示词附加反而更差（R3 0.074 < 全量 0.148）——决策卡/运行时状态/账本在**帮助**模型；
  3. **"门禁税"不成立**：全工具暴露更差（R4 0.111 < 全量 0.148），阶段裁剪有净收益；
  4. 同记忆下 stock agent（0.185）仍高于我们的 agent（0.148），残余约 0.04 来自控制层本身。
  → **保留控制层，替换记忆表示**。
- **有效方案（据此实施）**：`ADAPTMemory(enable_summary_rewrite=True)` 维护 LLM 画像并在 `read()` 中以**有界块**（默认 800 字）置顶注入；归纳提示词改为输出**可复用维度**（颜色/风格、口味与过敏、出行方式与等级、常去区域、服务、价格），具体商品与商家名仅在重复出现或代表维度时保留。runner 侧开关 `--profile-summary` / `--summary-max-chars`（默认关闭，待 R5 配对测量）。
- **适用边界**：隔离开关只用于对照实验，不改变默认行为；产出的结论用于决定下一轮做哪一项结构性改造。
- **后续风险/下一步**：按隔离结果排序改造优先级（预期：① 记忆回到"有界 LLM 归纳 + 候选接地过滤"；② 去 prompt 税并恢复完整历史；③ 全工具暴露、只保留可修复校验）。**验收规则**：任何改动必须在固定配对装置上胜过对照，否则默认回退。
- **能力抽象**：preference extraction / utilization / long-horizon consistency。

---

## E-047：写阶段上下文替换被怀疑为"prompt 税"，实测被推翻

- **日期**：2026-09-11
- **状态**：SUPERSEDED（假设不成立，开关保留为隔离装置）
- **通用性判定**：`GENERAL-EMPIRICAL`。同用户同 seed 配对实验。
- **难点**：E-046 的结论把差距归给"表示 + 自由度"，其中一项假设是：`_generation_messages` 在 `READY_TO_CREATE/READY_TO_PAY` 用"system + 最后一条 user + 控制器指令"**替换**整段历史，等于拿走模型自己的工具观察，因此模型在写阶段失忆。
- **证据**：`data/simulations/iso_R6a_keep_history.json`（保留完整历史）与 `iso_R6b_big_context.json`（把 `ADAPT_CONTEXT_BUDGET_CHARS` 提到 200000、工具消息上限提到 100000）与 `iso_R5b_adapt_summary.json` **三者同为 0.1111**，但三条轨迹互不相同（同分不同路径）。
- **根因（修订后）**：写阶段上下文裁剪不是亏损来源；三条轨迹的单元级同分说明亏损集中在"候选选择/实体正确性"这一层，而不是上下文容量。
- **有效方案**：`--keep-write-phase-history` 与上下文预算环境变量保留为隔离开关；默认行为不变。
- **验证**：`agent/tests/test_isolation_rig.py`；`scripts/_wire_focus.py` 记录两个开关确实改变了送进模型的消息序列（否则"同分"只是开关没生效）。
- **适用边界**：只在 dev 子集上验证过；不改默认值。
- **后续风险/下一步**：上下文容量问题可能在更长序列（56 用户全程）出现，需要在正式基准上复测一次。
- **能力抽象**：long-horizon consistency。

---

## E-048：框架替模型说话（罐头提问 + 推荐定稿）在"先问后做"的单元上净负

- **日期**：2026-09-11
- **状态**：PARTIAL（机制已实现并被前缀观测支持，尚无用户级增益证据）
- **通用性判定**：`GENERAL-EMPIRICAL`。证据来自 5 个"stock 赢、ADAPT 输"单元的逐单元轨迹对比。
- **难点**：把 stock 赢、ADAPT 输的 5 个单元拉出来逐条看，stock 的路径是"问口味/问地址/问时间 → 给对比 → 拿到确认 → 下单"；ADAPT 的路径是"直接下单"或者"框架代替模型说话"（`_framework_question` 罐头维度问题、`_framework_recommendation` 直接定稿 DONE）。框架发言把模型自己的澄清问句挤掉了。
- **根因**：`framework_speech=True` 时框架在模型之前产出用户可见文本：罐头问题没有候选上下文，推荐定稿直接结束子任务（`phase=DONE`），两者都拿走了"用户看到选项后再补充约束"的机会。
- **有效方案**：`framework_speech` 默认 `False`——不再有框架罐头提问，也不再有"推荐→DONE"；提问与推荐由模型自己产出。同时把问题门禁改为"只拦重复"：`QuestionGate` 只否决重复维度、未回答挂起问题、已委托/直接成交的候选问题、学习到的策略；**不再否决模型的新问题**。
- **验证**：`agent/tests/` 351→365 单测（含新 `test_choice_settlement.py`）；R7 前缀观测到模型自己提问、用户回答"随便，你看着办吧"。
- **适用边界**：`--framework-speech` 保留旧路径用于隔离对照；推荐类任务的收尾现在完全依赖模型发言。
- **后续风险/下一步**：推荐类任务若模型不发文字，可能出现"无输出"轨迹（`_finalize_visible_trajectory` 的 `missed_write` 只统计成交类）。
- **能力抽象**：proactiveness / execution。

---

## E-049：下单授权被当成"已经知道买哪个"，任何候选一到就强制 CREATE

- **日期**：2026-09-12
- **状态**：PARTIAL（配对测量 R8 已完成：同用户上 +0.111；单个用户上无变化）
- **通用性判定**：`GENERAL-EMPIRICAL`。结论来自 5 个失败单元的路径对比 + 5 处独立代码路径的语义一致性审查，不读 rubric。
- **难点**：用户说"帮我买X"，`TaskSpec.compile` 会把 `action=commit` 同时写成 `create_authorized=True` 和 `candidate_choice_authorized=True`；于是只要搜索返回任何候选，`observe_candidates()` 立刻把 phase 推到 `READY_TO_CREATE`——该阶段**只暴露 CREATE 工具**，并附上"立刻调用 CREATE、不要提问、不要搜索"的控制器指令。模型因此**没有合法动作去问"要哪种口味/送到哪"**，只能凭记忆猜一个候选下单。而 action evaluator 用的是 `min(trajectory, action)`，选错实体就是 0 分。
- **证据**：
  1. 路径对比：stock 赢的单元全部是"先问后做"，ADAPT 输的单元是"直接做"或"框架替模型做"。用 `scripts/_unit_dialogue.py` 读 R7 前缀（E057330 前 8 个单元）可直接看到：单元 2 搜完鲜花后**没有问**，直接选"粉色康乃馨+百合"下单；单元 3/4/7/8 同样是"搜完立刻选一个下单"；同一前缀里唯一得分的单元 5 也是同一形状（说明这不是"必输"，而是**把可用信息丢掉后赌一把**）；
  2. 单元 8 的失败链更直接：模型下单 → 框架拒绝并发出终局文本"现有候选无法满足硬约束，我没有执行下单。" → **之后**用户才回答"随便，你看着办吧"。框架在用户仍愿意接受服务时单方面终止了子任务；
  3. 旧语义被 4 处独立代码写死：`state.observe_candidates`、`state.observe_user`、`question_gate`（`candidate_choice_authorized` 直接否决候选问题）、`_write_phase_directive`（框架指定候选人并要求立即落单）；
  4. 反向激励：`_finalize_visible_trajectory` 把"有可执行候选但没下单"记为 `missed_write`，编译成 `force_decision_after_candidates` 策略，下一子任务里**重新武装**同一行为；
  5. 相关成本：一次两用户 trace 里 21 次 preflight 拒绝中有 6 次来自学习到的"最大偏好覆盖"硬否决（每次拒绝耗一轮重规划，有时走到终局拒绝）。
- **根因**：把两件事混为一谈——"用户授权我花钱"（authorization）与"我知道要买哪一个"（choice）。前者是用户给的，后者必须由**可观察证据**给出：用户选择、用户回答、用户委托、判别性偏好证据、或只有一个合规候选。
- **尝试过但无效的方案**：（1）只把偏好领先者降级为建议（E-045）——领先者不再锁死，但 phase 仍被强制推进；（2）只让框架不发言（E-048）——模型获得了发言机会，但 phase 仍可能已进写阶段，CREATE 一暴露就没有回头路。
- **有效方案**：引入单一判定 `TaskRuntime.choice_settled() -> (bool, source)`，把"阶段推进"和"问题门禁"都挂在它上面：
  1. **阶段**：`_maybe_promote()` 是唯一能进入 `READY_TO_CREATE` 的入口，条件为 `create_authorized ∧ execution_ready ∧ candidates_seen ∧ choice_settled`；
  2. **settled 的五个可观察来源**：显式选择/序号、`candidate_choice`/`preference_choice` 维度的**提问已被回答**、用户委托（随便/你看着办）、判别性偏好领先者（`unique_evidence_leader`，严格高于所有次席且非零）、只有一个合规候选；
  3. **有界逃逸（防 E-033 类活锁）**：问题预算用尽，或在 SELECT 里连续两次模型生成仍未settle，则记为 `bounded select budget` 并推进——写入永远可达；新的用户回合重置该计数；
  4. **门禁对称**：候选类问题在**未 settle 时允许**（这正是 stock 赢的形状），settle 之后拒绝（"再问也改变不了答案"）；`execution_ready=False` 时拒绝（答案无法被执行，先补实体）；
  5. **学习信号修正**：`missed_write` 只在 `choice_settled` 后仍不下单时记录，杜绝用"任何候选都得下单"反向武装；
  6. **硬否决 → 控制**：学习到的 `require_max_preference_coverage` 不再否决写入，改为在写阶段**指名最大证据覆盖候选**；`validate_ranked_choice` 只保留"用户显式选择"这一条硬否决，排名位置越界只记 `shortlist_position_diverged`。
- **验证**：`agent/tests/test_choice_settlement.py`（14 个新单测：六个 settle 来源、门禁对称性、有界逃逸、显式选择仍硬锁、位置越界不再否决、`missed_write` 只在 settle 后学习、低覆盖写入不再被否决）；全量 **365 单测通过**；`python -m compileall -q agent` 通过；`git -C evaluation/vitabench diff --exit-code HEAD -- src/vita` 通过。
- **配对测量（R8 = E-048 + E-049 组合，`data/simulations/iso_R8_choice_settlement.json`）**：
  - `E057330` 0.1538（2/13），与 R5b 同分同单元 → 该用户上没有变化；
  - `E941775` **0.0714 → 0.2857（1/14 → 4/14）**，追平 stock，并拿到单元 13、14（此前无任何 ADAPT 配置拿过）；
  - 单元 12/13 由 `create_instore_product_order` 落单成功——此前记录的"venue 级写操作 0/20"负债消失；
  - 单元 13、14 都出现 `preference_leader_diverged` 且**得分为 1.0**，即"领先者降级为建议"确实没有阻止正确选择；
  - 但 R8 仍有三处 `question gate: a question is already waiting for the user's answer` 造成的零工具调用死路（见 E-050），其中 `E941775` 单元 2 是 stock 4/4 的单元，说明 E-049 的收益被 E-050 掩盖了一部分；
  - **口径**：R8 相对 R5b 同时包含 E-048 与 E-049（及各自的伴随改动），不是 E-049 单项的贡献。
- **适用边界**：只对 `create_authorized` 的成交类子任务生效；推荐类任务（无写授权）行为不变，问题门禁不介入。settle 的每一个来源都必须来自可观察证据，不含任何 user/task/candidate 特例。
- **后续风险/下一步**：多一次澄清往返可能消耗 `max_steps`（长序列末尾尤其），需要用 R8 的单元轨迹确认；若某单元因"多问一轮"而超步，需要把问题预算进一步下调。
- **能力抽象**：execution / missing information detection / preference utilization。

---

## E-050：E-048 撤掉框架提问后，NEED_INFO 变成硬死路（无工具、无提问、必有终局拒绝）

- **日期**：2026-09-12
- **状态**：OPEN（机制已由单元轨迹与单测锁定；配对测量 R9 待跑）
- **通用性判定**：`GENERAL-EMPIRICAL`。结论来自 R8 逐单元 debug sidecar 与对话轨迹，可零模型复现。
- **难点**：R8 里 `E057330` 单元 6（`instore/wellness`「又得去理发店了，帮我买个套餐。」）与 `E941775` 单元 2（`ota`「想订张机票去旅游，你有什么好地方推荐吗？」，**stock 4/4 全对**）都是：一次工具调用都没有（单元 6 只有一次 `query_preference_memory`），直接输出终局文本"现有候选无法满足硬约束，我没有执行下单。"，用户随后 `###STOP###` 结束。单元 2 的日志里可以看到同一句被拒三次：
  `question gate: a question is already waiting for the user's answer`。
- **根因（三处叠加，全部可零模型复现）**：
  1. `TaskSpec.compile` 判定该指令有未决的关键槽（单元 6 = `['time']`，单元 2 = `['departure','date','quantity']`），`TaskRuntime.begin` 因此把 phase 直接置为 `NEED_INFO`；
  2. `QuestionGate` 当时按 **phase** 判断"已有问题在等答案"，于是把模型提出的第一个问题也拒掉——但此时 `pending_question_dimension == ""`，**根本没有任何问题被发出过**；
  3. `allowed_tools` 在 `NEED_INFO` 只放行 READ 角色工具，SEARCH 全部隐藏，CREATE 也隐藏。
  → 模型在该阶段**没有任何合法动作**：提问被拒、不能搜索、不能下单。三次 preflight 拒绝耗尽重规划预算后落到终局拒绝文案。
- **为什么是 E-048 引入的**：E-048 之前，框架自己会用 `_framework_question()` + `_GAP_QUESTIONS`（"请告诉我出发地。"）把这类槽问掉。同一单元在 R5b（E-048 之前）的轨迹是：`请告诉我出发地。` → 用户"随便吧，你看着办。" → 搜航班 → 下单（该单元仍因实体不符得 0，但**有完整动作链**）。E-048 关掉框架发言后，原本唯一的"合法动作"消失了，而门禁与工具裁剪仍在假设"框架会问"。
- **证据**：`data/simulations/iso_R8.log`（两个单元的完整对话与三次同因拒绝）、`data/simulations/iso_R8.jsonl`（单元 6 只有 1 次 tool_result、0 次 question_committed）、`scripts/_unit_events.py`、`scripts/_unit_dialogue.py`；对照 `data/simulations/iso_R5b.log` 同单元。
  R8 中同一死路共出现 **3 次**（`scripts/_unit_events.py` 逐单元可见三连 `question gate: a question is already waiting`）：
  `E057330` 单元 6（`time` 槽；stock 0/0/0/0）、`E941775` 单元 2（`departure/date/quantity` 槽；**stock 4/4**）、`E941775` 单元 10（"帮我订张这周五的动车票"；stock 0/0/0/0）。
  其中 `E941775` 单元 2 是 E-049 本可以得分、却被本死路吃掉的那一格。
- **有效方案**：
  1. `QuestionGate`：`NEED_INFO` 只在**确实有问题挂着**（`pending_question_dimension` 非空）时拒绝新问题；由未决槽导致的 `NEED_INFO` 允许模型自己提问——这正是"缺失信息检测"能力本身；
  2. `ToolRegistry.allowed_tools`：`NEED_INFO` 同时放行 SEARCH（观察不等于承诺），只保留不可逆写操作隐藏；
  3. 兜底文案：重规划耗尽且槽仍未决时，输出该槽的问题（`_GAP_QUESTIONS`，与框架发言路径共用同一词表）而不是"无法满足硬约束"这种与事实不符的拒绝；没有具体槽时回到 `SEARCH` 继续观察。
- **验证**：`agent/tests/test_open_slot_questions.py`（5 个单测：未决槽起始 NEED_INFO 且无挂起问题、模型可提问、已提交问题仍拦第二次、"NEED_INFO 仍暴露 SEARCH 但隐藏 CREATE"、回答后离开 NEED_INFO）；全量 **371 单测通过**；`compileall` 与 vendored 纯净检查通过。
- **配对测量（R9，`data/simulations/iso_R9_open_slots.json`）**：
  - 死路**类**被清零：整轮 27 个单元中 `a question is already waiting` 出现 **0 次**（R8 为 3 次），三个原本零工具调用的单元现在都产生了完整动作链；
  - `E941775` 单元 2（stock 4/4）按预测**转为 1.0**：模型问"时间"槽 → 用户委托 → 搜航班 → `create_flight_order`；
  - 但用户级总分不变（合计仍 0.2222）：同样的 1 单元损失在别处出现——`E941775` 单元 13 从 1.0 变成 0.0，机制完全相同（`unique preference-evidence leader` 结算 + `create_instore_product_order`），差别只在选中的候选，属单 trial 噪声量级（±1 单元）；
  - 新增可观测性（`choice_state` 事件）显示结算来源已按预期工作：单元 17/18/25/26/27 由 `unique preference-evidence leader` 结算、单元 15/19 由用户委托结算、`E057330` 单元 13 由学习到的 force-decision 策略结算；单元 20 出现 5 次 `question budget exhausted` 拒绝但仍得分。
  - **结论口径**：E-050 消除的是一整类**确定性的零工具调用失败**（机制已验证），其分数效应在当前 2 用户 1 trial 装置上被同量级的噪声掩盖（+1 单元 / −1 单元）。要给出分数结论需要多 trial 或更宽用户范围。
- **适用边界**：只放宽"由未决槽引起的 NEED_INFO"；真正挂起的问题、重复维度、预算耗尽仍然拦截。终局拒绝文案只保留给"确实存在候选但都不合规"的情况。
- **后续风险/下一步**：`_dimension_for` 的维度词表较粗（"从哪出发"会落到 `candidate_choice`），可能出现"同一维度只能问一次"过严；R9 的单元轨迹会显示是否需要细化。另外单元 12 的终局拒绝来自"没有任何航班候选"，需要在 R9 复查是否仍走到拒绝。
- **能力抽象**：missing information detection / proactiveness / execution。

---

## E-051：框架自己编造了无法解析的地址，并把"送个东西到家"编译成推荐任务

- **日期**：2026-09-12
- **状态**：OPEN（机制已由单测与离线编译锁定；配对测量 R10 进行中）
- **通用性判定**：`GENERAL-EMPIRICAL`。两个失败单元都可用零模型编译复现，且分别对应 stock 3/4 与 4/4 的单元。
- **难点（两个独立缺陷）**：
  1. **地址别名被当成字面地址**：`E057330` 单元 8「今天中午还是吃粉，给我点个粉送到单位来吧。」→ `_ADDRESS_RE` 把"送到"之后的文本整段捕获为 `单位来`（只剥了句尾"吧"），于是决策卡写成 `MUST: address=单位来` 且 operator=`contains`。模型照抄 `"address": "单位来"`，环境 `address_to_longitude_latitude` 抛 `Longitude and latitude not found for address 单位来`，重试同一参数被工具失败护栏拦下，子任务以终局拒绝结束。**stock 在 3/4 次试验中解出该单元。**
  2. **"送"不是交易动词**：`E057330` 单元 10「好热，给我送个奶茶的到家来。」→ `_ORDER_VERB` 里没有"送"，`is_transaction_request` 为假，`TaskSpec.action` 编译成 **recommend**，`create_authorized=False`。模型给出了正确的对比表、接着宣布"我直接帮你下单了"，却被**我们自己的授权门禁**判为不允许写，随后反复读商品详情（23 次工具调用）而零产出。**stock 在 4/4 次试验中解出该单元。**
- **根因**：两处都是"框架的表示层说谎"——一处把用户的地点别名变成环境无法地理编码的字面串，一处把明确的履约请求降级成信息咨询。模型在这两种情况下都按框架给的错误前提行动，失败看起来像模型能力问题，实际是框架造成的。
- **证据**：`data/simulations/iso_R9.log`（单元 8 的 `单位来` 报错与后续护栏拒绝；单元 10 的对比表 + "我直接帮你下单了" + 23 次工具调用）、`data/simulations/iso_R9.jsonl`（单元 8：`create proposals 2`、`tool failure guard` ×4；单元 10：无 create 提案）、`scripts/_unit_events.py`、`scripts/_unit_dialogue.py`。
- **有效方案**：
  1. `_ADDRESS_RE` 之外新增 `_ALIAS_ADDRESS_RE`（`到|去|在` + 家/公司/单位/宿舍/办公室…），并引入 `_strip_address_particles`（剥句尾语气词与"来/去/就行/谢谢"）与 `_address_alias`：捕获串若是**地点别名**（可带 ≤3 字后缀）就解析成 `home`/`company` 并改用 `resolves_profile`；带结构特征（路/街/号/楼/小区/大厦…）的才保留为字面地址。`送到家乐福超市门口`、`送到郑州市金水区国基路166号` 保持字面，不被"家"劫持。
  2. `_ORDER_VERB` 加入"送"。对象要求使 `帮我送到家`、`有没有送货服务` 仍然**不**构成交易请求。
  3. `_normalize_address_call` 对**别名形态**的约束值一律经 `profile_address` 解析（不再只认 `resolves_profile`）；当工具 schema 声明了地址参数而调用**缺**该参数时也补齐（新增 `ToolMeta.argument_names` 提供 schema 全字段，避免给不接受地址的工具塞参数）。
- **验证**：`agent/tests/test_address_alias_and_delivery_intent.py`（7 个单测：交易意图、信息请求不授权、`单位来`→company/`resolves_profile` 且卡片不再出现"单位来"、写参数被修复为注册单位地址、`到家`→home、真实地址不被劫持、旧 `contains` 卡片仍能解析）；全量 **378 单测通过**（其中 2 个既有测试按新契约更新：送货写在声明了地址参数时必须带地址）。
- **适用边界**：别名表只含注册档案里确实存在的概念（家/公司/单位/宿舍/办公室/学校）；带结构标记的捕获串永不改写。仅影响 delivery 类带地址参数的写操作与含"送+对象"的交易判定。
- **后续风险/下一步**：`送` 进入动词表后，含"送"的咨询句需要观察是否被误判（已加两条反例单测）；R10 在 4 个新 dev 用户上验证整体。
- **能力抽象**：preference-to-action grounding / execution / missing information detection。

---

## E-052：派生约束可以无限否决——一个日期解析错误烧掉整个局面

- **日期**：2026-09-12
- **状态**：PARTIAL（机制已由单测锁定；R11 待跑）
- **通用性判定**：`GENERAL-EMPIRICAL`。同一指令可零模型编译复现，R10 提供了完整现场计数。
- **难点**：R10 首个用户 `J365414` 单元 2「后天要去遵义喝朋友的喜酒，顺便在那玩几天，你给我在会议会址3km范围内订个酒店吗，**先定30和31号的就行**」（子任务时间 2026-01-28）：
  - `_DATE_RE` 的 `\d{1,2}号` 只在"30和31号"里匹配到 **31号**，"30"被丢掉；
  - 于是决策卡把"必须订到 31 号"当成硬约束，而模型合理地先订 30 号（房间产品的 `date` 是每晚一条）；
  - 每次写操作都被 `selected candidate does not satisfy required date: 31号` 否决 → 同一拒绝 **103 次**、**78 步**、**35 个用户回合**，终局拒绝文案对用户重复约 12 次，直到 `max_steps` 耗尽；用户两次坚持"那不行，你看着办，必须得在3km范围内，30号和31号都要有"。
- **根因（两层）**：
  1. **I1**：把一个"多值需求"截断成单值硬约束，且表示形式（`31号`）与观察形式（`date=2026-01-30`）不同层——框架在用自己造的、无法满足的约束否决模型；
  2. **I4**：否决没有上限。任何派生约束（正则抽取、排序分数）都可能永久不成立，而"永久不成立"在实现上表现为无限重规划 + 每回合同一段终局文案。
- **证据**：`data/simulations/iso_R10.log` 单元 2 段（103 次同因拒绝、35 个用户回合、12 次重复拒绝文案）、`scripts/_unit_events.py`；`_constraint_present('31号','date=2026-01-30')` 为假、`'date=2026-01-31'` 为真的离线复现。
- **有效方案**：
  1. 新增 `_DAY_LIST_RE`（`30和31号`、`30、31号`…）：日期列表按**多值需求**处理，生成 `hard=False` 的日期约束，并在做单日匹配前把该片段从文本中屏蔽，避免再产生一条单日硬约束；
  2. `_preflight` 统一过一道 `_cap_repeated_vetoes`：**派生约束**（"does not satisfy required… / does not show required value… / has observable preference score…"）在同一子任务内第 4 次重复即停止否决，并记 `constraint_veto_capped` 事件与 lesson；
  3. 明确**不可上限化**的事实类否决：ID 溯源、AVOID、授权、重复写、工具失败护栏、工具阶段限制——它们要么是环境事实，要么是用户硬约束；
  4. 兜底文案独立为 `_fallback_message()`：若本轮唯一的拦截来自已上限化的派生约束，不再输出"无法满足硬约束"这种假拒绝，而是回到 SELECT 让模型继续（E-050 的槽未决分支保留在前）。
- **验证**：`agent/tests/test_unbounded_veto.py`（7 个单测：日期列表两条且非硬约束、只覆盖其中一晚的房型可通过、ID 溯源仍被拦、第 4 次重复被上限化并记录、五类事实否决永不被上限化、上限化后兜底不再假拒绝、真约束仍拒绝）；全量 **385 单测通过**。
- **适用边界**：只给**派生**约束加上限，且上限只影响"继续否决"这一动作；硬约束与事实校验原样保留。
- **运行观测（R11，同指令同用户，被中止前的两个单元）**：`J365414` 单元 2 从 R10 的 **78 步 / 103 次同因拒绝 / 35 个用户回合 / 12 次重复假拒绝**，变为 **17 条消息**：读完 6 家酒店后选中 `遵义全季酒店(会议会址红花岗店)` 并**成功 `create_hotel_order`**（1 月 30 日入住），没有出现任何重复否决。该单元得分仍为 0.0（评分还要求覆盖 31 日等条件），因此这是**机制验证，不是分数证据**；R11 因人工中止未跑完，日志产物已清理，数字取自运行输出。
- **后续风险/下一步**：多晚预订本身仍只完成其中一晚（框架无法在一次 CREATE 里表达两晚）；R11 需确认该单元不再烧步数、用户不再被重复假拒绝。
- **能力抽象**：long-horizon consistency / execution。

---

## E-053：落地守卫被否证——"从不写入"是症状，不是瓶颈

- **日期**：2026-09-12
- **状态**：`SUPERSEDED`（假设被受控干预实验推翻）。机制保留为 `--landing-guard` 可选开关，**默认关闭**，不再作为主线。
- **通用性判定**：`GENERAL-EMPIRICAL`。结论来自**预注册的单用户配对干预实验**，只用可观察的工具调用、末尾消息类型、终止原因与步数；reward 仅用于配对分桶，不作学习信号。
- **难点**：`stock_avg4_8u.json`（8 dev 用户 × 4 trial = 400 单元）的离线审计给出一个很强的相关性：

  | 末尾消息类型 | n | 通过率 | 写入率 |
  | --- | --- | --- | --- |
  | 完成声明（"已下单/订单号"） | 161 | **0.441** | 100% |
  | 确认请求（"确认一下？"） | 84 | 0.190 | 26% |
  | 推荐/列表 | 155 | 0.194 | 57% |

  340/400 单元零模型编译为 `action=commit`。其中 **53 个单元以确认请求收尾、从未写入任何工具调用，通过率 0.057**，而同批"写入后声明完成"的同侪通过 **0.427**。若全部按同侪比率转化，上限 **+0.049** —— 几乎等于 0.2925 → 0.35 的缺口。当时据此推断：**模型已选定实体却把执行写成问句，用户模拟器读作"服务完成"即发 `###STOP###`，于是订单从未创建**；用户模拟器提示词确实写着"明确给出推荐后表示接受"、"满意时生成 STOP"。
- **预注册预测**（跑之前钉死，事后不得改口）：选 **P722245**（11 单元，其中 3 个目标型失败在 4 个 trial 里稳定复现，为 8 个 dev 用户中最多）。预测 `sub_P722245_1` / `_3` / `_5` 上守卫开火、三单元 0 → 1。判读表同样预注册：翻转且 `accepted: true` 才算机制成立；**翻转但无事件不算**；**未翻转但有事件 = 落地不是瓶颈**；未翻转且无事件 = 目标桶不稳定。
- **证据**（`data/simulations/pair_P722245_B_landing.json`，对照为 `stock_avg4_8u.json` 同用户 `trial 0`，两侧同为 `trial_seed=42`；`scripts/paired_arms.py --trial 0`）：

  | 指标 | 基线 | 守卫臂 |
  | --- | --- | --- |
  | 通过 | 4/11 = 0.3636 | **3/11 = 0.2727** |
  | Δ | | **−0.0909** |
  | 修复 / 打断 | | **0 / 1** |
  | 守卫开火 | | **5 次（全时序型，4 采纳 / 1 拒）** |

  **三个预注册单元全部落入了预注册的第三行**：

  | 单元 | 基线 | 守卫臂 |
  | --- | --- | --- |
  | `sub_P722245_1` 绵阳车票 | 确认式，未写入，0 | **完成声明，已写入，仍 0** |
  | `sub_P722245_3` 西安酒店 | 确认式，未写入，0 | **已写入，仍 0**，121 条消息、`max_steps` 崩掉 |
  | `sub_P722245_5` 养生团购券 | 确认式，未写入，0 | **已写入，仍 0**，`agent_stop` |

  **机制按预测方向生效（三个单元从"从不写入"变为"写入"），分数一动不动。**
- **根因（修正后）**：原推断把相关性当成了因果。**"从不写入"是症状，不是原因。** 模型之所以问"确认一下？"，恰恰因为它**不确定选中的实体是否合规**；逼它执行不会让它选对，只是把"没写"换成"写错"。最干净的反证是 `sub_P722245_1`：两次 rollout 选中**同一个实体 G8701**，基线没下单得 0，守卫臂下了单也得 0 —— 守卫把一个 0 换成了另一个 0。
- **尝试过但无效的方案（本条目本身就是该方案）**：在 stock 骨架上加一层"执行时序"薄层（`agent/landing.py`，`LandingGuardAgent`）：成交类指令 + 已观察候选 + 尚未写入 + 消息以确认请求收尾 → 用一条执行时序指令**有界重生成一次**；重试未变好则保留原答案；指令只进本次生成的局部消息、不写入对话状态。设计上刻意只改"什么时候做"、不改"做什么"，且**独立复现了 E-049 的陷阱**：拿不确定的实体去写，撞上 `min(轨迹分, 动作分)`。E-042 记下的 `LOST 7 : GAINED 1` 形态在本轮再次出现（0 修复 / 1 打断）。
- **有效方案**：无。守卫降级为可选开关，默认关闭；不再在"让写入发生"这个方向上投入。
- **验证**：`agent/tests/test_landing_guard.py` 20 个零模型单测（含"关闭时与 stock 逐字段等价"、指令不进状态、重试无改善则保留原答案、无候选则弃权）；全量 **405 单测通过**；`compileall` 与 `git -C evaluation/vitabench diff --exit-code HEAD -- src/vita` 通过。
- **S 阶段冒烟中发现的实现缺陷（已修）**：`vita/orchestrator/orchestrator.py` 只在一轮发出多个工具调用时才包 `MultiToolMessage`，单次调用送来的是**裸 `ToolMessage`**。首版只处理前者，导致 `candidates_seen` 恒为 false、守卫在真实运行里**完全不触发**——而 18 个单测因为都手工构造 `MultiToolMessage` 而全绿。修复后真实冒烟中 `candidates_seen` 由 `false` 变为 `true`，并补了两个裸 `ToolMessage` 回归测试。**教训：单测自造投递形状会掩盖真实投递路径。**
- **未取证的其余一半**：同一机制里的搜索预算分支（`search_budget=6`）在本轮 **0 次预算型开火**，因此"搜索 thrash 压死长单元"这条同样**没有干预证据**，不得当作有效。
- **适用边界**：结论覆盖 1 用户 / 1 trial / 11 单元，总分差 −0.0909 在统计上不显著（单对不一致，符号检验 z = −1.00）。**但机制证据强于分数证据**：三个目标单元在预测方向上改变行为而分数完全不动，这一组合本身就否证了"落地是瓶颈"。任何在更多用户上重跑该机制的行为必须按本条目预期为负或零。
- **已知可观测性缺陷**：`states["landing_guard"]` 每模拟只写一份，事件**没有按子任务打标**，因此"哪几次开火落在哪些单元"无法从产物恢复（`scripts/paired_arms.py` 已做去重并在输出中提示；曾因重复累加把 5 次报成 55 次）。逐单元归因能力待补。
- **后续风险/下一步**：真正的杠杆转向**写前实体校验**（D3：事实必须驱动写前校验）。靶子现成且已取证：`sub_P722245_1` 里首次按用户日期检索返回 G8515，模型却在第二次单独查询后绑定到 G8701——**绑定到了错的候选**，这与 E-051「框架的表示层说谎」同属 I1/D3 类。守卫的副作用也需盯：每次开火多一次生成，`sub_P722245_3` 退化到 121 条消息并撞 `max_steps`。
- **能力抽象**：execution / preference-to-action grounding（**作为反例**：证明"促成动作"本身不构成能力增益）。

---

## E-054：测量装置本身测不出结论——伪重复，以及用错方差分量

- **日期**：2026-09-13
- **状态**：`VERIFIED`。这是对**测量本身**的更正，不是对某个 agent 机制的判断；结论由 vendored 源码与零模型命令直接确定，不含假设。
- **通用性判定**：`GENERAL-FORMAL`（单元定义与方差分解）；受影响的**具体数值**为 `GENERAL-EMPIRICAL`，仅适用于 8 dev 用户 cohort。
- **难点**：本仓长期在错误的单元与错误的分母上做比较，导致过去所有 8 用户结论（无论正负）都不可判读。具体三项：

  1. **官方单元被弄错。** 官方指标 `_compute_subtask_pass_metrics`（`vita/metrics/agent_metrics.py:170`）的 docstring 明写 *"each (task_id, subtask_index) is treated as an independent evaluation unit observed across num_trials"*，`average_at_k`（`:113`）对该单元的 trial 取简单均值。所以官方单元是 **`(task_id, subtask_idx)`，本 cohort 为 100 个**，每个单元挂 `num_trials` 个二值奖励。`scripts/_official_metrics.py` 直接打印 `evaluation units (task_id, subtask_idx): 100`、`Avg@4=0.2925` 确认。
  2. **把单元内的复制当成了单元（伪重复）。** 4 个 trial 重放**同一脚本**（subtask 数、开场指令、`opensig` 哈希跨 seed 42/43/44/45 完全一致），且 `temperature` 对 agent/evaluator/user 三个模型全局为 `0.0`。因此 `(user, trial, subtask)` 的 400 条记录是 100 个单元的 4 次**内部重复观测**，不是 400 个独立观测。本仓曾按 400 计数，于是 N 虚增 4 倍、显著性同步虚增。
  3. **用用户内分量当用户级精度。** `between_user_sd = 0.0823`，`within_user_sd = 0.0573`。旧判据引用的 ±0.0203 是**用户内 trial** 分量（衡量"同一用户重复抽样"），而 promotion 问的是"换一批用户是否仍成立"，那是 `between_user_sd`。**加 trial 只缩小前者，永不缩小后者。** 8 用户真实非配对 2·SE = **±0.0582**。
- **证据**（全部零模型可复现）：

  ```powershell
  python scripts/noise_floor.py data/simulations/stock_avg4_8u.json   # 四层计数 + 两分量 + 两种底
  python scripts/_official_metrics.py                                 # 100 单元 / Avg@4=0.2925
  ```

  | 量 | 值 |
  | --- | --- |
  | 用户 / simulation 记录 / subtask-trial / 官方单元 | 8 / 32 / 400 / **100** |
  | `between_user_sd` / `within_user_sd` | 0.0823 / 0.0573 |
  | 8 用户非配对 2·SE（真实） | **±0.0582** |
  | 用户内 trial 2·SE（旧判据误用） | ±0.0203 |
  | 4 个 trial 分数完全相同的用户 | **3 / 8** |
  | 分辨 Δ=0.05 / 0.03 / 0.02 所需用户数 | 11 / 31 / **68** |

- **一个具体后果（本条目的直接反例）**：同一轮分析里按 400 单元统计得到"抖动单元 13 个全部失败、通过率 0.0000、误伤 0 个通过样本"；按官方/簇单元重算后，13 条记录其实是 **7 个单元**，其中 **4 个单元在非抖动 trial 里是能通过的**（`E941775/sub_E941775_1` 与 `sub_E941775_8` 各 4 trial 均值 0.50）。伪重复把一个 4/7 的事实放大成了 0/13。**这是本条目要防的错误本身，故保留为记录。**
- **有效方案**：全部统计一律在官方单元上做；trial 只作单元内重复；比较一律配对，禁止"两次分数相减"。（本条产生的新工具：`scripts/noise_floor.py`；`scripts/paired_arms.py` 补 `official Avg@4 shape` 与 `equal-user-weight` 两个口径并分别给 delta。）
- **影响**：`CLAUDE.md` 的 Goal / Measurement / Promotion criteria 已按此重写。关键推论：**目标 Δ=+0.06 恰好卡在 8 用户非配对的 ±0.0582 上，余量为零** —— 现有 cohort 是目标的最低可行情境，不是有余量的设计。**Δ ≤ 0.02 在用户级、即使用满 56 用户也结构上不可测**（需 68 用户），只能在官方单元上用配对对照判定。
- **适用边界**：100 单元 / 400 记录 / 3 个零 trial 方差用户等数值仅对 `stock_avg4_8u.json` 这 8 个用户成立；单元定义与方差分解的结论对任何 checkpoint 成立。
- **后续风险/下一步**：过去所有 8 用户比较（含 `ADAPT R9` 与 `R5a` 的 Δ=+0.0000）均落在比原先以为的大约 3 倍的误差棒内 —— 这些结果**既不能读作"有效"也不能读作"无效"**，需按新判据重做，不得引用为结论。

---

## E-055：控制器退役——把控制层整体删除，只保留数据层

- **日期**：2026-09-13
- **状态**：`VERIFIED`（删除已执行并验证；"删除使 agent 更好"这一更强命题**未测**，见适用边界）。
- **通用性判定**：`GENERAL-STRUCTURAL`。删除依据是架构所有权事实（L1/L2 循环 vendored 只读，唯一 per-turn 注入点是 `generate_next_message`），不是分数实验。
- **难点**：`ADAPTAgent` 及其 `runtime/` 控制器曾经是项目主体，与 stock 逐条对照后确认它是**与模型平行的竞争者**而非互补层：L1/L2 循环都不归我们所有，控制器只能在 L3 每回合抢话，于是必然退化为"替模型做决定"。E-042 已量化其净负（`LOST 7 : GAINED 1`），E-046 指出净负来自"表示 + 自由度"，E-049/E-053 两次独立复现同一陷阱。
- **证据（删除集，全部已 `git rm`）**：

  | 对象 | 规模 |
  | --- | --- |
  | `agent/adapt_agent.py` | 1748 行 |
  | `agent/runtime/{state,tools,question_gate,evolution,debug,tool_errors}.py` | 1478 行 |
  | `agent/landing.py` | 339 行 |
  | `agent/v2/`（10 文件） | 1425 行 |
  | `agent/framework/`（6 文件） | 202 行 |
  | `agent/lessons.py` | 105 行 |
  | 4 个被清空的测试文件 + `test_adapt_v2.py` | — |

  删除后 `agent/` 为 58 个 `.py` / 14894 行（`memory/` 14 文件 3665 行，`runtime/` 仅剩 alignment/location/ranking/schedule，`tests/` 26 文件 5009 行）。
- **关键使能修复**：`agent/runtime/__init__.py` 的 re-export 必须清空。**一个 eager 的包 `__init__` 就是当初把已退役控制器拖进数据层 import 闭包的原因** —— 清空 re-export 是控制器得以真正可删的唯一一环。此约束已写入 `CLAUDE.md`。
- **同时清理的危险默认值**：`vitabench_runner.py` 的 `--agent` 默认为 `adapt_v2`（已删除的路径），构成静默陷阱；现改为 `("stock",)` 且默认 `stock`。另删除 7 个空转开关（`--no-phase-gating`、`--framework-speech`、`--no-adapt-prompt`、`--keep-write-phase-history`、`--no-candidate-validation`、`--no-lessons`、`--no-tiered-compaction`）。
- **保留下来的复用物**：`agent/tool_recovery.py`（292 行）从 `runtime/tool_errors.py` 忠实提取（改用 `is_write_tool(name)`，不再依赖已删的 `ToolRole`），**当前没有任何生产调用方**；`agent/tests/test_harness_integrity.py` 抢救出 2 个测试（评测器重试接受真零分；`trace_metrics` 排除评测器失败）。
- **明确放弃（用户决定，不先挖后删）**：`agent/v2/` 的 flag 系统、parity contract、`StockCompatibleExecutor`、`ABLATION_MATRIX`、`promotion_report` 一并删除，未先提取可复用逻辑。此损失记录于 `docs/AGENT_ARCHITECTURE.md` §4b。
- **验证**：全量 **301 单测通过**；`compileall` 通过；`git -C evaluation/vitabench diff --exit-code HEAD -- src/vita` 为空；真实冒烟 `--cohort dev --task-ids P722245 --subtask-ids sub_P722245_1` 得 `Subtasks: 1`、`agent_version: stock`、`states["rubric_detail"]` 存在、`info` 不再携带 `candidate_validation`/`lessons`/`tiered_compaction`。
- **一处险些漏掉的实现事故**：删除 `adapt_v1` 分派的正则过宽，吞掉了 `run_selected` 的任务选择块（`tasks`/`selected_ids`/`selected`/subtask 过滤），而 **301 个测试全绿**（没有测试调用 `run_selected`），最终由 `ruff F821`（`Undefined name selected_ids`）发现并逐字恢复。**教训：正则删除必须配 lint，测试覆盖不到的分支不会报错。**
- **适用边界**：已验证的是"删除后仓库仍自洽、测试与冒烟通过、边界未破"。**"删除控制器使分数变好"从未被测量**，不得如此声称。
- **后续风险/下一步**：`agent/tool_recovery.py` 与 `runtime/ranking.py`（487 行）、`runtime/location.py`（128 行）目前均无生产调用方，共约 615 行可达性待决：要么接线，要么按 E-055 同一标准删除，不得长期悬空。

---

## E-056：冻结重复调用（抖动）——单元内对照成立，因果未验，等待预注册干预

- **日期**：2026-09-13
- **状态**：`OPEN`。观察已取证；**干预实验尚未运行**，因此不得称为有效方案。
- **通用性判定**：`GENERAL-EMPIRICAL`。只用可观察的工具名、参数、结果内容与终止原因；reward 仅用于离线配对分桶，不作学习信号。
- **难点**：部分子任务里模型对**同一个工具、同一组参数**反复调用，且每次返回**完全相同**的结果，直到烧完步数预算。两个已取证的实例：

  | 单元 | 调用 | 次数 | 不同结果数 | 返回 |
  | --- | --- | --- | --- | --- |
  | `sub_E057330_13` | `get_delivery_order_status(order_id=OTcd66b5eca9)` | 42 | **1** | `'paid'`（4 字符） |
  | `sub_E057330_1` | `delivery_product_search_recommand(keywords=["瑞士莲","牛奶巧克力"])` | 16 | **1** | 同一 24161 字符列表 |

  结果**冻结**是关键性质：同一调用第 N 次返回同一内容，**信息增量为零**，因此"拒绝重复"在原理上不可能移除模型需要的信息。这使它与 E-053 的落地守卫有本质区别：后者替模型**做决定**，前者只是**拒绝一次零信息动作**。该规则其实早已写进 `CLAUDE.md`（"同一搜索签名允许两次，第三次是抖动信号"），但**没有任何代码路径执行它**。
- **难点（度量）**：抖动单元跨 seed 高度可复现 —— `sub_U000828_2` 在 seed 43/44/45 三次独立 rollout 下均为 **102 条消息、43 次重复**，逐字节相同。故这是 `(user, subtask)` 的确定性属性，不是抽样噪声。
- **证据（单元内对照，控制住"子任务本身难"）**：比较**同一** `(user, subtask)` 单元内部抖动 trial 与非抖动 trial：

  | | 通过 | 总计 | 通过率 |
  | --- | --- | --- | --- |
  | 抖动 trial | **0** | 13 | 0.0% |
  | 非抖动 trial | **6** | 15 | **40.0%** |

  Fisher 精确检验双侧 **p = 0.0178**；涉及 7 个单元，全部位于 cohort B（`E057330`、`E941775`、`O309411`、`Q089190`、`U000828`）。
- **零模型复现**：

  ```powershell
  python scripts/runaway_autopsy.py data/simulations/stock_avg4_8u.json --repeat-threshold 3 --show-frozen
  ```

- **为何这不是因果结论**：残余混淆是"模型已经迷路"同时导致抖动与失败。打破循环**未必**能救回该单元 —— 与 E-053 的形态相同。跨单元横截面比较（抖动单元 vs 其他单元）会把这个混淆放大，故**不作为证据**。
- **预注册干预（尚未执行，跑之前钉死）**：在上述 13 个已缓存的抖动 `(user, trial)` 单元上启用抖动守卫，对照为同一批单元的缓存观测 **0/13**。判读表：
  - 通过数显著高于 0（如 ≥5/13）→ 机制成立，期望 `ΔAvg ≈ +0.013`（13 × 0.40 × 0.0025）。
  - 全部 0/13 → **抖动是症状不是瓶颈**，与 E-053 同样记作证伪，永久关闭该方向。
  - 中间态（1–4/13）→ 证据不足，不得宣称有效。
  记账口径见 E-054：官方单元内单个 trial 救回 = `0.0025` Avg，单单元 0/4→4/4 = `+0.0100`。
- **上限**：即使全部 13 个抖动 trial 救回，也只有 **+0.0325**；按非抖动 trial 通过率折算的期望是 **+0.013**。因此该机制**不可能单独达成 +0.06 缺口**，必须在 E-054 的新判据下与配对对照一同评估。
- **已知可观测性要求**：守卫必须记录"每个单元的开火次数与被拒绝的签名"，否则无法按单元归因（E-053 曾因 `states["landing_guard"]` 未按子任务打标而丧失归因能力）。
- **预注册执行前发现的有效性约束（重要，改变了实验成本）**：**目标单元不能单独重放。** `RewriteMemory` 在子任务之间累积（`process_interactions`），所以缓存对照里 `sub_k` 的上下文包含 `sub_1..sub_{k-1}`。只跑 `sub_k` 会去掉那些上下文，处理臂与对照臂就不再匹配。因此配对测试必须**重放整个用户任务**。受影响的 5 个用户（`E057330`、`E941775`、`O309411`、`Q089190`、`U000828`）共需 12 次整用户运行 ≈ 16 小时。
- **第二个复杂化（对分数有利，但对归因不利）**：抖动大多发生在**早期子任务**（`U000828/sub_U000828_2` 是 index 1，`E941775/sub_E941775_1` 与 `O309411/sub_O309411_1` 是 index 0）。守卫若在这些位置破环，会通过记忆更新**传导到后续子任务**，因此"某个单元翻转"不再能唯一归因于守卫在该单元开火。预注册的判读表据此扩展为：**主判读看那 7 个抖动单元本身**，**其余单元的变化一律记为副作用**，只报告不归因。
- **后续风险/下一步**：实现必须先证明**真的会触发** —— E-053 的首版守卫因只处理 `MultiToolMessage` 而在真实路径上完全空转，18 个单测却全绿。本轮必须先做真实投递形状的冒烟，再花预算跑 12 次整用户运行。

---

## E-057：同一 seed 不可复现——缓存对照不是配对对照

- **日期**：2026-09-13
- **状态**：`VERIFIED`（受控复现实验）。这是对**测量基础**的更正，不是对某个 agent 机制的判断。
- **通用性判定**：`GENERAL-EMPIRICAL`。结论来自 3 次完全相同的串行运行逐条文本比对；只用可观察的消息文本，不涉及 rubric。
- **难点**：本仓的 promotion 方法一直建立在"用缓存基线在**匹配 seed** 下作对照"之上（E-053 的预注册、本轮 E-056 的预注册、以及所有 `Δ=+0.0000` 的结论）。若同一 seed 不可复现，这条推论链在每一步都断了。

  **受控实验**：`U000828` 的同一个子任务 `sub_U000828_1`，`--num-trials 1 --seed 44 --memory-type rewrite`，**串行**跑 3 次（串行是刻意的：并发会改变服务端批组合，那本身可能成为非确定性的成因，并发会污染测试）。

  | | msgs | assistant | reward | termination | 内容签名 |
  | --- | --- | --- | --- | --- | --- |
  | run 1 | 14 | 7 | 1.0 | `user_stop` | `467483dd70a6` |
  | run 2 | 14 | 7 | 1.0 | `user_stop` | `e99b70cd8f4a` |
  | run 3 | 14 | 7 | 1.0 | `user_stop` | `a845b4229a14` |

  聚合量完全相同，**文本不同**。逐条比对的关键证据：

  | 消息 | run 1 / run 2 | run 3 |
  | --- | --- | --- |
  | 6 (assistant) | `…19块，你平时也常点` | `…19块钱，奶油芝士霜的经典款，离你店也近` |
  | **7 (user)** | `行，就这家吧，送到店里就行。` | `确认下单，不用加饮品了。` |
  | 12 (assistant) | `那你自己支付就好～` | `那配送这边已经安排好了，骑手15:35从店里取餐出发` |

  **用户模拟器自己的回复也不同**，所以非确定性不只在 agent 侧：agent（8000）与 user（8002）两个模型在 `temperature: 0.0` + 固定 seed 下都产生不同文本。
- **一个必须记录的工具缺陷（本条目的成因）**：`_trajectory_hash`（`agent/vitabench_runner.py:531`）把 `messages` **整体**纳入哈希，而每条消息带 `timestamp`，`raw_data` 里还有服务端逐次生成的 `chatcmpl-*` id。**该哈希在任何两次运行之间按构造就不可能相等。** 用它比较复现性是一个**不可能为假的对照**，而且它一度让我得出两个相反的错误结论（先报"29/29 不可复现"，再报"可复现已确认"）。
- **有效方案**：`scripts/reproducibility_check.py` 现改为剥离易变字段（`timestamp` / `cost` / `raw_data` / `id`）后比对内容签名，并对同一 `(config, user, seed)` 跨 checkpoint 归并。零模型复现：

  ```powershell
  python scripts/reproducibility_check.py data/simulations/rep_s0_run1.json data/simulations/rep_s0_run2.json data/simulations/rep_s0_run3.json
  ```
- **后果（对既有结论的影响）**：

  1. **缓存对照不是配对对照。** 任何"新臂 vs 缓存基线、匹配 seed"的比较都是在比两条不同的轨迹。E-053 与本轮 E-056 的预注册设计都因此失效。
  2. **双臂必须同期跑。** 对照不能再从磁盘上取，必须与处理臂在同一批次、同一服务端状态下产出。
  3. **"seed" 不是匹配键。** 4 个 trial（seed 42/43/44/45）在实用意义上应重新理解为**同一条件下的 4 次重复抽样**，因此 `within_user_sd = 0.0573` 就是 run-to-run 方差的估计，而不是"seed 效应"。这也意味着 `users_with_zero_trial_variance = 3/8`（E-054 记录）是**运气**，不是确定性。
  4. **E-056 的簇内对照需降级引用。** 它仍是有效的单元内观察对照（控制了子任务难度），但"同一脚本、只有 trial 不同"这个框架是**错的** —— trial 之间在文本上处处不同。结论强度相应下调。
- **适用边界**：受控实验覆盖 1 用户 / 1 子任务 / 3 次运行。**"不可复现"的幅度、以及它与任务长度的关系均未测量。** 短子任务（14 条消息）即已分叉，长子任务的方差未知。
- **更正（2026-09-13，见 E-065）**：本条上面第 1、2 条推得过远，现按用户诊断文档第 6 节收窄。
  - **被否定的只有"按 seed 对齐 trial"**：同 seed 的文本既不复现，把 trial i 与 trial i 配对就是虚假精度。
  - **配对本身没有被否定**：按**相同用户/相同子任务**作配对区组比较**依然有效**。正确做法是每臂先在
    `(task_id, subtask_idx)` 单元内聚合多 trial，再比较同单元差值，并处理用户内相关性（见 E-065 修正 3/4）。
  - **缓存基线仍可作为历史参考**：若模型、提示、服务配置、任务、评测器与 runner 可比，它不是配对对照，但仍是有效参考；
    只有当服务配置可能漂移时，同期交错跑 A/B 才更可信。因此"对照不能再从磁盘上取"不是普遍要求。
- **后续风险/下一步**：**run-to-run 方差是现在项目里最重要的未知量**，它直接决定什么效应可测。测量方式：同一配置在整用户任务上重复 N 次。代价与整用户运行同量级，需用户决定预算。

---

## E-058：候选词面标注不能代表满足条件；试验有界模型证据核对

- **日期**：2026-09-13
- **状态**：OPEN，尚无分数结论。
- **用户授权**：目标改为 stock 已跑八用户 Avg@4 >= 0.35，允许自研 agent 循环，不限记忆插件；不修改 benchmark。
- **通用性**：GENERAL-INVARIANT（否定不等于正向匹配），新增核对策略为 GENERAL-EMPIRICAL 待干预验证。
- **复现**：虚构花生商品与用户“不要花生”被旧 mark_candidates 标成符合；长备注后接最新纠正时摘要格式化丢失纠正，见 ADAPT_DIAGNOSIS_2026-09-13.md。
- **移除的断言**：词面重合意味着用户条件满足。内部核对明确比较满足/冲突/未知，不控制工具可用性，也不强制成交。
- **实验**：新增 EvidenceAgent，同模型有限额内部核对，保留 RewriteMemory 对照信息量；核对不进入原始工具返回或用户对话，保存逐子任务调用、失败和成本。
- **判据**：先验证原始消息、用户纠正与工具投递契约，再整用户 smoke；正式固定相同八用户四次，官方 Avg 及净修复/破坏均报告。未运行前不宣称有效。

## E-059：整体记忆读取把否定事实当成偏好——极性在渲染时丢失

- **日期**：2026-09-13
- **状态**：PARTIAL。修复已落地，零模型复现与单测通过；尚无真实轨迹或评测证据证明跨用户泛化。
- **通用性**：GENERAL-INVARIANT（polarity 是 fact 的类型字段，渲染不得断言它不知道的极性）
- **复现命令**：`python scripts/information_expression_repro.py`（D1 段，修复前 `PREFER: 花生`，修复后 `AVOID: 花生`）
- **难点**：`ADAPTMemory.read()` 在不带 query 时，把**所有** active fact 的 `value` 直接拼在字面量 `PREFER:` 后面，完全不看 `fact.polarity`。用户历史只有"我对花生过敏"时，内部存储是正确的 `(花生, negative, safety, active)`，但渲染结果变成 `PREFER: 花生`。`read_preference_memory` 工具正好走这条路径，所以模型可能拿到与事实相反的信息。
- **证据**：`python scripts/information_expression_repro.py`，D1 段。输出 `stored facts mentioning 花生: ('花生', 'negative', 'safety', 'active')`，随后 `read() with NO query -> 'PREFER: 花生'`，而带 query 的决策卡路径正确给出 `AVOID: 花生`。无模型、无网络、无评测数据。
- **根因**：不带 query 的分支是自己手写的 `"PREFER: " + " | ".join(values)`，与 `DecisionCard.render()` 是两条独立渲染路径。带 query 的路径经过 `build_decision_card` 并按键极性分流，所以正确；不带 query 的路径绕过了它，于是极性、维度、作用域全部丢失。
- **移除的断言**：*"整体读取的每条 fact 都是用户喜欢的东西"*。这是代码里的字面断言（`PREFER:` 前缀），不是模型推断。
- **有效方案**：让无 query 路径复用 `DecisionCard.render()`，按 `fact.polarity` 分流到 AVOID / PREFER。一条记忆只有一份渲染语义，极性由类型字段决定，不由调用点决定。
- **反例门禁**：正向 fact 仍必须出现在 `PREFER:`；`polarity=positive` 的事实不得被塞进 `AVOID:`。见 `agent/tests/test_information_expression.py`。
- **适用边界**：不改变"无 query 时读全量记忆"这一语义，只改变它的渲染方式；不引入 query 相关的排序，仍然按 confidence 稳定排序。
- **后续风险/下一步**：整体读取目前是 confidence 排序的扁平视图，没有作用域过滤。是否应该按当前任务作用域收窄，属于另一个可复现难点，不在本条内解决。
- **能力抽象**：preference extraction / utilization（表示层的正确性，不是提取能力）。

## E-060：决策卡按位置截断，硬条件会整条消失

- **日期**：2026-09-13
- **状态**：PARTIAL。渲染改为优先级驱动，零模型复现与单测通过；尚无真实轨迹证据。
- **通用性**：GENERAL-INVARIANT（MUST/AVOID 是当前指令的硬条件，不是可竞争的软素材）
- **复现命令**：`python scripts/information_expression_repro.py`（D2 段，修复前丢 `内脏` 与 `sweetness=无糖`，修复后 7 条硬条件全部渲染）
- **契约变更**：`agent/tests/test_agent_architecture.py::test_decision_card_reserves_space_for_must_and_prefer` 原断言"8 条 avoid 时 `noise-2` 不应出现"，即把第 3 条排除项当作可牺牲预算。该断言随契约一并更新为"硬条件全部渲染，`max_facts` 只约束软素材"，并在测试 docstring 中说明原因。
- **难点**：`DecisionCard.render()` 取 `must[:3]` 和 `avoid[:2]`。三条忌口 + 四条 MUST 的任务，渲染后只剩前两条忌口和前三条 MUST：第三条忌口（如"内脏"）和第四条硬条件（如 `sweetness=无糖`）从未进入模型上下文。因为它们在渲染阶段就被丢弃，后续任何推理或核对都无法恢复——"模型根本没看到完整条件"。
- **证据**：`python scripts/information_expression_repro.py`，D2 段。3 条忌口 + 4 条 MUST 渲染为 `AVOID: 花生 | 香菜` 与 `MUST: category=咖啡 | size=大杯 | temperature=热饮`，`内脏` 与 `sweetness=无糖` 缺席，而 `card.constraints` 对象里 7 条约束完好。无模型复现。
- **根因**：`render(max_facts=8)` 把硬条件和软素材放进同一个"事实配额"，并按列表位置先到先得。`avoid` 被固定压到 2 条，与它是不是安全约束无关。字符串层面还有第二次位置截断：整段拼接后直接 `[:max_chars]`，尾部硬条件同样会被切掉。
- **移除的断言**：*"第 3 条之后的忌口和第 3 条之后的 MUST 是可有可无的"*。
- **有效方案**：渲染改为优先级驱动而不是位置驱动。硬条件（MUST/AVOID）整体优先，永不被软素材挤出；`max_facts` 只约束软素材（PREFER / ASK / EVIDENCE）。字符预算同样先满足硬条件，软条目在放不下时整体不追加，而不是把已拼接的字符串从尾部切断。
- **反例门禁**：软素材仍必须受配额约束（不能因为修硬条件就无界膨胀）；只有软素材时行为与原来一致；空卡仍回落到 `MUST: follow the current instruction`。见 `agent/tests/test_information_expression.py`。
- **适用边界**：硬条件数量本身无界时，仍受 `max_chars` 兜底；这是有意的最后一个约束，且此时被截断的必然是硬条件本身，属于提示长度上限而非策略取舍。
- **后续风险/下一步**：现有单测 `test_decision_card_reserves_space_for_must_and_prefer` 断言的是旧契约（8 条 avoid 时 `noise-2` 不应出现），必须随契约一起更新并在本轮说明，不能默默改绿。
- **能力抽象**：utilization / execution（条件绑定完整性）。

## E-061：子句边界被跨过——一个"喜欢"吞掉后半句的"过敏"

- **日期**：2026-09-13
- **状态**：PARTIAL。捕获组收口到子句内、剥离词表补齐连词，零模型复现与单测通过；尚无真实轨迹证据。
- **通用性**：GENERAL-INVARIANT（被捕获的偏好对象是一个名词短语；名词短语不跨句读标点）
- **复现命令**：`python scripts/information_expression_repro.py`（D3 段，修复前只有 `('香菜，但是我对花生过敏', 'positive', 'like')`，修复后得到 `花生/negative/safety` 与 `香菜/positive/like` 两条）
- **回归风险与已处理项**：单测 `agent/tests/test_avoidance_signals.py::test_common_avoidance_phrasings` 的 `我不能吃辣，微辣也不行` 用例一度失败——旧正则正是靠跨过逗号凑够 2 字下限，再由清洗函数截断。修复把"捕获宽度上限"与"对象最小长度"解耦（下限降到 1 字，质量交给 `_clean_avoidance_object` / `_is_item_like` 判定），该用例恢复通过。
- **难点**：`SignalParser._parse_dialogue_turn` 的 LIKE / WANT / BRAND 捕获组用 `.{2,15}` 这类通配，能吃掉 `，` `。` 等子句标点。用户说"我喜欢吃香菜，但是我对花生过敏。"时，LIKE 把整段 `香菜，但是我对花生过敏` 当成一个正向对象；同一句里真正要紧的过敏事实反而没有被保留。结果是记忆里只有一条正向 like，安全约束整条消失。
- **证据**：`python scripts/information_expression_repro.py`，D3 段。`memory.facts` 只有 `('香菜，但是我对花生过敏', 'positive', 'like', 'active')`，没有 `花生 / negative / safety`。无模型、无评测数据。
- **根因**：两个独立的结构缺陷叠加。
  1. 捕获组 `.{2,15}` 不含标点排除，跨子句匹配。
  2. 后置过敏模式 `(?:对)?([\u4e00-\u9fff]{2,6})(?:过敏|…)` 会把子句首连词一起捕获（`但是我对花生`），而 `_clean_avoidance_object` → `_strip_avoidance_lead` 的 `_AVOIDANCE_LEAD_WORDS` 里没有 `但是/不过/可是/然而`，逐字剥离停在 `但` 上，随后 `_is_item_like` 因为 `是` 是功能字而判定整段不可用——**整条丢弃，而不是回退到更短的可用项**。这与 E-044 是同一类"真实安全事实没进记忆"，但根因不同。
- **移除的断言**：*"一个偏好对象的文本可以跨过句读标点"*，以及 *"清洗失败时整段作废是安全的"*（对安全约束而言，作废比截短更危险）。
- **有效方案**：
  1. 捕获组改为子句内的否定字符类，标点即边界。
  2. 把对比连词加入剥离词表，使 `但是我对花生` 能回退到 `花生`。
- **反例门禁**：不应触发修复的场景必须仍然成立——单子句 `我对花生过敏。` 仍得到 `花生`；`我喜欢吃香菜。` 仍得到 `香菜`；含功能词的伪项（`让我去查过敏源`）仍必须被拒绝。见 `agent/tests/test_information_expression.py`。
- **适用边界**：只约束"偏好对象"这一层；订单/备注等结构化字段不受影响，它们的文本本来就不是自由子句。
- **后续风险/下一步**：同类通配还出现在 WANT / BRAND / future_default 模式中，按同一不变量一并收口；若后续发现合法对象本身含标点，需要改为按词表校验而不是简单排除。
- **能力抽象**：preference extraction（否定与安全约束的提取）。

## E-062：候选选择的机制分解——审计相关，尚非瓶颈

- **日期**：2026-09-13
- **状态**：OPEN。机制已量化，**尚未做预注册干预**；按 E-053，审计相关不得直接当作瓶颈，也不得据此声称提分方向。
- **通用性**：GENERAL-EMPIRICAL（跨多个 domain 的机制计数；需要干预验证才可升级）。
- **复现命令**：
  - `python scripts/target_reachability.py data/simulations/stock_avg4_8u.json`
  - `python scripts/candidate_selection_audit.py data/simulations/stock_avg4_8u.json`
  - 单案例肉眼核对：`python scripts/_cell_probe.py data/simulations/stock_avg4_8u.json --cell host_listed_product_absent --limit 1`
- **先复核用户口径**（`target_reachability.py`，stock 8u x 4t，graded 396，overall 0.2955）：
  - `product_printed_n = 235`，`product_printed_pass_rate = 0.4255` — 与"目标商品已展示的 235 次仍只有约 42.6% 通过"一致。
  - `host_listed_product_absent` = delivery 47 + instore 48 + ota 34 = **129** — 与"129 次父商家/酒店已出现但目标商品未展开"一致。
  - 失败 279 次的结构：never_returned 62、returned_near_not_bound 103、returned_buried_not_bound 91、returned_and_bound 23。**194/279（69.5%）的失败是"答案已经在模型眼前而它选了别的"**。
- **Cell A（129 次：父候选已列出、目标商品从未打印，pass_rate 0.1008）**：

  | 目标父候选排名 | 未展开任何父候选 | 展开了另一个父候选 |
  | --- | --- | --- |
  | 1 | 4 | 6 |
  | 2–3 | 7 | 15 |
  | 4–10 | 14 | 13 |
  | 11+ | 42 | 28 |
  | 合计 | **67** | **62** |

  `top3_host_but_different_parent = 21`：正确的父候选已在**前 3**，模型仍去展开别的。
  另有 **70/129（54%）** 的目标父候选排在第 11 名之后——这是检索/排序的空间，属于数据层。
- **Cell B（135 次：目标商品**已**打印但子任务仍失败）**：

  | 目标商品排名 | 未绑定任何 id | 绑定了另一个已见 id | 绑定了目标但评分仍失败 |
  | --- | --- | --- | --- |
  | 1 | 10 | 14 | 16 |
  | 2–3 | 13 | 6 | 0 |
  | 4–10 | 5 | 6 | 2 |
  | 11+ | 32 | 31 | 0 |
  | 合计 | **60** | **57** | **18** |

  `top3_product_but_different_id = 20`：目标商品已在**前 3**，模型绑定了另一个已见 id。
  18 次"绑定了目标仍失败"中有 16 次目标排在第 1——选对之后仍然失败，说明瓶颈在参数/支付/其他条件，不在选择。
- **单案例机制**（`_cell_probe.py`，E057330 trial0 subtask1）：目标父候选 `S...S00011`（score 4.0）在 42 条结果中排第 3；模型展开的是 `S...S00006`（score **5.0**），随后 `create_delivery_order` 绑定了 `S...P00015`——**该 id 在任何工具结果中都没有打印过**。这与 E-035 同类：写入使用了模型自己提供的 id。
- **根因假设（未验证）**：父候选的取舍由通用质量信号（评分/顺序）驱动，而不是由当前指令的约束驱动；数据层没有把"某候选的已打印属性是否满足本次约束"变成可比较的信息。
- **必须避免的推理错误**：*"目标父候选几乎从不被展开（0/129）"* 不是独立发现。展开 target parent 正是让 target product 打印出来的原因，所以那些行落在 Cell B；该比例接近构造性恒真。已在脚本 `note` 字段中标注，禁止引用为独立结论。
- **预注册干预（尚未执行）**：只做信息呈现、不做控制层决策（E-042/E-049/E-053 的教训）。候选一旦被观察到，就在决策卡/工具返回中并列显示该候选的**已打印属性**与当前指令/记忆约束的对应关系（满足 / 冲突 / 未知），不替模型选择，不阻断写入。判据：官方 unit 上的 paired fixes/breaks，`fixes > breaks` 且 p < 0.05；同时报告 `top3_host_but_different_parent` 与 `top3_product_but_different_id` 两个总体是否下降。
- **量级核算（已更正，勿再引用旧表述）**：41 次"前 3 却选了别的"若全部修复，按每次 0.0025 计约 +0.1025 Avg；修复一半约 +0.051。
  **此前本条把它说成"低于本队列 ±0.0582 的分辨下限"，该论证已被用户诊断文档第 6 节否证，现予撤回**：
  0.0582 是**单臂**用户均值的 2SE，既不是双臂差值的门槛，也不是官方加权 Avg 的区间，更不是"任何改动的普适检测下限"。
  在等方差的同 n 双臂下，差值区间的 2SE 是 √2 倍，即 0.0823（已由 `noise_floor.py` 输出 `two_arm_unpaired_2se`）。
  因此该 +0.051 的估算**不足以据此放弃干预**；要不要做是资源决策，不是统计定理。正确的读法见 E-065。
- **能力抽象**：utilization / execution（候选选择与规格对应）。
- **下一步**：先做无模型的最小复现（虚构候选 + 虚构属性），再决定是否进入实现；不得跳过反例门禁。

## E-063：清理不产生分数——但"没接线的代码"会被当成能力

- **日期**：2026-09-13
- **状态**：PARTIAL。清理已执行并通过全部门禁；**明确不声称任何分数变化**。
- **通用性**：GENERAL-INVARIANT（"未被生产代码引用的模块不构成能力"是结构事实，可用 import 图证明）。
- **背景**：用户指出"文件数量不能代表 agent 实际拥有的能力"，并选择先做一次独立清理。
- **实测的引用图结论**（`grep` + 子进程 `sys.modules` 断言，非推测）：
  - `agent/memory/reflexion.py`、`agent/tool_recovery.py`：**只有各自的单测引用**，无生产调用点。
  - `CandidateLedger`（612 行，原在 `decision.py`）：**全部引用都在 `agent/tests/`**，无间接/动态用法。
  - `runtime/ranking.py`、`runtime/location.py`：只被 `CandidateLedger` 与测试引用。
  - `runtime/schedule.py`：只被 `agent/tests/test_schedule.py` 引用。
  - `agent/runtime/alignment.py`：**是**生产依赖（经 `agent/memory/grounding.py`）。
- **执行的清理**：
  1. 删除 `_execution_hint()` 与 `_summary_fallback()`（无调用点；且其注释里带 `A891207` / `B865629` 这类
     用户/任务特例句，属仓库禁止的写法），连带删除只服务于它们的 `_HINT_TOP_K` 与 `_normalize_domain` 导入。
  2. `reflexion.py` + 其单测、`tool_recovery.py` + 其单测移入 `archive/`，并修正归档测试的导入路径；
     归档目录自带 `README.md` 说明"未接线不等于能力"。
  3. `CandidateMarkingAgent` **冻结**：代码与 `--candidate-marking` 保留，但标注为 frozen 且不是能力。
  4. `CandidateLedger` 从 `decision.py` **拆出**到 `agent/candidate_ledger.py`；`decision.py` 现在只承载共享数据结构。
- **顺带修正的两处过期结论（重要）**：
  - `docs/ADAPT_DIAGNOSIS_2026-09-13.md` 担心 `CandidateMarkingAgent` 原地修改 `ToolMessage.content`
    会污染给 evaluator 的原始证据。**该问题已不存在**：`generate_next_message` 先 `deepcopy(message)`（第 75–77 行）
    再改副本，环境原始消息保持不变。该条应视为已修复，不要再据此质疑历史产物。
  - `docs/AGENT_ARCHITECTURE.md` 9.4 把 `runtime/{ranking,location,schedule}.py` 列为"已实测的数据层硬依赖"，
    与实测不符，已更正。三者保留，但不再被描述为数据层依赖或现有能力。
- **验证**：`agent/tests` 312 passed（清理前 346；35 个随归档移出、新增 1 个闭包守卫）；
  `archive` 35 passed；`compileall` 通过；`ruff agent scripts --select F401,F811,F821` 通过；
  vendored VitaBench `diff --exit-code` 通过；`scripts/_official_metrics.py` 仍复现
  `evaluation units = 100, Avg@4 = 0.2925`（未受清理影响）。
- **新增守卫**：`test_data_layer_import_closure_excludes_retired_controller_code` 用子进程断言
  导入数据层后 `sys.modules` 不含 `candidate_ledger` / `runtime.ranking` / `runtime.location`。
  这直接防住"急切 `__init__` 重导出把控制器拖回数据层闭包"的历史回归。
- **适用边界**：本条**不声称提分**。删除没有运行的代码只降低维护成本，不改变分数；把它当成绩效是错的。
- **能力抽象**：不适用（工程可维护性 / 评测诚实性）。
- **下一步**：`decision.py` 中仍留有若干只被 ledger 使用的 helper（`_validate_argument_constraint`、
  `_normalize_search_arguments`、`_category_groundable_in_ledger` 等），属下一轮"再删减"的对象；
  移动它们前需先证明无其他调用点。

## E-064：对齐机制只有二元匹配，且会把"禁止项"算成正向得分

- **日期**：2026-09-13
- **状态**：PARTIAL。缺陷已无模型复现，替代实现已落地并通过反例门禁；**尚未接入任何计分路径**，因此还没有 paired 测量。
- **通用性**：GENERAL-INVARIANT（约束对候选的状态是 {满足, 违反, 未知} 三值；把"违反"和"未知"折叠成同一个布尔值会丢失信息；极性由约束类型决定，不由承载它的字段决定）。
- **复现命令**：`python scripts/alignment_correspondence_repro.py`（A 段复现旧机制，B 段验证替代实现）
- **背景**：E-062 预注册的干预是"并列显示候选已打印属性与约束的对应关系（满足／冲突／未知）"。实现前先验证现有对齐代码能不能承载它。
- **复现结果（全部无模型、虚构候选与属性）**：

  **A1「违反」与「未知」是同一个值。** 约束"热饮"，三个候选：
  - 满足（`temperature=热饮`）→ `matches=('热饮',)` score 2.0
  - 违反（`temperature=冰饮`）→ `matches=()` score 0
  - 未知（未打印 temperature）→ `matches=()` score 0

  违反与未知得分完全相同。`CandidateAttributeMap.matches` 只回答"候选文本里有没有这个值"，从不回答"是否与约束冲突"。

  **A2 指令里的禁止项变成正向偏好。** 指令`帮我点一杯奶茶，不要花生碎`，候选属性 `topping=花生碎`：
  - 含禁止项的候选 → `matches=('花生碎',)` score **3.0**
  - 干净候选 → score 0

  诱导出的原子是 `('topping', '花生碎', 3.0)`——**禁止项被当成权重最高的正向偏好**，于是"含花生碎"的候选得分反而更高。根因：`DecisionCard.alignment_source_records` 把 `task_intent`（原始指令）以 3.0 权重喂进正向匹配器，而 `_atomize` 不区分极性。这是 E-059 同族的极性反转，发生在排序层。

  **A3 持久否定约束完全不在对齐视图里。** `card.avoid=['花生碎']` 时 `alignment.preferences` 为空、诱导原子为空：含禁止项的候选与干净候选得分都是 0。否定约束在表示层就被丢弃，不是被误判。

  **附带发现**：对齐只在**指令文本含有候选已打印属性的完整取值**时才激活。"不要花生"（目录里叫"花生碎"）诱导不出任何原子——机器激活与否取决于字面重合，而不是语义。
- **重要限定（必须一起引用，否则会误导）**：上述三点的宿主 `EvidenceAlignment` / `CandidateAttributeMap.matches` / `ground_facts_to_candidates` / `apply_candidate_grounding` / `CandidateRanker` **都没有生产调用点**（只被 `agent/tests` 引用；`grounding.py` 被数据层 import 但函数本身无调用者）。因此：
  1. 这些缺陷**当前不造成分数损失**，不得表述为"现存扣分项"。
  2. 真正的风险在于**把旧排序器直接接回去**这个显而易见的动作会注入 A2 的极性反转——那是把 E-059 类错误重新引入排序层。
- **移除的断言**：*"候选文本包含某个值"等价于"该候选满足用户条件"*（A1/A3）；*"指令里出现的取值一定是用户想要的"*（A2，忽略了祈使句里的否定）。
- **有效方案（已实现，未接线）**：`agent/runtime/correspondence.py`。约束对应关系是显式三值，极性由约束来源决定：
  1. 每条约束带 `polarity`（`require` / `forbid`）；`forbid` 只可能产生 `VIOLATED` 或 `UNKNOWN`，**永不产生 `SATISFIED`，也永不进入正向得分**。
  2. 三值是纯函数输出，可被渲染，**不参与选择、不阻断写入**（E-042/E-049/E-053 的教训）。`render_correspondence` 只标注"满足／冲突／未知"，不含推荐语气。
  3. `require` 只有在**同一属性键、单值、且已打印的值不同**时才判为 `VIOLATED`；键缺失或多值键一律 `UNKNOWN`——缺失不等于违反。
  4. 数值与日期约束：**数值范围已实现**（`numeric_max` / `numeric_min`，读候选自身打印的数字或 price 字段；
     无打印数字则 `UNKNOWN`；禁止型数值约束一律 `UNKNOWN`，因为"没打印数字"不能被读成"干净"）。
     **日期关系仍未实现**，一律 `UNKNOWN`。
  5. **父子绑定已实现**（`parent_must_include`）：候选的 `parent_ids` 含该父候选 → `SATISFIED`；
     含别的父候选 → `VIOLATED`；未观察到父候选 → `UNKNOWN`（不能凭缺失断言）。
  6. **当前纠正已实现**（`apply_current_correction`）：同一条件的当前表述**替换**记忆中的值，而不是与之并列；
     并引入 `SOURCE_PRECEDENCE`（instruction > correction > constraint/avoid > memory > prefer），
     让高优先级来源可以推翻低优先级来源，且冲突极性的败者被删除而不是留在视图里自相矛盾。
- **对照既有约定的覆盖情况**（用户第 6 节预注册的验证集）：正负约束 ✓、当前纠正 ✓、数值范围 ✓、父子绑定 ✓；
  **「不同主体」尚未覆盖**——它属于**提取层**的归属问题（"我朋友喜欢X"不得记成用户偏好），不是候选–约束关系代数，
  应在 `signals.py` / `facts.py` 侧单独处理并单独复现，不应塞进本模块。
- **验证（本模块）**：`agent/tests/test_correspondence.py` 28 项反例门禁通过
  （含"禁止项永不得为满足"、"缺失不得判违反"、"多值键不得判冲突"、"无属性键的 require 不得被判违反"、
  "禁止型数值约束不得为满足"、"渲染不得含推荐语气"、"纯函数"）；
  零模型入口 `python scripts/alignment_correspondence_repro.py` 的 B 段覆盖全部 6 项并全部为 CORRECT。
- **附带修复**：`CandidateAttributeMap.from_candidate` 增加 `min_chars` 参数（默认 2，**默认行为完全不变**，grounding 路径不受影响）。correspondence 视图传 1，因为单字符属性是真实证据（`taste=辣`），而旧的 `_usable` 要求 ≥2 字，会让单字符约束永久无法判定——这是 E-061 的信息丢失在更下一层的复现。
- **验证**：`agent/tests/test_correspondence.py` 16 项反例门禁通过（含"禁止项永不得为满足"、"缺失不得判违反"、"多值键不得判冲突"、"渲染不得含推荐语气"、"纯函数"。全量 `agent/tests` 328 passed（+16），`archive` 35 passed；`compileall` / `ruff` / vendored `diff --exit-code` 通过；`scripts/_official_metrics.py` 仍为 `Avg@4=0.2925`；数据层 import 闭包断言仍为空。
- **反例门禁**：正向约束仍必须能被判为满足；禁止项命中必须判为违反；属性未打印必须是未知（而不是违反）；不得因为引入三值而让任何路径阻断写入。
- **适用边界**：本条只覆盖"约束 ↔ 单个已观察候选"的对应关系，不覆盖候选之间的比较与最终选择。**接入 turn loop 尚未做，也不得在无 paired 测量的情况下宣称提分。**
- **能力抽象**：utilization（规格对应与条件绑定）。
- **下一步**：按用户第 6 节执行——先在虚构候选上验证（**已完成**），再跑**小范围真实工具投递 smoke 证明确实触发**（未做），
  然后才在相同开发用户、相同 trial 数、完整用户历史上做 A/B，主要报告官方 Avg、逐用户差值、修复/破坏分布。
  **接入时保留 stock + RewriteMemory 不变**，只加候选证据视图，避免一轮同时改记忆、标注、控制器与预算。

## E-065：测量装置的三个口径错误——官方 Avg、双臂门槛、配对层级

- **日期**：2026-09-13
- **状态**：PARTIAL。三处已修正并由零模型命令复现；**受影响的旧推论已在 E-062 中撤回**。
- **通用性**：GENERAL-INVARIANT（"单位"必须先定义层级；单臂区间不等于双臂差值门槛；共享用户历史的单元不是独立样本）。
- **来源**：用户诊断文档 `docs/ADAPT_DIAGNOSIS_2026-09-13.md` 第 6 节"现有测量纪律中需要纠正的推论"。以下是按其指出的问题逐条修正。
- **复现命令**：
  - `python scripts/noise_floor.py data/simulations/stock_avg4_8u.json`
  - `python scripts/noise_floor.py data/simulations/stock_avg4_8u.json data/simulations/stock_avg4_8u.json`（自配对对照）
  - `python scripts/_official_metrics.py`
- **修正 1：官方 Avg 被说反了。** `noise_floor.py::hierarchy` 的 `note` 原写"only user_weighted_avg is the official Avg"，与官方函数输出矛盾。
  实测：`flat_subtask_trial_mean = 0.2925`（按 (task_id, subtask_idx) 单元、先取该单元各 trial 均值再平均）才是官方 Avg；
  `user_weighted_avg = 0.2940` 是等用户权均值，是另一个量。note 已改为明确指向官方口径，并写明两者因用户子任务数不等而不同。
- **修正 2：单臂区间被当成双臂门槛。** `unpaired_2se_over_users = 0.0582` 是**单臂**用户均值的 2SE。等方差同 n 的双臂差值 2SE 为 √2 倍，即 **0.0823**，现已作为独立字段 `two_arm_unpaired_2se` 输出；
  `decompose()` 的 note 明确写出"两者都不是普适检测下限，子任务共享用户历史，100 个聚合单元不是 100 个独立样本"。
  **受影响结论**：E-062 曾用"0.051 < 0.0582 所以读不出来"来降低该干预的优先级，该推理已撤回。
- **修正 3：配对发生在伪重复层级。** `paired()` 原先在 `(user, trial)` 上配对（32 个键），把同一脚本的 4 个 trial 当作 4 个独立观测。
  现改为在**官方单元 `(task_id, subtask_idx)`** 上配对（100 个键），并额外给出**用户级聚类稳健**汇总：
  - `official_unit`：功效更高，但同一用户内的单元相关，区间偏乐观；
  - `user_cluster`：先取每用户自身均值再配对，对用户内相关性稳健，**小队列下应以此为主报告**。
  同时明确：配对按 (user, subtask)，**永不按 seed**（E-057 仍然成立：同 seed 不复现，按 trial 对齐是虚假精度）。
  自配对对照：同一文件跑两次 → `shared_units=100`、全部 tied、双侧差值均为 0，说明该对照**可以被证伪**（不是恒真装置）。
- **修正 4：主配对装置同样在伪重复层级上做显著性判断。** `scripts/paired_arms.py` 原本在
  `(task_id, trial, subtask_id)`（400 个键）上统计 `fixes`/`breaks` 并做符号检验。点估计没有错——
  平衡时平坦均值确实复现官方 Avg（实测输出 `0.2925`，与 `_official_metrics.py` 一致，`both pass = 117` 也与诊断文档一致）——
  但**显著性被放大**：同一脚本的 4 个 trial 被当作 4 个独立观测。
  现已改为以**官方单元 `(task_id, subtask_idx)`（100 个键）**为主判据，并追加**用户级聚类稳健**汇总；
  原 per-trial 计数降级为"仅用于归因，不得引用为显著性"。
  **可证伪性验证**：构造一个把所有 trial 从 0 翻到 1 的合成臂（20 个官方单元），
  实测官方单元 `fixes=18 / breaks=0`、`z=+4.24, p<0.0001` 判定 PASS；
  同一份数据 per-trial 的 `z=+8.00`。两者比值 ≈2 = √4，正是 4 个 trial 造成的放大——
  这条同时证明该判据**会**在真实差异下触发（不是恒真装置），也量化了旧口径的虚高幅度。
  自比较对照（同一文件跑两次）得到 `shared official units=100, ties=100, delta=+0.0000, not significant`。
- **移除的断言**：*"等用户权均值就是官方 Avg"*；*"单臂 2SE 就是任何改动的检测下限"*；*"按 (user, trial) 配对／统计是合法的配对实验"*。
- **适用边界**：本条只修测量口径，不含任何 agent 行为改动；`_official_metrics.py` 输出未变（仍为 100 单元 / 0.2925），
  说明修正的是**描述与汇总层级**，不是数据。合成臂已删除，仓库中无残留。
- **仍未处理**：`README.md` 与工程日志中关于"配对是否有效"的表述尚未统一（E-057 的措辞需要按用户第 6 节改写：
  同 seed 不复现**不否定**按相同用户/任务做配对区组比较，被否定的只是按 seed 对齐 trial）。
- **能力抽象**：不适用（评测口径 / 评测诚实性）。
- **下一步**：按用户诊断文档第 6 节改写 E-057 的措辞——被否定的只是**按 seed 对齐 trial**，
  按相同用户/任务做配对区组比较**依然有效**；并把 `README.md` 与日志统一到同一说法。

## E-066：候选证据视图的触发门禁——冒烟在花模型预算前给出负面信号

> **2026-09-13 更正**：本条下面的"结果 A"与由它导出的第 2 条读法是**错的**，错因是 `constraints_from_card` 未读取
> `card.constraints` 中的候选定向要求。修正后指令口径的覆盖是 **164/400（41%）**而非 16/400，详见 **E-067**。
> 本条保留原样作为演进记录：其**结论**（冲突信号极稀少）在修正后仍然成立，但**根因**已由 E-067 更正为
> "指令约束不带 attribute_key，导致 REQUIRE 无法被判 VIOLATED"，而不是"编译覆盖率低"。

- **日期**：2026-09-13
- **状态**：OPEN。预注册的"先证明确实触发"这一步**已完成**；结论是**判别力很低**，因此在取得更大预算前不应直接进入 A/B。
- **通用性**：GENERAL-EMPIRICAL（在固定 8 用户基线的 400 次运行上做零模型重放；只读子任务 `instruction` 与 agent 已收到的工具结果，**从不读 `user_intention`**）。
- **复现命令**：
  - `python scripts/correspondence_trigger_audit.py data/simulations/stock_avg4_8u.json`
  - `python scripts/correspondence_trigger_audit.py data/simulations/stock_avg4_8u.json --with-memory`
- **口径**：把 E-064 的 `correspondence.py` 接到真实轨迹上重放——按编排器顺序先 `memory.update(subtask.interactions)`（`llm=None`，零模型），再用 `build_decision_card` 编译约束，用现有解析器把工具结果解析成候选，统计视图会不会非空、以及每个(候选, 约束)对落到哪个状态。
- **结果 A：只用指令编译约束（stock + RewriteMemory 的实际情况）**

  | 指标 | 值 |
  | --- | --- |
  | 子任务运行 | 400 |
  | 能从指令得到约束 | **16（4%）** |
  | 视图非空 | 16 |
  | (候选,约束) 对 | 1926 |
  | 其中 unknown / violated / satisfied | 1910 / **16** / **0** |
  | 已判定占比 | 0.83% |

- **结果 B：额外重放 ADAPT 事实库（即"同时替换 memory"，用户第 6 节明确暂不做）**

  | 指标 | 值 |
  | --- | --- |
  | 能得到约束 | 392（98%） |
  | 视图非空 | 376（94%） |
  | 约束来源计数 | prefer **26064** / avoid 312 |
  | (候选,约束) 对 | **2,789,597** |
  | 其中 unknown / satisfied / violated | 2,763,441 / 26,128 / **28** |
  | 已判定占比 | 0.94% |

- **读法（哪些支持、哪些不支持）**：
  1. **不支持"先做候选证据视图"**：两种口径下冲突信号都极稀少——指令口径 16 次、记忆口径 28 次。若该视图的价值主要在"标出冲突"，它在 400 次运行里只有这么多次机会。E-062 观察到 Cell B 有 57 次"绑定了另一个已见 id"，但那 57 次里绝大多数**不是**能被结构化约束判为冲突的情形。
  2. **指令本身几乎不携带可结构化约束**（16/400）。真正被评分的条件（房型、席别、尺寸、预算）多数只以自然语言出现在指令里，而 `TaskSpec.compile` 并未把它们编译成结构化要求。**因此优先级 2 的真实瓶颈在"编译覆盖率"，不在"渲染对应关系"。**
  3. 记忆口径的 prefer 计数高达 26064（每次运行约 66 条），因为 `build_decision_card` 会把**所有**相关且 decision-eligible 的正向事实追加进 `card.prefer`（`decision.py` 第 708 行）。把这些软偏好一律当成 REQUIRE 会产出 279 万个(候选,约束)对，其中 94% 是 `unknown`——这是噪声级确认，不是判别。**把软偏好与硬约束分开计数是下一步的必要修正。**
  4. 26,128 个 `satisfied` 对（每次运行约 67 个）是真实的正向信号，可能帮助"选对要查看的对象"。本条**不**据此声称无用，只声称**冲突检测**这一路的价值上限很低。
- **移除的断言**：*"把约束–候选对应关系显示出来就能改善选择"*——在证明该视图**会在真实轨迹上触发并判别**之前，这句话没有证据支持。
- **适用边界**：这是**触发覆盖，不是效果**。非空只说明机制会开火，不说明有帮助。本条不含任何模型调用，不含分数结论。
- **能力抽象**：utilization（规格对应与条件绑定）。
- **下一步（按证据重排）**：先提高指令→结构化约束的**编译覆盖率**（这是 16/400 的直接瓶颈），并把软偏好与硬约束分开计数；再重跑本条门禁，若冲突信号数量级上升，才值得花模型预算做 A/B。

## E-067：E-066 的"编译覆盖率 4%"是我的提取缺陷，不是编译器的属性

- **日期**：2026-09-13
- **状态**：PARTIAL。E-066 的错误论断已更正；修正后**冲突信号依然极稀少**，但**原因与 E-066 说的不同**。
- **通用性**：GENERAL-INVARIANT（"约束在哪个字段"是代码事实；`card.constraints` 里的候选定向要求必须被读取）。
- **复现命令**：
  - `python scripts/_constraint_source_probe.py`（卡片字段构成）
  - `python scripts/correspondence_trigger_audit.py data/simulations/stock_avg4_8u.json`
  - `python scripts/correspondence_trigger_audit.py data/simulations/stock_avg4_8u.json --with-memory`
- **E-066 错在哪里**：E-066 断言"指令本身几乎不携带可结构化约束（16/400），真实瓶颈在编译覆盖率"。
  **这是错的。** 错因在我的 `constraints_from_card`：它只读 `card.avoid`、`card.prefer` 和 operator 为 `excludes` 的约束，
  **完全没有读 `card.constraints` 里 target 为 CANDIDATE 的要求**。而那些要求一直都在被编译。
- **卡片字段构成实测**（771 个子任务定义，`_constraint_source_probe.py`）：

  | 字段 | 有内容 |
  | --- | --- |
  | `card.must` / `spec.must` | 660（**85.6%**） |
  | `card.avoid` | 16（2.1%） |
  | `card.prefer` | 13（1.7%） |
  | `spec.unknown_slots` | 448（58.1%） |
- **修正后的触发门禁**（400 次运行）：

  | 指标 | 指令口径（修正前 → 修正后） | 记忆口径（修正后） |
  | --- | --- | --- |
  | 有约束的运行 | 16 → **164（41%）** | 396 |
  | 有**硬**约束的运行 | — → **160** | 248 |
  | 视图非空率 | 0.04 → **0.37** | 0.82 |
  | 已判定占比 | 0.83% → **27.7%** | 1.0% |
  | all pairs（unknown/satisfied/**violated**） | 10919 / **4172** / **0** | — |
  | hard pairs（unknown/satisfied/**violated**） | 9949 / 4172 / 0 | 41268 / 4172 / **12** |
  | soft pairs（unknown/satisfied/violated） | 970 / 0 / 0 | 2,729,551 / 23,460 / 0 |
- **结论（与 E-066 的实质结论一致，但根因不同）**：
  1. **冲突信号依然近乎为零**：修正提取后，指令口径 `violated = 0`，记忆口径 `violated = 12`。所以"该视图的价值主要在标出冲突"仍然不成立。
  2. **但原因不是编译覆盖率**，而是一个更具体的结构事实：**来自指令的约束几乎都不带 `attribute_key`**。
     按本模块的保守规则，`REQUIRE` 只有在"同一属性键、单值、且已打印值不同"时才能判 `VIOLATED`；没有键就只能 `SATISFIED` 或 `UNKNOWN`。
     因此该视图**能确认、几乎不能反驳**——这正是 `violated = 0` 的机制。
  3. 正向信号是真实的：4172 个 `SATISFIED`（硬约束）。这条可能帮助"选对要查看的对象"，但本模块**不能**据此声称提分。
- **同时新增的结构改动**：`CandidateConstraint` 增加 `hard` 字段，把**硬条件**（指令的禁止项与候选定向要求）与**软偏好**（记忆里的正向事实）分开计数。
  软偏好默认不渲染 `UNKNOWN`：记忆口径下软约束产生 273 万个对、`satisfied` 仅 2.3 万个，全部渲染会把真正的冲突埋在噪声里（E-066 已观察到该量级）。
- **移除的断言**：*"指令几乎不携带可结构化约束，瓶颈是编译覆盖率"*（E-066 的论断，已撤回）；
  以及 *"把所有 card.prefer 当 REQUIRE 一起渲染是安全的"*。
- **适用边界**：仍是**触发覆盖，不是效果**；不含模型调用、不含分数结论。`spec.unknown_slots` 覆盖率 58.1% 是本轮**新发现**且指向优先级 3（主动提问要填的就是这些槽）。
- **能力抽象**：utilization（规格对应与条件绑定）。
- **进一步：给约束补属性键（候选集诱导词汇）。** 按上一条下一步，新增 `build_vocabulary(candidates)`：
  从**当前候选集**自身的已打印属性诱导 `值 → 属性键`（open-world、候选集局部、不引入领域词典，与 `alignment.py` 的设计一致）。
  这样一条不带键的约束可以借由"别的候选把 `热饮` 打印在 `temperature` 下"而获得键，从而具备被判 `VIOLATED` 的能力。
  **实测结果（关键）**：

  | 口径 | hard violated | soft violated | hard satisfied |
  | --- | --- | --- | --- |
  | 指令口径 | **0**（不变） | 0 | 4172 |
  | 记忆口径 | **12**（不变） | 0 → **14,227** | 4172 |

- **由此得到的可行性边界（本轮最重要的结论）**：
  1. **在预注册配置下（stock + RewriteMemory，只用指令编译约束）该视图无法反驳任何候选**——`violated` 恒为 0。
     它能做的只有确认（4172 次 `SATISFIED`）。也就是说，它在 37% 的运行上渲染出一行，全部是"满足"或"未知"，**没有一次冲突**。
  2. **冲突信号只在引入结构化记忆后才出现**（软约束 14,227 次、硬约束 12 次）。
     但"引入结构化记忆"正是用户第 6 节明确要求**本轮不要同时做**的第二项改动。
  3. 因此按用户自己的"一次只验证一个假设"的纪律，**当前形态的候选证据视图不值得直接投入模型预算**；
     要么承认它只是确认性信息（价值上限低），要么把它与"结构化记忆作为约束来源"合并成一个新假设重新预注册。
- **移除的断言**：*"补上属性键就能显著提高冲突检出"*——对**指令口径无效**（0 → 0），对记忆口径有效但代价是同时改记忆层。
- **下一步**：不建议直接接线。两条待裁决路线：
  (a) 把"候选证据视图 + 结构化记忆作为约束来源"合并为一个**新预注册假设**，先做小范围真实工具投递 smoke，
      证明确实产出冲突，再谈 A/B；
  (b) 转向**优先级 3（主动提问闭环）**——本轮顺带实测 `spec.unknown_slots` 在 **58.1%** 的子任务上有内容
      （`product` / `address` / `quantity` / `departure` 等），这正是主动提问要填的缺口，且与 E-066/E-067 的负面结果不冲突。

## E-068：主动提问靠关键词猜领域——四个可复现的错误提问

- **日期**：2026-09-13
- **状态**：PARTIAL。四个决策缺陷中的三个已修复并有零模型门禁 + 19 项反例单测；**接线闭环仍未完成**（见"仍未处理"）。
- **通用性**：GENERAL-INVARIANT（提问只能针对"已声明、仅用户可给、且仍未知"的槽；问题文本由槽决定，不由领域主题词表决定；极性只能来自事实的类型字段）。
- **复现命令**：
  - `python scripts/proactive_policy_repro.py`（三个决策缺陷，修复前 `3/3 STILL BROKEN`，修复后 `0/3`）
  - `python scripts/proactive_coverage_audit.py data/simulations/stock_avg4_8u.json`（覆盖率）
  - `python scripts/_proactive_context_probe.py`（编译后的结构化上下文）
- **复现的缺陷（`ProactiveEngine` 旧实现）**：

  | 输入 | 旧提问 | 错因 |
  | --- | --- | --- |
  | 帮我推荐一本书 | 您更倾向什么口味？比如清淡、麻辣、烧烤等。 | 未识别领域时默认成 delivery |
  | 帮我预约理发 | 大概是几个人一起呢？有包间或其他要求吗？ | `预约` 被当作聚餐信号 |
  | 帮我买张出行票（记忆：不喜欢高铁，喜欢飞机） | 您之前偏好高铁出行，这次也一样吗？ | 对渲染后的记忆做子串匹配，不看极性 |
- **三个根因（都在"决策依据"层，不在模型）**：
  1. **领域默认成 delivery**：`propose_question` 里 `domain = domain or "delivery"`，随后 Pattern 6/7 就按 delivery 提问。
  2. **关键词表决定问什么**：`GROUP_DINING_KEYWORDS` 含 `预约`，于是理发被问人数；`DOMAIN_QUESTIONS` 是固定的每域问句表。
  3. **对渲染文本做子串匹配**：`_personalized_question` 用 `if kw in memory_text` 判断"之前偏好"。
     这**正是 E-059 的同一个不变量**（不得断言它不知道的极性），只是位置更高一层：E-059 在渲染层，这里在决策层。
     附带发现：`我不喜欢高铁，我喜欢坐飞机` 当时**一条结构化事实都没抽出来**（`不喜欢X`/`喜欢X` 无 `吃/喝` 时不在模式里），所以策略只能退回到子串匹配。
- **有效方案：改为缺口驱动。**
  - **只有已声明的缺口才提问**：`TaskSpec.unknown_slots` 是编译器自己的"必需但未给"清单。无缺口即不问——
    这就是"帮我推荐一本书"现在正确地不问的原因（它的 `required_slots` 为空）。
  - **问题文本由槽决定**：`SLOT_QUESTIONS` 以 schema 槽名为键，新增能力是改 schema，不是再加一个关键词。
  - **只读结构化上下文**：`QuestionContext` 不含任何渲染文本，`memory_text` 参数保留但**故意不使用**。
  - **工具/上下文能给的槽不问**：`product`/`shop_or_service`/`store`/`hotel`/`attraction` 归入不问；
    `address` 归入"上下文可解"（实测 **56/56** 个用户档案都含 `常住住址`/`工作地址`，且 `decision.profile_address` 已解析 家/公司 别名）。
  - 预算、待回答问题、答案关联**原样保留**（用户明确要求保留的部分）。
- **顺带修正的编译器缺陷**：
  1. **票务请求被路由成 delivery/retail**（E-041 复发）：`帮我买…的票` 里的 `帮我买` 是 delivery 标记，而裸 `票` 不在 ota 标记里，
     于是 `facet` 永远到不了 `travel`，transport 缺口永远不被声明。已加入带守卫的裸 `票` 判定
     （`_TICKET_FALSE_POSITIVES`：男票/女票/发票/股票/彩票/传票）并单测覆盖。
  2. **`room_type` 的判定标记含 `酒店`/`房`**：于是"帮我订个酒店"被判为"已给出房型"，真正要问的缺口被标成已填。已收窄为 大床/双床/标准间/套房/亲子房/海景房/城景房。
  3. **餐厅缺口没有 `taste` 槽**：为 delivery/restaurant 补上，并加了 `taste` 的存在标记。
  4. **`recommend` 动作一律没有必需槽**：这会让"咖啡+提神"这类**改变推荐本身**的缺口消失，已把这类槽提前到动作判定之前。
- **过度修正与撤回（重要）**：中途曾把 `product` 归入"仅用户可给"，覆盖率一度从 27% 升到 64%，
  但审计样例立刻暴露问题：`'帮我点个麻辣烫' -> '您想要哪一类的呢？'`——用户已经说了。
  编译器的 `_PRODUCT_CATEGORIES` 不含食物名，于是产生 **47 次重复提问**。这正是用户反对的"历史已明确仍机械重问"。
  已撤回：`product`/`shop_or_service` 重新归入不问。**这一步是靠审计样例发现的，不是靠推理。**
- **最终覆盖率（100 个唯一子任务定义，非 400 次 trial）**：

  | 指标 | 值 |
  | --- | --- |
  | 有已声明缺口 | 69 |
  | 提问次数 | **30（30%）** |
  | by_domain | delivery 16/72 · instore 4/11 · ota 10/17 |
  | by_action | commit 30/85 · **recommend 0/15** |
  | 槽位分布 | taste 15 · departure 5 · time 4 · quantity 4 · room_type 3 · transport 2 · date 1 · city 1 · caffeine 1 |
- **验证**：`agent/tests` **369 passed**（新增 `test_proactive_policy.py` 19 项反例门禁）；`archive` 35 passed；
  `compileall` / `ruff` / vendored `diff --exit-code` 通过；`scripts/_official_metrics.py` 仍为 `Avg@4=0.2925`。
- **移除的断言**：*"未识别领域时可以按 delivery 提问"*；*"领域主题词表可以决定问什么"*；
  *"记忆文本里出现某个词就说明用户偏好它"*；*"产品/服务需求可以由工具查到"*（工具能给目录，不能给用户意图）。
- **适用边界**：本条只改**提问决策**。**接线仍未完成**：stock 循环没有自动调用 `commit_question`，
  用户答复也没有自动进入事实库——即用户第 5 节列出的"主动提问还没有完整接通"。这三点必须在下一步单独处理与验证。
- **能力抽象**：proactiveness / missing-information detection。
- **下一步**：
  1. 接通闭环：问出 → `commit_question` → 答复关联到槽（而不是存成 `value="是的"`）→ 更新本轮状态 → 重新选择；
  2. `recommend` 动作 0 次提问是已知覆盖边界：需要为"推荐型任务缺主语"声明一个槽，但不得靠扩大词表实现；
  3. 之后再谈 A/B。

## E-069：主动提问没有接通——问出了没人记账，答复被存成"是的"

- **日期**：2026-09-13
- **状态**：PARTIAL。五处状态迁移已修复并有零模型门禁 + 22 项单测；**尚未在真实模型上跑过**，因此没有 smoke 或分数结论。
- **通用性**：GENERAL-INVARIANT（"问出"必须被记账、"答复"必须关联到槽、答复值必须是槽值而不是回复文本；观察者不得改动模型输出）。
- **复现命令**：
  - `python scripts/proactive_loop_repro.py`（修复前 `1/5` 未通过，修复后 `0/5`）
  - `python -c` 端到端接线 smoke（构造 agent，无模型调用）
- **背景**：用户第 5 节列出三件事——stock 循环没有自动提交"这个问题确实已经问出"；用户回复不会自动进入问题状态；
  对"这次仍然不加糖吗？"回答"是的"，结构化事实会存成 `value="是的"`。E-068 只改了**问什么**，本条接通**问出之后**。
- **可观察面（已核实，非推测）**：vendored `LLMAgent.generate_next_message(message, state)` 同时拿到入站消息与出站助手消息；
  `PersonalizationAgent.system_prompt` 每次访问都调 `memory.read(query=...)`，所以建议的问题确实以 `ASK:` 进入了模型上下文。
  `read()` 是纯函数、`update()` 只在子任务边界被调用，所以**子任务内的对话轮次没有任何其他观察点**——必须由一个 agent 子类观察。
- **五处状态迁移与门禁**：

  | 编号 | 迁移 | 修复前 | 修复后 |
  | --- | --- | --- | --- |
  | L1 | 确认问题的肯定答复 → 槽值 | 存成 `value="是的"` | 存成 `value="无糖", dimension="sweetness"` |
  | L2 | 无法解析的答复 → 不编造值 | 会把回复文本当偏好 | 不产生事实 |
  | L3 | 只在**真的问了**时消耗预算 | 无判定 | 逐字/改述判定，提及话题不算 |
  | L4 | 子任务指令不算答复 | 无保护 | 无 pending 时拒绝 |
  | L5 | 提议携带所属槽 | 只有问题文本 | `Proposal(question, slot, value, is_confirmation)` |
- **实现**：新增 `agent/proactive_agent.py::ProactiveLoopAgent`（stock 骨架的子类），只做两件事：
  入站用户轮次若存在 pending 问题则关联答复；出站助手轮次若**确实问了**提议的问题则 `commit_question`。
  它是**观察者，不是控制器**：不改写、不替换、不重排、不阻断工具调用或写入（E-042/E-048/E-049 的教训）。
  单测直接断言"观察者不修改模型消息内容"。
- **"真的问了吗"的判定**：先看整句是否被包含；否则要求**消息是疑问句**且命中**至少 2 个该槽在 `SLOT_QUESTIONS` 中的独有二元组**
  （独有性由槽表本身计算，不是手写词表）。**中途修正过一次**：最初用"整句被覆盖的比例"，把改述判成未提问——
  因为提议问句很长，通用框架词（"这次出行您想用哪种方式"）淹没了实质内容，而模型的改述更短。
  比例是错的统计量，改为绝对命中数 + 疑问句守卫后全部通过；反例（"高铁和飞机都可以，我看看。"）仍正确拒绝。
- **接口变更**：`ADAPTMemory.propose()` 返回 `Proposal`（`propose_question()` 仍返回字符串）；
  `commit_question(question, slot=, value=, is_confirmation=)`；`record_user_answer` 存**解析后的槽值**并把槽作为 dimension；
  `record_answer` 返回 `(slot, value)`。`ProactiveEngine.pending_question` 变成只读属性（内部改为 `pending: PendingQuestion`）。
- **接线**：`--agent proactive` 成为可选项（`choices=("stock","adapt","proactive")`），runner 在 `simulation.states["proactive_loop"]`
  保存逐子任务计数（问出/关联/解析出值），与 E-053 的教训一致：不能只写一次模拟级快照。
- **验证**：`agent/tests` **391 passed**（新增 `test_proactive_loop.py` 22 项）；`archive` 35 passed；
  `compileall` / `ruff` / vendored `diff --exit-code` 通过；`scripts/_official_metrics.py` 仍为 `Avg@4=0.2925`；
  `--help` 显示三个 agent 选项；端到端接线 smoke 得到 `questions_committed=1, answers_linked=1, facts=[('下午三点','time')]`。
- **移除的断言**：*"提议一个问题就等于问出了它"*；*"用户答复的文本就是偏好值"*；
  *"把不可解析的答复存下来总比丢掉好"*（对偏好而言，存错比不存更危险）。
- **适用边界**：**本条没有任何模型调用**，所以 L3 的判定只在构造样例上验证过，真实模型会不会用不同措辞提问**尚未测量**；
  `--agent proactive` 也还没有跑过 smoke。不能据此声称提分。
- **能力抽象**：proactiveness / missing-information detection / long-horizon consistency。
- **下一步**：
  1. 单用户小范围 smoke：跑 `--agent proactive`，确认 `proactive_loop` 计数在真实轨迹上非零且答复确实进事实库；
  2. 仍缺用户点名的"当前指令与历史偏好冲突 → 澄清冲突"路径：现在只会对**未知槽**提问，没有冲突澄清问句；
  3. 之后才谈 A/B。

## E-070：主动闭环的首次真实 smoke——确实问出了，但"委托"被当成偏好值

- **日期**：2026-09-13
- **状态**：PARTIAL。闭环在真实轨迹上确认可用；**发现并修复一个真实缺陷**；单子任务 reward=0.0，**不构成分数结论**。
- **通用性**：GENERAL-INVARIANT（"把决定交回给 agent"不是用户偏好证据；提问只应在槽表声明缺口时发生）。
- **复现命令**：
  - smoke：`python -m agent.vitabench_runner --agent proactive --cohort dev --num-trials 1 --task-ids M793481 --subtask-ids sub_M793481_1 --memory-type adapt --profile-summary --agent-llm qwen38-agent --user-llm qwen35-user --evaluator-llm qwen36-evaluator --debug-to data/traces/smoke_proactive_M793481_sub1.jsonl --save-to data/simulations/smoke_proactive_M793481_sub1.json`
  - 检视：`python scripts/_proactive_smoke_inspect.py data/simulations/smoke_proactive_M793481_sub1.json`
  - 缺口闭合：见 `agent/tests/test_proactive_loop.py::test_a_recorded_answer_closes_the_gap_and_stops_the_reask`
- **成本**：单子任务 435.8 秒；本地端点，配置成本 0。
- **闭环确认（真实轨迹）**：`states.proactive_loop = {questions_committed: 1, answers_linked: 1, answers_resolved_to_a_value: 1}`。
  轨迹显示模型**真的问了**出行方式（"您偏好高铁/动车，这次也是坐高铁去吗？还是想坐飞机？"），随后用户回复，闭环记账触发。
  这是 L3（"只在真的问了才消耗预算"）在**真实模型措辞**下的第一次检验，通过。
- **发现的缺陷：委托被当成偏好值。** 用户对出行方式问题回答 **"随便，你看着办吧。"**，
  `resolve_answer` 把它当成开放问题的答复文本，于是 `transport` 槽被赋值为整句委托语。
  "把决定交回给 agent"是**状态变化**，不是关于用户的证据——与 E-069 的 L1 同类，只是触发条件不同。
  **修复**：新增 `_is_delegation`，且**必须确实出现委托短语**才算（"随便/都行/你看着办/听你的/无所谓/你决定/不清楚…"），
  然后要求剥离后无实质内容。
  **中途修正**：第一版只看"剥离后是否为空"，把裸 **"好"** 也判成委托，破坏了一个正确的确认用例；
  加"必须出现委托短语"后修复。混合回复（"随便，就二等座吧"）仍保留其值。
- **零模型验证的闭环补全**：`test_a_recorded_answer_closes_the_gap_and_stops_the_reask` 验证
  答复 → 事实 `('坐高铁吧','transport')` → `resolve_preference_slots` 归一为 `{'transport':'高铁'}` → **缺口清空、不再重问**。
  这就是用户要求的"更新本轮状态 → 重新选择"。反向用例：委托答复**不**关闭缺口（`test_a_delegated_answer_leaves_the_gap_open`）。
- **顺带修正**：`taste` 槽的存在标记过泛——加入 `餐` 后 `'晚上给我点个双人餐外卖'` 被误判为"已给出口味"，
  使一个本来有效的提问消失（`test_vague_food_asks` 失败）。改为移除 `餐`、补 `云南菜/贵州菜/东北菜/湘菜/徽菜/本帮菜` 等具名菜系。
  覆盖率维持在 21%（`taste` 6 · `departure` 5 · `time` 4 · `quantity` 4 · `room_type` 3 · `transport` 2 · `date` 1 · `caffeine` 1）。
- **结构性限制（必须记住）**：闭环**只在 `--memory-type adapt` 下工作**。
  `RewriteMemory` 没有 `propose`/`commit_question`，子类会静默 no-op。
  因此"优先级 3 的闭环"与"保留 stock + RewriteMemory 作为保底"这两件事**不能同时成立**——
  这是一个需要用户裁决的架构取舍，不是实现细节。
- **验证**：`agent/tests` **397 passed**；`archive` 35 passed；`compileall` / `ruff` / vendored `diff --exit-code` 通过；
  `scripts/_official_metrics.py` 仍为 `Avg@4=0.2925`；`proactive_policy_repro` 0/3、`proactive_loop_repro` 0/5。
- **移除的断言**：*"用户对提问的任何文字回复都是可用的偏好值"*。
- **适用边界**：**1 用户 / 1 子任务 / 1 trial，reward=0.0**。这既不是 smoke 通过也不是失败：
  该子任务是主动隐藏意图型，单次结果不能说明闭环是否提分。**不得引用为任何分数证据。**
- **能力抽象**：proactiveness / missing-information detection / long-horizon consistency。
- **下一步**：闭环已可用，但仍缺用户点名的"当前指令与历史偏好冲突 → 澄清冲突"问句；
  以及 §6 要求的 A/B（在确认值得花预算之前不做）。

## E-071：冲突澄清问句服务的人群是零——不建

- **日期**：2026-09-13
- **状态**：`VERIFIED`（负面结论）。**建议不实现**；人群为零是本条的全部结论。
- **通用性**：GENERAL-EMPIRICAL（在固定 8 用户基线的 100 个唯一子任务定义上测量）。
- **复现命令**：`python scripts/instruction_conflict_audit.py data/simulations/stock_avg4_8u.json`
- **背景**：用户点名的提问动机之一是"当前指令和历史偏好冲突 → 必要时澄清冲突"。E-068 只做了"未知槽"提问，
  没有冲突澄清。按项目方法论，先量人群再决定是否实现。
- **先核实到的既有事实**：`agent/memory/slots.py::resolve_preference_slots` **已经实现了优先级规则**——
  第 116–118 行把当前指令解析出的槽从历史结果中移除（"Explicit current-task values always outrank historical resolution"）。
  也就是说，指令本来就静默压过历史，冲突澄清只在"用户希望这次改变持续下去"时才有额外价值。
- **实测（100 个唯一子任务定义，非 400 次 trial）**：

  | 指标 | 值 |
  | --- | --- |
  | 记忆解析出至少一个类型化槽 | 35 |
  | 指令明确给出至少一个类型化槽 | 54 |
  | 两者**一致** | 3 |
  | 两者**冲突** | **0** |

- **结论**：冲突澄清问句在本质检点上服务的案例数为 **0**。实现它只会增加提问表面积、增加误问风险，
  不会改变任何一个子任务的结果。**因此不实现**，并把本条作为"提议的功能先量人群"的正面例子。
- **适用边界（必须一起引用）**：检测范围**受限于编译器的类型化槽词汇**——只有 `room_type / transport / taste /
  caffeine / size / budget`（`slots._CANONICAL_MARKERS`）加上 `spec.resolved_slots`（目前只有 address 与 caffeine）
  可比较。`sweetness / temperature / topping` 等维度**不在**该词汇内，两侧都无法映射成槽，所以本条的
  "0 冲突"**只对它覆盖的词汇成立**。要覆盖更宽的冲突类，需要先扩展 `_RESOLVABLE_PREFERENCE_SLOTS`；
  那是另一项独立改动，需单独预注册与测量（并会扩大提问表面积，风险方向与本条相反）。
- **移除的断言**：*"指令与历史冲突是值得主动澄清的常见情形"*——在本题材上它不常见，实测为零。
- **能力抽象**：proactiveness（**作为反例**：一个听起来合理但人群为零的提问动机）。

## E-072：优先级 4 的判定——防循环机制的可改善上限不足以成为路径

- **日期**：2026-09-13
- **状态**：PARTIAL（ThrashGuard 有量化判定；**EvidenceAgent 无任何产物，无法判定**）。
- **通用性**：GENERAL-EMPIRICAL（固定 8 用户基线聚合 + 单元内对照）。
- **复现命令**：
  - `python scripts/runaway_autopsy.py data/simulations/stock_avg4_8u.json --repeat-threshold 3`
  - 自己的 smoke：`data/simulations/thrash_guard_smoke_U000828_s44.json`（`states.thrash_guard.events == []`）
- **ThrashGuard 的量化**：
  - 人群：`thrashing_subtasks = 13/400`，`thrashing_clusters = 7/100`。
  - **上限**：`max_cohort_avg_gain_if_all_rescued = 0.0325`——即使 13 次抖动**全部**被救回也只得 +0.0325。
    目标是 +0.0575（0.2925 → 0.35），所以**单靠防循环无法达标**。
  - 该上限还假设 100% 有效；实际实现是**一次提示**而非硬性禁止，效果只会更小。
  - 风险侧：`passing_subtasks_that_would_fire = 0`——不会误伤已通过的子任务，这一点是好的。
  - 单元内对照：抖动 trial 通过 0/13，非抖动 trial 通过 6/15，Fisher 双侧 p=0.0178。**但这只是相关**：
    抖动更可能是"模型卡住"的症状而不是原因，抑制重复调用并不告诉模型该做什么（与 E-053 同一推理陷阱，
    也与 E-056"单元内对照成立、因果未验"一致）。
  - 它自己的 smoke（U000828，14 个子任务）里 **`events == []`**：真实轨迹上一次都没触发。
  - 另有 `runaway`（max_steps 终止）口径：`upper_bound_delta = 0.0`——该类运行的平均奖励（0.3077）
    反而略高于非 runaway（0.2931），该人群**没有可改善空间**。
  - **判定**：保留为**有界可选机制**，但**不得计入能力或分数预期**；不推进为默认。
- **EvidenceAgent**：
  - 用户已说明其冒烟中断、只有前两项 0/1 评分、无完整用户结果。本轮核实：仓库里**没有任何** EvidenceAgent 评分产物——
    `data/simulations/smoke_after_deletion.json` 的 `info` 是 `agent_kind: "stock" / memory_type: "rewrite"`，**不是**该分支。
  - 因此正确表述是 **UNRESOLVED：没有测量，无法判定收益**。既不能保留为"已验证有效"，也不能声称无效。
  - **判定**：维持"实验分支"定位（用户已如此要求），**不得成为新的默认复杂度**，也不得在文档中被描述为有收益。
- **移除的断言**：*"防循环 / 额外核对是通往 0.35 的路径之一"*——按实测上限，前者单独不够，后者无测量。
- **适用边界**：本轮**没有运行**任何新模型调用（除 E-070 的单子任务 smoke 外），全部结论来自既有产物与零模型命令。
- **能力抽象**：execution / bounded exploration（**作为反例**：机制存在不等于收益存在）。

## E-073：最大的机械清晰失败面是"问了用户然后被放弃"——重新定位候选证据视图

- **日期**：2026-09-13
- **状态**：PARTIAL。人群已量化；**干预尚未预注册、未测量**。
- **通用性**：GENERAL-EMPIRICAL（固定 8 用户基线，400 次运行，仅读工具调用与消息文本）。
- **复现命令**：`python scripts/no_write_audit.py data/simulations/stock_avg4_8u.json`
- **动机**：E-066 把"目标商品已打印但仍失败"的 135 次拆成"绑定了别的已见 id（57）"和"一个 id 都没绑（60）"。
  后者更大且未解释，且可能不是模型选择而是机械缺陷。本轮把它拆开。
- **实测结果（400 次运行）**：

  | 指标 | 值 |
  | --- | --- |
  | 失败 ∧ 目标已打印 ∧ 未绑定任何其它 id | **60**（占 400 的 15%，占 279 次失败的 21.5%） |
  | 形态 | **`no_write_at_all` 60/60**——一次写入都没有尝试 |
  | 指令要求的动作 | **commit 56** / recommend 4 |
  | 域分布 | delivery 48 · instore 10 · ota 2 |
  | 终止原因 | user_stop 53 · max_steps 7 |
  | **运行如何结束** | **`last_turn_asks_the_user` 60/60** |
  | 助手轮数 | 平均 9.9（最少 3，最多 51） |
  | 尝试过的写入工具 | **无** |
- **结论**：这不是"推荐任务不需要写入"（用户诊断文档提醒过的那种误读）——**56/60 的指令明确要求交易，而 agent 从未尝试写入**。
  形态高度一致：agent 在最后一轮向用户提问，用户没有回答就结束了对话（53/60 `user_stop`）。
  这是"先问后做"的失败形态，也是目前找到的**最大且机械清晰**的失败面。
- **为什么不应"修"它**：不能通过强制写入或禁止提问来修。仓库已用受控实验反复否证过这条路——
  E-042（控制器替模型决定净负 `LOST 7 : GAINED 1`）、E-048（框架罐头提问 + 推荐定稿在"先问后做"单元净负）、
  E-049（一到候选就强制 CREATE）、E-050（撤掉框架提问后 NEED_INFO 变成硬死路）、E-053（"从不写入"是症状不是瓶颈）。
  本条**不主张**任何控制层动作。
- **它真正改变的东西：候选证据视图的定位。**
  E-066/E-067 用**反驳**信号（`violated`）评判该视图，结论是"触发门禁不通过"（指令口径 violated = 0）。
  但本条显示，占主导的失败人群不是"选错"，而是**在信息其实已经足够时仍然去问用户**。
  该视图可用的信号恰恰是**确认**（指令口径 4172 个 `SATISFIED`，约每次运行 25 个），而不是反驳。
  也就是说：**被 E-067 判为"价值上限低"的那一半信号，可能正对着最大的失败面。**
- **这仍是一个假设，不是结论**：没有任何测量表明"显示候选与约束的满足关系"会减少模型提问。
  模型提问的原因可能是它认为信息不足，也可能是它在执行"先确认再下单"的策略；两者的干预方式不同。
  在预注册并测量之前，不得据此声称提分，也不得据此接线。
- **移除的断言**：*"'一个 id 都没绑'意味着选错了候选"*——它意味着**根本没走到选择**；
  以及 *"该视图的价值主要在标出冲突"*（E-066 据此下的结论需要重新表述为"冲突信号稀少"，
  但"确认信号"这一路的评估**尚未做过**）。
- **适用边界**：60 次运行只覆盖"目标商品已打印"的子集；"目标未打印"的 161 次里同类形态未测。
- **能力抽象**：proactiveness / missing-information detection（**作为反例方向**：问题不是问得太少，而可能是问得太多）。
- **下一步（预注册草案）**：单一假设——"在保留 stock + RewriteMemory 的前提下，把候选与**当前指令约束**的满足关系作为附加观察呈现，
  是否减少'问了用户然后被放弃'的运行数"。主判据：官方 unit 上的 paired fixes/breaks；
  次判据：`no_write_at_all` 且 `last_turn_asks_the_user` 的运行数是否下降（本条提供基线 60）。
  **仍受一条结构限制**：该视图的约束来源是 `card.constraints`/`card.prefer`，其中 `card.prefer` 来自结构化记忆；
  纯 RewriteMemory 配置下只剩指令侧的 164/400 覆盖，需先确定约束来源才能预注册。

## E-074：在冻结配置下确认信号干预不可行——结构化能力都依赖 ADAPTMemory

- **日期**：2026-09-13
- **状态**：`VERIFIED`（负面结论，可零模型复现）。**在 `stock + RewriteMemory` 下不预注册该干预。**
- **通用性**：GENERAL-EMPIRICAL（固定 8 用户基线，400 次运行）。
- **复现命令**：`python scripts/confirmation_feasibility.py data/simulations/stock_avg4_8u.json`
- **要解决的前置问题**（E-073 下一步）：E-073 提出"把候选与当前指令约束的满足关系作为附加观察，减少'问了用户然后被放弃'的运行"。
  在冻结配置（stock + RewriteMemory）下没有结构化记忆，唯一可用的约束来源是 `TaskSpec.compile(instruction)`。
  必须先量出这个来源能覆盖 E-073 人群的多少——**约束为空的运行，视图无话可说，干预碰不到它**。
- **实测结果（E-073 的 60 次运行）**：

  | 指标 | 值 |
  | --- | --- |
  | 人群 | 60 |
  | 能从指令得到**任何**约束 | **21（35%）** |
  | 其中含硬约束 | 21 |
  | 约束来源 | instruction 19 · constraint 2 |
  | **在 400 次运行中的可达上限** | **21 次（5.3%）** |

- **而且这 21 次里的约束质量很差**。样例（`confirmation_feasibility.py` 输出）：

  | 指令 | 编译出的"约束" |
  | --- | --- |
  | 一到下午猪瘾就**犯了**，给我送个巧克力到单位来~ | `('犯了', require, hard)` |
  | 晚饭吃完想喝点什么，帮我点杯喝的到宿舍来 | `('饭', require, hard)` |
  | 我决定周六去普吉岛玩三天了…你帮我订个**酒店**吧 | `('酒店', require, hard)` |
  | 好热，帮我点个**奶茶**送到家 | `('奶茶', require, hard)` |

  前三条是**伪约束**（`犯了`/`饭`/`酒店` 分别是分词残留、泛化名词与容器名词，都不是"用户要求某属性"）。
  把它们当作"满足／未知"显示出来，等于往上下文里注入噪声——正是 E-014"规格词被误当商品类别证据"的同类问题。
- **结论**：该干预在冻结配置下**不可行**。可达上限是 400 次中的 21 次（5.3%），且其中很大比例是伪约束。
  即使假设 100% 有效（不可能），也只有约 +0.0525，而伪约束会带来反向损害。
  **它要求的约束来源是结构化记忆，不是当前指令。**
- **与 E-070 汇合的同一条结构性障碍**：到本轮为止，仓库里所有已实现的**结构化**能力
  ——三值约束对应（`correspondence.py`）、主动提问闭环（`ProactiveLoopAgent` + `propose/commit_question`）——
  **都只在 `--memory-type adapt` 下工作**：
  - 闭环：`RewriteMemory` 没有 `propose`/`commit_question`，子类静默 no-op（E-070 已记录）；
  - 确认信号：`RewriteMemory` 不产生结构化事实，约束只剩指令侧，且质量差（本条）。
  而冻结的对照臂恰恰是 `stock + RewriteMemory`。
  **因此"一次只验证一个假设、保留 stock + RewriteMemory 作保底"与本仓库已建成的能力在结构上不相容。**
  这不是实现细节，是一个必须由用户裁决的架构取舍。
- **移除的断言**：*"候选证据视图可以在不改动记忆层的前提下接线"*——在冻结配置下它最多触及 5.3% 的运行，且噪声大于信号。
- **适用边界**：本条只否证**在冻结配置下**的可行性；它**不**否证"结构化记忆 + 候选证据视图"这一组合，
  也不否证该组合值得测量。两者是不同的假设，后者尚未预注册。
- **能力抽象**：utilization（**作为反例**：能力存在但接入点与对照臂不相容）。

## E-075：裸「就」被当成选择标记——伪硬约束进入 card.must

- **日期**：2026-09-13
- **状态**：PARTIAL。主类已修复并有单测；一个残留子类已记录。
- **通用性**：GENERAL-INVARIANT（选择义由**子句位置**决定，不由词表决定）。
- **复现命令**：`python -m pytest agent/tests/test_agent_architecture.py -q -k mid_clause_jiu`
- **发现路径**：E-074 为可行性做测量时，样例暴露 `'一到下午猪瘾就犯了…' -> ('犯了', require, hard)`。
  根因是 `_EXACT_ENTITY_RE` 把**裸「就」**列为选择标记（`(?:就选|指定|要的是|就)`），
  而「就」在中句是极常见的副词（"猪瘾就犯了"、"就到了"），于是它后面的一切都被捕获成"精确实体"硬约束，
  并进入 `card.must`，被所有下游消费者读到（E-074 测到的正是它对候选对应视图的污染）。
- **修复：结构性判据而非词表。** 选择义的「就」出现在**子句起始**（串首或标点之后）或**选择动词之后**
  （就选/就要/就来/就订/就买）；中句的「就」不匹配。用一个零宽断言实现，没有引入任何动词/停用词表。
- **中途自我否证**：第一版直接删掉裸「就」只保留选择动词，**打破了一个合法用例**——
  `test_task_spec_does_not_define_hidden_evaluation_fields` 用的 `'就糯糯青山吧，少糖多冰，送到公司'`
  正是标准的选择句式，实体 `糯糯青山` 是应该被抽到的。测试正确地抓住了这个回归。
  改为子句位置判据后，两种情形同时成立。
- **验证**：`'就糯糯青山吧…' -> entity=['糯糯青山']`（保留）；`'猪瘾就犯了…' -> entity=[]`（消除）；
  `'就要那家大床房吧' -> entity=['那家大床房']`（保留）。`agent/tests` **399 passed**（+2）；
  `compileall` / `ruff` / vendored `diff --exit-code` 通过。
- **已知残留（未修，勿当成已解决）**：**子句起始**的副词「就」仍会漏网——`'就到了，帮我订酒店' -> entity=['到了']`。
  把它与 `'就X吧'` 区分需要词性信息，而本项目拒绝为此扩词表。当前判据已消除中句这一大类（E-074 报告的那些）。
  另注：`_PRODUCT_CATEGORIES` 仍含 `'饭'`/`'酒店'`/`'汤'` 这类过泛词，会产出 `('饭', category)` / `('酒店', category)`
  伪约束；本条**未**处理，属独立的一轮清理。
- **移除的断言**：*"选择义的「就」可以用一个词表枚举"*。
- **能力抽象**：utilization / execution（约束来源的保真度）。

## E-076：`_PRODUCT_CATEGORIES` 的过泛词在制造伪约束

- **日期**：2026-09-13
- **状态**：PARTIAL。四个过泛词已移除并验证；其余待审。
- **通用性**：GENERAL-INVARIANT（"商品类别"必须指用户想要的东西；渠道名与容器名不是类别）。
- **复现命令**：脚本见下（对 771 个子任务定义统计每个词实际命中的指令）。
- **E-075 记录的待办，本轮处理。** `_PRODUCT_CATEGORIES` 里含 `饭 / 外卖 / 酒店 / 汤`，它们不是商品类别，
  却在最常见的指令上产出伪 `category` 硬约束。实测命中次数（771 个子任务定义，每个指令只记第一个命中词）：

  | 词 | 命中 | 典型指令 | 判定 |
  | --- | --- | --- | --- |
  | `饭` | **50** | 中午**饭**就帮我点个外卖来单位算了 | 伪（分词残留） |
  | `酒店` | **49** | 帮我定个**酒店** | 伪（容器，不是商品） |
  | `外卖` | 15 | 最好能点**外卖** | 伪（渠道，不是商品） |
  | `汤` | 8 | 给我买点适合炖**汤**的食材 | 伪（请求是食材） |
  | `套餐` | 31 | 帮我下单一个足浴**套餐** | 真 |
  | `奶茶`/`咖啡`/`衣服`/`机票`/`景点`/`门票` | — | — | 真 |

  `汤锅`/`火锅` 保留（它们指具体菜品）；凡真正属于口味维度的由 `taste` 槽自己的标记处理。
- **修改与验证**：移除 `饭 / 外卖 / 酒店 / 汤`。
  - `'中午饭就帮我点个外卖来单位算了' -> must=[('authorization','create')]`（伪约束消失）
  - `'帮我定个酒店' -> must=[('authorization','create')]`（伪约束消失）
  - `'珍珠奶茶' -> ('category','奶茶')` 保留；`'汤锅' -> ('category','汤锅')` 保留
  - `agent/tests` **399 passed**；`compileall` / `ruff` / vendored `diff --exit-code` 通过。
- **下游影响（诚实记录，方向是"触及更少但更准"）**：
  `confirmation_feasibility.py` 的可达上限由 **21 次 → 18 次**（400 次运行的 5.3% → 4.5%）。
  即原先那 21 次里有 3 次是**靠伪约束才"达标"**的。移除噪声会降低名义可达性，这是正确的方向：
  该视图本就不该在 4.5% 的运行上被认真期待效果，E-074 的不可行结论因此**更强**，不是更弱。
  主动提问覆盖率不受影响（21 次提问，槽位分布不变）——说明这次改动没有附带损伤。
- **移除的断言**：*"'外卖'/'酒店'/'饭' 可以作为商品类别证据"*。
- **适用边界**：本条只清理了 `_PRODUCT_CATEGORIES`。同类风险仍存在于其他词表
  （`_FACET_MARKERS`、`_ATTRIBUTE_TERMS`、`_DOMAIN_MARKERS`），尚未逐一按命中次数审查。
- **能力抽象**：utilization（约束来源的保真度）。

## E-077：过泛词问题是 `_PRODUCT_CATEGORIES` 特有的，不是普遍现象

- **日期**：2026-09-13
- **状态**：`VERIFIED`（负面结论）。**不需要修改**。
- **通用性**：GENERAL-INVARIANT（同一个词在不同层的语义不同；"过泛"只在"该词必须指用户想要的东西"这一层才有害）。
- **复现命令**：对 771 个子任务定义统计 `agent/memory/facts.py::_FACET_MARKERS` 每个词的命中（同 E-076 的口径）。
- **动机**：E-076 清理了 `_PRODUCT_CATEGORIES` 的过泛词，并留下待办"同类风险仍在 `_FACET_MARKERS` / `_ATTRIBUTE_TERMS` / `_DOMAIN_MARKERS`"。
  本轮先量 `_FACET_MARKERS`。
- **实测命中（前 16）**：`restaurant/吃` 82 · `hotel/酒店` 50 · `beverage/喝` 45 · `restaurant/饭` 44 ·
  `retail/水果` 29 · `beverage/奶茶` 24 · `restaurant/餐厅` 23 · `flight/机票` 21 · `restaurant/菜` 19 ·
  `beverage/杯` 13 · `attraction/景点` 12 · `restaurant/外卖` 11 · `beverage/咖啡` 10 · `train/高铁` 9 ·
  `train/车票` 9 · `wellness/按摩` 8。
- **结论：这些是合理的，不应修改。** 关键在于**同一个词在两层里的语义不同**：
  - `_PRODUCT_CATEGORIES` 的用途是"用户想要**什么商品**"，所以 `饭`/`酒店`/`外卖` 是伪的（E-076）；
  - `_FACET_MARKERS` 的用途只是"这属于**哪一类场景**"，`饭`→restaurant、`酒店`→hotel 是**正确**的分类。
  "过泛"不是词的属性，而是"该词所在那一层对它的要求"的属性。
- **据此收窄 E-076 的待办**：`_ATTRIBUTE_TERMS` 与 `_DOMAIN_MARKERS` 需要按各自层的语义分别审查，不能因为
  `_PRODUCT_CATEGORIES` 有问题就推定它们也有问题。`_ATTRIBUTE_TERMS` 的语义接近"用户要求的规格"（与
  `_PRODUCT_CATEGORIES` 同类，**值得查**）；`_DOMAIN_MARKERS` 的语义接近"这属于哪个域"（与 `_FACET_MARKERS` 同类，风险低）。
- **移除的断言**：*"一张词表出现过泛词，其他词表就也有同类问题"*。
- **能力抽象**：不适用（信息保真度的分层语义）。

## E-078：`_ATTRIBUTE_TERMS` 没有过泛词，但召回很低

- **日期**：2026-09-13
- **状态**：`VERIFIED`（负面结论）。**不需要按 E-076 的方式修改**；低召回是另一个方向的问题。
- **通用性**：GENERAL-EMPIRICAL（771 个子任务定义）。
- **复现命令**：统计 `agent/decision.py::_ATTRIBUTE_TERMS` 每个词在 771 个指令中的命中。
- **实测**：24 个词中**只有 6 个命中过**，合计 18 次：

  | 词 | 命中 | 典型指令 |
  | --- | --- | --- |
  | 高铁 | 9 | 帮我买张上午的**高铁**票 |
  | 大床房 | 5 | 帮我订四晚酒店吧，要**大床房** |
  | 常温 | 1 | 帮我买份糖水送家来，要广式的，**常温** |
  | 飞机 | 1 | 你帮我订清明节当天的**飞机**吧 |
  | 经济舱 | 1 | （返程票） |
  | 热饮 | 1 | 帮我点个**热饮**送到家里暖暖身体 |

  **从未命中（18 个）**：`少糖 / 半糖 / 无糖 / 正常糖 / 多冰 / 少冰 / 去冰 / 双床房 / 海景房 / 城景房 /
  靠窗 / 靠过道 / 二等座 / 一等座 / 商务舱 / 火车 / 低咖啡因 / 高咖啡因`。
- **结论**：
  1. **没有过泛词**——命中样例全部是用户真实提出的规格，`_ATTRIBUTE_TERMS` 不属于 E-076 那一类问题。
  2. 但它**召回很低**：本题材的指令极少以这些字面形式给出规格。这与 E-067 的结论一致
     （"真实被评分的条件大多只以自然语言出现在指令里，而编译器并未把它们编译成结构化要求"）。
  3. 因此 E-067 指出的瓶颈是**召回**，不是**精度**。E-076 修的是精度，本轮确认精度侧已无同类问题。
- **据此关闭词表审查线**：`_PRODUCT_CATEGORIES`（精度，已修 E-076/E-077）与 `_ATTRIBUTE_TERMS`（召回，无需改）
  两侧都已量过，`_FACET_MARKERS`/`_DOMAIN_MARKERS` 按 E-077 判定为低风险。**继续逐词表清理的边际价值已经很低。**
- **移除的断言**：*"编译器词表普遍存在过泛问题"*——两个方向都量过，只有一处（product categories）成立。
- **能力抽象**：utilization（**作为边界说明**：这一层的瓶颈是召回，靠扩词表解决会撞上用户明确反对的方向）。

## E-079：约束召回提升 2.8 倍，但反驳信号仍为零——根因是槽名与候选字段名不一致

- **日期**：2026-09-13
- **状态**：PARTIAL。召回改进已落地并验证；**E-067 的 `violated = 0` 未被解决**，但根因已定位到下一层。
- **通用性**：GENERAL-INVARIANT（编译器槽名与候选 schema 字段名是两个独立词表；要求二者字面相等是隐含断言）。
- **用户授权**：本轮用户裁决"不跑评测，先扩宽约束召回（零模型）"。
- **复现命令**：
  - 召回：`python -c` 统计 771 个定义中 `TaskSpec.compile(...).must` 里 kind='attribute' 的数量与带 `attribute_key` 的数量
  - 下游：`python scripts/correspondence_trigger_audit.py data/simulations/stock_avg4_8u.json`
- **做法（不加任何新词表）**：`agent/memory/slots.py::_CANONICAL_MARKERS` 里**早已存在** `大床→大床房`、`动车→高铁`、`提神→高咖啡因`
  这类"标记→规范值"映射，但**指令侧的约束提取从未使用它**——所以「要大床的」抽不出 `room_type` 约束。
  新增 `_stated_slot_constraints()` 复用这张既有表，并按 facet 限定适用范围。
- **结果**：

  | 指标 | 修改前 | 修改后 |
  | --- | --- | --- |
  | 有 attribute 约束的定义 | 18 | **51（2.8×）** |
  | 其中带 `attribute_key` | 概念不存在 | **33** |
  | `resolved_share_of_all_pairs` | 0.2765 | **0.387** |
  | `satisfied` 对 | 4172 | **4378** |
  | **`violated` 对** | **0** | **仍是 0** |

  样例：`'咖啡提神'→('高咖啡因','caffeine')`、`'要大床的'→('大床房','room_type')`、`'来回机票'→('飞机','transport')`。
- **中途自查出的两个自己的 bug（都靠样例发现，不是靠推理）**：
  1. 抑制重复时用了双向子串比较，而**类别值 `咖啡` 是规范值 `高咖啡因` 的子串**，把合法的 caffeine 要求静默杀掉了。
     改为比较**槽键**（`attribute_key`）而非子串。
  2. 不做 facet 限定会产出假阳性：`'机票已经买好了，你帮我订个酒店吧'` 里的 机票 是**过去购买**，
     抽出 `transport=飞机` 要求是错的。已按 facet 限定（transport 只在 travel/train/flight，room_type 只在 hotel 等）。
- **关键负面结果：`violated` 仍为 0。根因已定位。**
  本模块的反驳规则要求**同一 `attribute_key`** 上出现不同值。但编译器用的槽名来自 `_CANONICAL_MARKERS`
  （`room_type` / `transport` / `caffeine`…），而候选侧的键来自工具返回的 `attributes=` 字段
  （实际可能是 `房型` / `type` / 别的拼写）。**两套词表没有对齐**，所以"同键不同值"几乎从不成立，
  `REQUIRE` 依旧只能确认、不能反驳。
  这是一个**隐含断言**：*"编译器的槽名与候选 schema 的字段名字面相等"*——它从未被验证，也不成立。
- **下一步（已明确、未实现）**：反驳不要求键字面相等，而要求**同一规范槽族**——即候选打印的某个值
  经 `_CANONICAL_MARKERS` 映射后落在**同一槽**、且规范值不同（如要求 `room_type/大床房`，候选打印 `双床房`）。
  这**复用同一张既有表，不引入新词表**，符合用户"不要继续扩词表"的方向。
  **注意**：本条**没有**验证该做法会产出多少 `violated`；在落地前不得假定它会解决问题。
- **移除的断言**：*"给约束补上 attribute_key 就能实现反驳"*（E-067 的下一步假设）——补上键是必要条件，但不充分，
  还要求两侧键名对齐。
- **适用边界**：本轮为纯零模型改动，`Avg@4` 未变（0.2925）。召回提升**不等于**分数提升。
- **能力抽象**：utilization（约束来源的召回）。

## E-080：长序列记忆行为套件——当场抓出两个真缺陷

- **日期**：2026-09-13
- **状态**：PARTIAL。套件已落地（15 条场景）；它暴露的两个缺陷已修复并锁定。
- **通用性**：GENERAL-INVARIANT（否定的作用域覆盖其后的动词；"否认 + 给出新值"必须保留新值）。
- **复现命令**：`python -m pytest agent/tests/test_memory_longitudinal.py -v`
- **动机**：本会话此前修了多个记忆层缺陷（E-059/060/061/075/076），但验证是零散单测，
  没有"长序列偏好演化"的成套场景。这条把散落修复升级为**可复用的验证方法**，同时继续找同类缺陷。
- **套件形态**：每条测试是一个**偏好时间线**（多轮带日期的用户发言），断言记忆"相信什么"与"渲染什么"。
  覆盖：极性、硬条件完整性、子句边界、多值集合累积、作用域隔离、当前指令优先、当前纠正、
  委托、缺口闭合、长序列卫生（read 纯函数 / 早期安全事实不被后续轮次抹掉 / 重复交互不重复记账）。
- **首轮运行 5 处失败——这正是套件的价值。分类如下（诚实区分"我的期望错"与"真缺陷"）**：

  **真缺陷 1（严重）：明确的不喜欢被解析成正向偏好。**
  `'我不喜欢吃香菜。'` → `('香菜', 'positive', 'like')`。
  LIKE 模式 `(?:我喜欢吃|我爱吃|…)` 在 `我不喜欢吃` 里匹配到子串「喜欢吃」，于是把用户的**明确厌恶**记成了偏好，
  并会渲染到 `PREFER`。与 E-059 同类，但在**提取层**而非渲染层。
  修复：新增后缀 dislike 模式（`我不喜欢/不爱吃/不爱喝` + 可选吃/喝）使极性正确；
  并让 LIKE 循环检查匹配前 **4 字符窗口**内是否有否定（不/别/没/勿/拒绝）——标记可能距否定一两个字符（"我不喜欢…"），
  单字符 lookbehind 看不到。

  **真缺陷 2：「否认 + 给出新值」时新值被丢弃。**
  对确认问题回答 `'不是，这次少糖'`，`resolve_answer` 只看到 `_is_negative` 为真就返回空值，
  于是**用户刚给出的纠正被扔掉**——正是用户最初报告的"当前纠正没有及时进入决策"。
  修复：否认后剥掉否定词与语气词，若仍剩 ≥2 字则作为新值；再剥掉一小段**话语框架词**
  （这次/改成/换成…，属话语层、与领域无关，刻意保持极小）。

  **我的期望错 3 处**（记录以免后人误判）：
  - `compile_task()` 返回 `DecisionCard` 而非 `TaskSpec`，`resolved_slots` 要用 `TaskSpec.compile`；
  - 委托后**同一问题按设计不再重复**（CLAUDE.md：同一问题只计一次），所以可观察量是"缺口仍开、换个槽还能问"，
    不是"同一问题回来"；
  - 解包 `memory.facts` 时误当元组（它是 `PreferenceFact`）。
- **验证**：套件 **15 passed**；全量 `agent/tests` **414 passed**（+15）；
  `compileall` / `ruff` / vendored `diff --exit-code` 通过；`Avg@4` 未变（零模型改动）。
- **移除的断言**：*"'喜欢'这个子串出现就说明用户喜欢"*——否定的作用域覆盖其后的动词；
  *"确认问题的否定回答一定没有携带新信息"*。
- **适用边界**：套件断言的是**记忆层行为**，不是任务成功率。它提高的是"这类缺陷不会再悄悄回来"，不是分数。
- **能力抽象**：preference extraction / updating / long-horizon consistency。

## E-081：长序列工具调用画像——把散落审计整合成一份可读报告

- **日期**：2026-09-13
- **状态**：PARTIAL（描述性统计，非因果）。整合完成，可一键复现。
- **通用性**：GENERAL-EMPIRICAL（固定 8 用户基线；纯机械计数，无 rubric、无 reward 信号、无 `user_intention`）。
- **复现命令**：`python scripts/longhorizon_report.py data/simulations/stock_avg4_8u.json`
- **动机**：本会话此前的工具行为证据散在 `runaway_autopsy` / `target_reachability` /
  `candidate_selection_audit` / `no_write_audit` 四个脚本里，无法一次讲清"agent 在长任务上到底怎么用工具"。
  本条把它整合成单一报告。
- **实测（400 次可评子任务运行，共 2981 次工具调用）**：

  **对话形态**

  | 指标 | mean | median | p90 | max |
  | --- | --- | --- | --- | --- |
  | assistant 轮数 | 9.3 | 7 | 13 | **51** |
  | 每次运行工具调用数 | 7.5 | 4 | 12 | **99** |
  | 每次运行使用的不同工具数 | 3.5 | 3 | — | — |

  **工具集中度（前 6）**：`delivery_product_search_recommand` 409 ·
  `instore_product_search_recommend` 396 · `get_delivery_store_info` 224 ·
  `longitude_latitude_to_distance` 222 · `get_date_holiday_info` 213 · `get_ota_hotel_info` 210。
  搜索类工具占主导。**`query_preference_memory` 被调用 97 次**——模型确实会主动查记忆，不是只读注入的卡片。

  **重复与结果质量**：同一调用签名在一轮内重复 ≥3 次的有 **13 次运行（3.25%）**，最坏单轮重复 **48 次**；
  工具结果看起来为空或报错的占 **303/2962（10.2%）**（窄文本启发式，是**下界**）。

  **展开与决策**：尝试过写入的 **271/400（67.75%）**；打开过父候选详情的 **152/400（38%）**；
  首次写入发生在第 **6** 轮（中位数），p90 = 9，最晚 35。

  **失败分类（求和 = 400）**：

  | 类别 | 次数 |
  | --- | --- |
  | 目标商品从未被打印 | 144 |
  | 通过 | 117 |
  | 写了但不是目标 | 75 |
  | **问了用户然后被放弃** | **60** |
  | 无商品目标 | 4 |
- **可讲的三个点**：
  1. **长尾极端长**：中位数 4 次调用，但 p90 是 12、最长 99；轮数中位数 7、最长 51。平均值掩盖了尾巴。
  2. **三分之一的运行从未尝试写入**（400 − 271 = 129），其中 60 次明确是"问完等用户、用户走了"。
  3. **记忆工具被真实使用**（97 次），说明个性化路径不是死代码。
- **移除的断言**：*"平均调用数可以代表长序列行为"*——中位数与 p90 差 3 倍，必须报分布。
- **适用边界**：`looks_empty_or_error` 是窄启发式；失败分类只对"目标是商品"的子任务成立；
  全部为**描述性**统计，不含因果，也不构成分数。
- **能力抽象**：execution / bounded exploration / long-horizon consistency（**画像**，非干预）。

## E-082：在线自适应记忆演示——"自进化"的诚实版本

- **日期**：2026-09-13
- **状态**：PARTIAL（演示，非效果测量）。
- **通用性**：GENERAL-EMPIRICAL（真实轨迹证据 + 零模型状态重放）。
- **复现命令**：`python scripts/online_adaptation_demo.py`
- **动机**：用户希望项目带一个"自进化"这类新潮关键词。**直接写"自进化"是本仓库最危险的一处**：
  `archive/reflexion.py` 是从未接线的提示词级 flywheel，其依赖的 `lessons` 模块已随控制器删除；
  日志中 E-017（把运行时自进化误实现为外部 Coding Agent 改源码）、E-018（运行时 lesson 仍是提示词提示，不等于自进化）、
  E-042（控制器净负）记录的都是**失败或不完整**。拿不出正面结果的词会被面试官拆掉。
- **因此改为演示一条证据成立的窄命题：记忆在会话内自适应。**
  演示把两类证据**显式分开**，不混：
  - **REAL**（来自 `data/simulations/smoke_proactive_M793481_sub1.json`）：真实运行里模型**确实问了**策略提议的那个问题，
    闭环计数 `{questions_committed: 1, answers_linked: 1, answers_resolved_to_a_value: 1}`，
    用户真实回答 `'随便，你看着办吧。'`。
  - **DERIVED**（零模型重放）：缺口声明 → 提议（不花预算）→ 记账 → 答复解析 → 缺口状态。
    其中"答复关闭缺口"这一步标注为 **COUNTERFACTUAL**，因为**真实那次用户是委托而非作答**，不能拿真实轨迹冒充。
- **演示揭示的状态机（零模型，可当场看）**：

  | 步骤 | facts | open gaps | 下一步 |
  | --- | --- | --- | --- |
  | 子任务开始 | `[]` | `['transport']` | 问 transport |
  | 提议并记账 | `[]` | `['transport']` | （`asked_this_subtask=1`） |
  | 真实答复＝委托 | `[]` | `['transport']` | 缺口仍未决，但**同一问题不重复** |
  | 反事实：`'坐高铁吧'` | `[('坐高铁吧','transport','positive')]` | `[]` | 无需再问 |

- **演示中途修掉的两处自己的表述错误**（避免演示误导）：
  1. `the user said` 取的是**子任务指令**（第一条用户消息）而非问题之后的答复——已改为取问题之后的用户轮次；
  2. 委托后显示"next action: ask about transport"会让人误以为同一句会重复——已改为
     "缺口仍未决，但同一问题不重复"（CLAUDE.md：同一问题只计一次）。
- **移除的断言**：*"运行时自适应可以被称为自进化"*——本仓库没有支持该措辞的证据；
  能支持的是"记忆在会话内自适应并改变后续行为"。
- **适用边界**：**不展示任何分数提升**。没有跑评测；真实答复是委托而非作答，所以闭合那一步在真实轨迹中是反事实。
- **能力抽象**：preference updating / long-horizon consistency（**在线自适应**，非自我改进）。

## E-083：本会话改动的影响面实测——平均只改 6.2 个字符

- **日期**：2026-09-13
- **状态**：`VERIFIED`（负面自评）。**结论对我不利，如实记录。**
- **通用性**：GENERAL-EMPIRICAL（770 个唯一子任务定义，零模型）。
- **复现命令**：`python scripts/change_impact_audit.py`
- **用户质疑**："关键是你的改动能影响效果。" 本条是对该质疑的直接回答。
- **先承认的问题**：本会话在**测量路径**（`--agent stock --memory-type adapt`）上积累了一批**会改变模型所见提示**的改动
  （E-059/060/061 改 `read()` 渲染；E-075/076 改进卡内容；E-079 往 `card.must` 加约束），
  而**没有测量过它们的合计效果**。其中有些方向是单边的（把"用户喜欢花生"改成"忌花生"不可能更差），
  有些是**双向的**（E-076 删掉的类别约束也可能正是模型在用的提示）。
- **实测（770 个唯一子任务定义）**：

  | 指标 | 值 |
  | --- | --- |
  | 提示被本会话改动过的定义 | **164（21.3%）** |
  | ├ 渲染不再截断（E-060） | 20 |
  | ├ 丢失一个伪类别约束（E-076） | **126** |
  | └ 新增一个带槽键的约束（E-079） | 33 |
  | **每个被改动定义的平均提示差异** | **6.2 字符** |
  | 其中：新可见的条件字符 | 404 |
  | 其中：移除的类别约束字符 | 619 |

- **结论（诚实）**：
  1. 这些改动**太小**。平均 6.2 个字符、覆盖 21.3% 的定义，**不足以让人期待在 8 用户队列上产生可检测的分数变化**。
     主要效果（126 个定义）是删掉 `饭`/`外卖`/`酒店`/`汤` 这几个 1–2 字的类别词。
  2. **字符数不是全部**：E-059（极性）与 E-061（安全约束从"完全不存在"变为存在）是**语义性**的，
     大小度量不到它们。但它们是否真被模型误用，**没有测量**，所以不得声称有效。
  3. 因此正确的表述是：**这些改动修的是确定的错误，不是一个可期待提分的干预。**
     "修对了"与"能提分"是两件事，本条把这个区分量化了。
- **真正的杠杆在哪里**：本会话量出的最大可干预人群是 E-073 的 **60 次运行（占 400 的 15%）**——
  指令要求交易、目标已打印、agent 从未尝试写入、最后一轮在问用户然后被放弃。
  这才是"改动能影响效果"应该打的地方。而它对应的干预（确认信号）已在 E-074 被实测为
  **在冻结配置下不可行**（可达 18/400 = 4.5%，且其中含伪约束）。
- **移除的断言**：*"修好这些缺陷就构成一个提分干预"*——实测影响面平均 6.2 字符，该断言不成立；
  以及 *"改动越多越接近目标"*——改动数量与效果无关，本条给出的是**影响面**而非**效果**。
- **适用边界**：本条度量的是**提示层面**的影响面（reach），不是效果（effect）。
  它说明"哪里有变化"，不说明"变化有没有用"。零模型，不含分数结论。
- **能力抽象**：不适用（工程自评 / 效果诚实性）。

## E-084：按"频率 × 影响 × 可优化性"归因——大类别都难改，好改的都很小

- **日期**：2026-09-13
- **状态**：PARTIAL。频率与影响是**实测**；可优化性是**判断**，逐条附证据出处。
- **通用性**：GENERAL-EMPIRICAL（`stock_avg4_8u.json`，400 次子任务-trial，283 次失败，通过率 29.2%）。
- **复现命令**：`python scripts/failure_priority.py data/simulations/stock_avg4_8u.json`
- **口径**：影响 = 该类**全部**被救回时对官方 Avg@4 的上限。一次子任务-trial 救回 = 0.0025
  （该 unit 的 trial 均值动 1/4，该 unit 权重 1/100）。这是**上限**，假设 100% 有效。
  标志**不互斥**，一次运行可同时命中多个，因此占比之和超过 100%。
- **排名（实测频率/影响 × 判断的可优化性）**：

  | 失败模式 | 频次 | 占比 | 影响上限 | 可优化性 | **优先级** |
  | --- | --- | --- | --- | --- | --- |
  | `target_never_printed`（答案从未出现） | 144 | 36.0% | 0.3600 | 0.15 | **7.78** |
  | `ended_asking_the_user`（未写，最后在问） | 116 | 29.0% | 0.2900 | 0.10 | **3.36** |
  | `order_created_for_another_candidate` | 83 | 20.8% | 0.2075 | 0.15 | **2.58** |
  | `wrote_but_not_the_target` | 75 | 18.8% | 0.1875 | 0.15 | **2.11** |
  | `thrash_frozen_repeat` | 13 | 3.2% | 0.0325 | 0.20 | 0.08 |
  | `order_created_for_the_target_but_unpaid` | 8 | 2.0% | 0.0200 | 0.50 | 0.08 |
  | `hit_max_steps` | 17 | 4.2% | 0.0425 | 0.10 | 0.07 |
  | `write_argument_rejected` | 5 | 1.2% | 0.0125 | 0.60 | 0.04 |
- **结论：** **前四名全部是候选检索/选择类失败**（答案没出现 / 没写就去问 / 写错候选 ×2）。
  而**可优化性最高的两类都极小**（`write_argument_rejected` 5 次、支付未完成 8 次）。
  即：**大类别都难改，好改的都很小**——这正是该公式的价值，也是本项目一直提不上分的结构原因。
- **我自己的一个误判，以及它是怎么被拆掉的（重要，属 E-053 的同一课）**：
  第一版我把"订单已创建但未支付"当成**支付类**失败，它凭"机械状态迁移"拿到 0.50 的可优化性，
  **排名第一（10.35，远超第二名）**。去翻这 91 次后发现：**83 次创建的订单用的是非目标 id**，
  只有 8 次是"选对了商品但没支付"。
  也就是说它其实是**候选选择失败**的另一个观测角度，可优化性与选择类相同（0.15），排名掉到第三。
  **教训**：一个由我设计的标志排到第一，不等于它是瓶颈；必须下钻验证机制才能定可优化性。
- **顺带修掉一个会污染数字的真 bug**：拆分"用了目标 id"时，`products` 在赋值前被使用，
  取到了**上一轮迭代**的值，使拆分为 88/3 而非正确的 83/8。`ruff --select F821` 抓到了它；
  修好后与独立探测完全一致。**这是"逐项复核"而非"看总量"抓出来的。**
- **移除的断言**：*"订单没支付＝支付环节有问题"*（83/91 其实是选错商品）；
  *"一个失败类别只要有明确机制就容易被修"*（前四名机制都很明确，可优化性都很低）。
- **适用边界**：可优化性不是测量值，是我依据 E-020/042/048/049/050/051/053/056/062/066/067/072/074 的判断。
  换一个人可以合理地给不同分数，排名会变——但"大类别难改、好改的很小"这一结构不会变，因为频率与影响是实测的。
- **能力抽象**：utilization / execution（**归因表**，用于排优先级，不是干预结果）。

## E-085：前两类失败到底是不是 agent 的问题——按成因再拆一层

- **日期**：2026-09-13
- **状态**：PARTIAL。成因已拆分（零模型）；**两处限定使结论弱于字面**（见下）。
- **通用性**：GENERAL-EMPIRICAL（`stock_avg4_8u.json`，400 次运行；只读工具调用与消息文本）。
- **复现命令**：`python scripts/failure_cause_split.py data/simulations/stock_avg4_8u.json`
- **动机**：E-084 排了优先级，但排名不回答"这是 agent 的错、检索的错，还是任务本身的错"。本条把前两类各自再拆一层。
- **类别 1：`target_never_printed`（144 次）**

  | 拆分 | 次数 | 占比 |
  | --- | --- | --- |
  | **目标容器已打印、但没被打开** | **116** | **81%** |
  | 搜了，但目标容器从未出现 | 18 | 12.5% |
  | 完全没搜 | 10 | 7% |

  容器排名中位数 **12**，其中 **57 次在 rank ≤10**。所以多数是 agent 侧——正确容器就在它收到的列表里。
- **类别 2：`ended_asking_the_user`（116 次）**

  | 拆分 | 次数 | 占比 |
  | --- | --- | --- |
  | 指令要求 **commit**（交易） | **84** | **72%** |
  | 指令只是 recommend | 32 | 28% |

  域分布：instore 44 · delivery 40 · ota 32。样例形态高度一致：**agent 已经做好决定、宣布了，然后停在"确认"上而不执行**（"那我帮你选…的…奶茶——冰的，大杯，"）。
- **结论**：这两类里约 **200 次（116 + 84）确实是 agent 自身行为**，不是评测或环境的问题。
- **三条必须一起引用的限定（否则结论会被误用）**：
  1. **"是 agent 的问题" ≠ "是我们代码能修的"。** 仓库恰好在这两个行为上做过受控干预且全部净负（E-042 `LOST 7 : GAINED 1`、E-048、E-049、E-050、E-053）。
  2. **类别 2 分不清"问得好"与"不该问"。** 多数是用户模拟器在提问后结束会话，而主动型子任务**要求**提问才能披露隐藏意图。区分它需要 rubric，而 rubric 不是运行时可见的。这正是"不能为假的对照不算对照"那条。
  3. **类别 1 的 81% 含注意力成分。** 中位 rank 12 说明有一半情况正确容器在长列表深处；说"没点进去"是事实，归为"疏忽"则过度。
- **移除的断言**：*"最大失败类一定是 agent 的错"*——需要拆到成因才能说，且拆完仍有两条无法用现有证据排除的解释。
- **适用边界**：只覆盖两类；"目标未打印"里容器也未出现的 18 次、完全没搜的 10 次未再细分。纯描述，不含分数。
- **能力抽象**：execution / long-horizon consistency（**归因**，非干预）。

## E-086：agent 侧收敛为唯一一个纯观察 agent——目的是让"改一个 agent 再测量"成立

- **日期**：2026-09-13
- **状态**：`VERIFIED`（结构与可测量性的收敛；**本条不声称任何分数变化**）。
- **通用性**：GENERAL-STRUCTURAL（agent 类与 runner 的 `--agent` 取值；零模型，不含 user/task/candidate 特例）。
- **复现命令与实测结果**：
  - `python -m pytest agent/tests -q` → **397 passed**（收敛前 414；删掉 `test_evidence_agent.py` 与
    `test_thrash_guard.py` 后自然下降。没有跳过或改写任何测试来凑绿）
  - `python -m pytest archive -q` → **35 passed**
  - `python -m compileall -q agent archive scripts` → 退出 0
  - `ruff check agent scripts --select F401,F811,F821` → `All checks passed!`，退出 0
  - `git -C evaluation/vitabench diff --exit-code HEAD -- src/vita` → 退出 0（vendored 未改）
  - `python -m agent.vitabench_runner --help` → `--agent {stock,adapt}`（只剩这两个取值）
- **收敛前的四条 agent 路径及其判定**：

  | 类 | 判定 | 依据 |
  | --- | --- | --- |
  | `EvidenceAgent` | **无评分产物，无法判定** | E-072 核实仓库里没有任何它的评分 checkpoint（UNRESOLVED） |
  | `CandidateMarkingAgent` | **实测无增益** | E-058：词面标注不能代表条件满足，随后冻结，未进入任何测量配置 |
  | `ThrashGuardAgent` | **自身 smoke 零触发** | E-072：`states.thrash_guard.events == []`；人群 13/400，影响上限 **+0.0325**，低于 +0.0575 的目标缺口 |
  | `ProactiveLoopAgent` | **已验证的纯观察者** | E-069：真实轨迹上闭环确认；`test_proactive_loop.py` 的零模型门禁 |

- **变更**：
  - 删除 `agent/evidence_agent.py`、`agent/marking_agent.py`、`agent/candidate_marking.py`、
    `agent/thrash_guard.py`、`agent/proactive_agent.py`，以及 `agent/tests/test_evidence_agent.py`、
    `agent/tests/test_thrash_guard.py`。
  - 唯一保留的自研 agent 是 `agent/adapt_agent.py::AdaptAgent`——`ProactiveLoopAgent` 的内容原样改名，
    **继承**（而不是复制）vendored `vita.agent.personalization_agent.PersonalizationAgent`。
  - runner：`--agent` 收敛为 `{stock, adapt}`；删除 `--thrash-guard`、`--thrash-repeat-threshold`、
    `--candidate-marking`、`--marking-limit` 及其在函数签名和 `info` 里的全部穿线；
    状态落盘改为 `simulation.states["adapt_agent"] = agent.loop_events`。
  - `agent/tool_signature.py` 保留（`scripts/runaway_autopsy.py` 仍导入它），只更新了提到已删文件的 docstring。
- **不变量**：`AdaptAgent` 只覆写 `generate_next_message`，在 `super()` 前后各做一次**记账观察**
  （入站答复链接、出站问题提交）；不修改、不替换、不重排、不抢占模型消息，不阻断工具调用或写入。
  由 `agent/tests/test_proactive_loop.py::test_observer_never_modifies_the_model_message` 守护。
- **本条结论**：**这次收敛本身不改变任何分数。** 它没有新增能力，也没有删掉任何已证实有效的能力
  （四条路径分别是没有产物、无增益、零触发，以及被改名保留的那一条）。
- **它买到的是什么**：此前 `--agent adapt` 指向 EvidenceAgent，另两个实验藏在 flag 后面，
  "agent 改动"与"flag 组合"混在同一个 CLI 里，任何对比都说不清被测的是哪一个 agent。
  收敛后只剩**一个可改的 agent 类**，"改一个 agent，再测量"才第一次成为一个定义清楚的实验。
- **移除的断言**：*"多一条实验路径等于多一种能力"*——四条路径没有一条有可引用的分数产物；
  *"--agent adapt 正在被测量"*——它指向的类连一个评分 checkpoint 都不存在。
- **适用边界**：这是结构收敛，不是干预效果。`AdaptAgent` 的对照仍是 stock `PersonalizationAgent`；
  8 用户队列上 ±0.0582 的噪声下限不变，任何提分主张仍须走 E-057 之后的非配对对比与官方单元门禁。
- **后续风险/下一步**：`AdaptAgent` 尚未在 8 用户 × 4 trial 上测量。**"可测量"不等于"有增益"。**
- **能力抽象**：proactiveness（主动提问闭环的观察侧）/ updating（答复进事实库的状态转移）。

## E-087：给唯一那个 agent 加第二个机制——候选/约束三值观察，独立开关、默认关闭、未测量

- **日期**：2026-09-13
- **状态**：OPEN。机制已实现并通过零模型门禁；**未测量**，开关默认关闭；**本条不声称任何分数变化**。
- **通用性**：GENERAL-STRUCTURAL（机制、开关与落盘字段；零模型测试，不含 user/task/candidate 特例）。
- **动机（证据来自 E-084 / E-085 的实测）**：最重的失败类别都是**候选检索/选择**类——
  `target_never_printed` 144 次（36.0%），其中 **116 次是目标容器已经打印在工具结果里、却没有被打开**；
  `order_created_for_another_candidate` 83 次（20.8%）与 `wrote_but_not_the_target` 75 次（18.8%），
  合计 **158 次是绑定或排序到了错误的候选**。这些运行里，判定所需的属性**已经在环境打印的候选记录中**，
  缺的只是在当轮按"满足 / 冲突 / 未知"把它摆出来。
- **变更**：
  - `agent/adapt_agent.py`：新增第二个开关 `enable_candidate_evidence`（默认 **False**），与
    `enable_proactive_loop` 相互独立；`loop_events` 增加 `candidate_evidence_enabled`、
    `tool_results_annotated`、`candidate_constraint_pairs_resolved` 三个键，供逐单元归因。
  - 工具结果到达时，用 `agent/candidate_ledger.py` 解析候选，用当前指令经**真实编译器**得到的约束
    （`constraints_from_card`）算**三值**关系（`correspondence_for_candidate`，极性由约束本身携带，
    因此 `forbid` 永远不会被读成正向偏好），再把有界区块（`render_correspondence_block`）**追加**到消息上。
  - 追加只发生在 `deepcopy` 出来的副本上：环境自己的消息对象——也就是评测与离线审计读取的那条 trajectory
    ——保持**逐字节不变**。计数只在开关打开时更新。
  - `agent/vitabench_runner.py`：新增 `--proactive-loop` 与 `--candidate-evidence` 两个 CLI flag，穿到
    `AdaptAgent` 构造，并写入 checkpoint 的 `info["adapt_agent"] = {"proactive_loop": bool, "candidate_evidence": bool}`；
    参数在同一条链上的**每一个**函数签名（`run_stock_personalization_task` / `_run_one_simulation` /
    `run_selected`）与每一处调用点都已同步。
- **能且只能追加**：机制**只能追加**，绝不修改、重排、删除或重绘环境产出的任何候选记录；追加文本由命名常量
  `_MAX_EVIDENCE_CHARS = 600` 封顶；任何异常都被静默吞掉，记账永不打断一次运行。
- **开关关闭时**：两个开关都关时 `generate_next_message` 仍是 stock 的**逐字直通**（不读记忆、不写记忆、
  不动消息），继续由未改动的 `test_adapt_agent_defaults_to_a_pass_through` 守护。
- **复现命令与实测结果**（全部零模型）：
  - `python -m pytest agent/tests -q` → **420 passed**（新增 21 条；无跳过、无弱化断言）
  - `python -m pytest archive -q` → **35 passed**
  - `python -m compileall -q agent archive scripts` → 退出 0
  - `ruff check agent scripts --select F401,F811,F821` → `All checks passed!`，退出 0
  - `git -C evaluation/vitabench diff --exit-code HEAD -- src/vita` → 退出 0（vendored 未改）
  - `python -c "import sys, agent.memory.adapt_memory; print([n for n in sys.modules if n.startswith(('agent.candidate_ledger','agent.runtime.ranking','agent.runtime.location'))])"`
    → `[]`（数据层导入闭包仍不含 ledger 与 runtime ranker）
- **不变量**：观察只发生在 `super()` **之前**，且只作用于副本；不替模型或用户决定，不阻断工具调用或写入，
  也不修改模型生成的助手消息。两条投递形状（裸 `ToolMessage` 与 `MultiToolMessage`）都有测试覆盖——
  E-053 的教训是只处理包装形状会让机制在真实路径上完全空转。
- **移除的断言**：*"审计里频率最高的失败类别就等于该修的瓶颈"*——E-053 已经用一次预注册干预否证过这条推理。
  本次只是把"判定所需信息已经打印出来"这一观察做成**可开关、可单独测量**的机制，**不声称它会提分**。
- **适用边界**：8 用户队列上 ±0.0582 的噪声下限不变；机制默认关闭，尚未进入任何测量配置。
  与 E-084/E-085 同一口径：那些次数是**归因**，不是干预结果。
- **后续风险/下一步**：先做一次真实投递形状的冒烟（裸 `ToolMessage` 与 `MultiToolMessage` 两条路径），
  再按 E-057 之后的非配对对比与官方单元门禁测量。**"已实现且测试通过"不等于"有效"。**
- **能力抽象**：utilization（把已打印的证据摆到模型面前）/ long-horizon consistency。

## E-088：记忆的"演变"机制从未触发——取代 0 次，遗忘 2968 次

- **日期**：2026-09-13
- **状态**：PARTIAL。机制触发情况**已实测**；根因当时是假设，现已由 E-090 实测证伪并替换。
- **通用性**：GENERAL-EMPIRICAL（8 个用户的完整历史，零模型重放）。
- **复现命令**：`python scripts/memory_evolution_audit.py`
- **动机**：benchmark 的能力项包含"长期记忆与关系管理"和"真实场景动态适应"——即偏好随数月/数年演变。
  仓库**已有**这套机制（作用域漂移检测、事实取代、生命周期遗忘），但**从没量过它是否触发**。
  E-072 的教训是：机制存在不等于会开火（ThrashGuard 在自己的 smoke 里触发 0 次）。
- **实测（8 用户，整段历史，启发式路径 `llm=None`）**：

  | 指标 | 合计 |
  | --- | --- |
  | 信号 | 10,594 |
  | 事实 | 4,933 |
  | **status = superseded** | **0** |
  | drift 触发 | **15** |
  | 遗忘淘汰 | **2,968** |
  | 单值维度同时持有多个 active 值 | **18** |

  8 个用户**每一个**的 superseded 都是 0。
- **结论**：
  1. **取代一次都没发生。** `FactStore.ingest` 的取代路径从未走通。
  2. **这个记忆的"演变"实际上是衰减淘汰**（遗忘 2968 次），不是适应。旧偏好不是被新偏好取代，而是被容量上限扔掉。
  3. **18 个单值维度矛盾并存**——例如
     `('local_commerce','beverage','temperature','奶茶') -> ['冰饮','热饮']`、
     `('local_commerce','beverage','temperature','咖啡') -> ['冰饮','热饮']`。
     **一杯奶茶不可能既是冰饮又是热饮**；这是偏好随时间变化，而记忆两个都留着、**两个都渲染给模型**。
- **根因（假设，未验证）**：取代的前置条件是 `confirmed_drift`，而 drift 只触发 15 次，实际偏好变化远多于此。
  即**漂移检测过保守 → 取代被它卡住 → 矛盾值永久并存**。具体阈值行为需要读 `DriftDetector` 才能确认。
  **（2026-09-13 更新）** 该假设的**具体内容已被证伪**：不是阈值保守，而是漂移槽键与取代槽键定义不一致，
  取代在等一个检测器发不出的信号。实测根因、修复与前后对照见 **E-090**（取代 0 → 27，单值冲突 18 → 0）。
- **为什么这正对 benchmark 的能力项**：长期记忆（跨会话保留了矛盾状态）、动态适应（偏好变了没被适应）、
  个性化（模型拿到矛盾偏好）三项同时失败，且**不是词表问题而是状态迁移机制问题**。
- **移除的断言**：*"仓库里有漂移/取代/遗忘 = 具备长期记忆与动态适应能力"*——实测取代 0 次，该断言不成立。
- **适用边界**：`llm=None` 只测启发式路径；真实运行还跑 LLM 抽取与摘要，可能引入额外的取代路径，本条**未覆盖**。
- **能力抽象**：preference updating / long-horizon consistency。

## E-089：机制级归因——六类示例全部命中，并补出一类"事务未收尾"

- **日期**：2026-09-13
- **状态**：PARTIAL。归因已机械实现；**8 条（2.8%）不可归因**，需 rubric 才能分类。
- **通用性**：GENERAL-EMPIRICAL（`stock_avg4_8u.json`，400 次运行、283 次失败，互斥主标签）。
- **复现命令**：
  - `python scripts/mechanism_attribution.py data/simulations/stock_avg4_8u.json`
  - 未归因者细读：`python scripts/_unattributed_probe.py data/simulations/stock_avg4_8u.json`
- **归因口径（用户的硬性要求）**：**禁止**归因为"这道题的关键词没匹配上"；
  必须归因为"Agent 缺乏处理此类模糊指代的通用逻辑"。分类器因此**只看 agent 拿着它已经拥有的信息做了什么**，
  不看它包含什么词。用户给的六类是**示例而非封闭清单**。
- **最终分布（合计 283）**：

  | 错误类别 | 对应能力 | 失败数 | 占比 |
  | --- | --- | --- | --- |
  | 状态丢失 `state_loss` | 个性化 / 长期记忆 | **127** | 44.9% |
  | 规划缺陷 `planning_defect` | 规划 / 任务拆解 | **66** | 23.3% |
  | 不该问乱问 `over_asking` | 主动性（过度提问） | 42 | 14.8% |
  | 工具幻觉 `tool_hallucination` | 工具调用 / 接地 | 14 | 5.0% |
  | 死循环/未恢复 `dead_loop_no_recovery` | 长序列控制 | 13 | 4.6% |
  | **事务未收尾 `transaction_left_unfinished`（本轮新增）** | 执行 / 承诺 | 11 | 3.9% |
  | 不可归因 | — | 8 | 2.8% |
  | 该问没问 `missed_question` | 主动性（漏问） | 2 | 0.7% |
- **新机制的来源**：初版有 15 条落进"不可归因"。细读后发现其中 11 条形态完全一致：
  **正确观察到目标 → 用目标商品成功写入订单 → 把最后一步交还用户 → 用户离开**
  （"要现在支付吗？💳" / "那你自己支付就行～订单号是…记得在订单列表里找到它完成支付哦"）。
  指令是 `commit`（已授权交易），因此这是**通用的"收尾"逻辑缺失**，不是词汇问题。已并入分类器，不可归因降到 8 条。
- **剩余 8 条为何仍不可归因**：它们机械上无可指摘（观察到目标、绑对候选、写入成功、未死循环、未幻觉），
  却仍然失败——例如 `P722245 sub1` 最后一句是"**支付成功！✅**"。要分类必须看到 rubric 条件，
  而**运行时不可见**。如实标为不可归因，不硬塞。
- **两个方向性发现**：
  1. **主动性是"问太多"不是"问太少"**：过度提问 42 vs 漏问 2，差 21 倍。
  2. **前四类里三类是"信息在手却没用对"**（状态丢失、工具幻觉、事务未收尾），合计 152 次 = 53.7%。
     这与 E-084/E-085 一致：主要瓶颈不在获取信息，在**利用**信息。
- **移除的断言**：*"最大失败类一定是检索/词汇问题"*——机制级归因显示"信息在手没用对"占一半以上。
- **适用边界**：互斥主标签（固定优先级），**数字不能与 E-084 的重叠标志直接比较**——
  E-084 里 `target_never_printed` 是 144，这里因"目标已打印"先被 `state_loss` 收走，同类只剩小量。
- **能力抽象**：utilization / planning / proactiveness / execution（**归因**，非干预）。

## E-090：取代从未触发——两个机制对"同一个偏好槽"的定义不一致（取代 0 → 27，单值冲突 18 → 0）

- **日期**：2026-09-13
- **状态**：PARTIAL。机制触发情况与三个根因**已实测**；取代与单值冲突两项已有前后对照；
  **但这不是分数结论**（见"不做的声明"）。
- **通用性**：GENERAL-EMPIRICAL（8 个用户的完整历史，零模型重放，启发式路径 `llm=None`）。
- **复现命令**：
  - 前/后对照：`python scripts/memory_evolution_audit.py`
  - 反例门禁：`python -m pytest agent/tests/test_preference_evolution.py -v`
- **动机**：E-088 实测"取代 0 次、遗忘 2968 次"，当时把根因标为**假设**（"漂移检测过保守 → 取代被它卡住"）。
  本条把假设读成代码并**证伪了它的具体内容**：不是阈值太保守，而是**两个机制对"同一个偏好槽"的定义不同**，
  取代分支等在等一个检测器**永远不可能发出**的信号。
- **实测根因**（三条，全部定位到具体代码）：
  1. `agent/memory/drift.py` 的 `DIMENSION_PREDICATES = {"taste_preference"}`：
     `DriftDetector._slot_key` 对任何其它 predicate 直接返回 `None`。由 `explicit_preference`
     得到的 `time`/`room_type`/`transport`/`budget` 等单值维度**从不参与漂移**。
  2. 漂移槽键构成 `(scope, facet, dimension, "default")` **丢掉 `category`**，
     而 `FactStore.ingest` 的取代分支要求 `existing.category == fact.category`。
     两边对"同一个槽"的定义不同 → 取代**不可能**被确认。
  3. 取代被 `confirmed_drift` 门控，而它需要 `drift_threshold`(2) 次冲突，即**三次观测**；
     叠加 (1)(2) 后基本不会发生。
  4. **（第一轮修复后新暴露的一条）** 重复观测一个此前被取代的值会把它**重新激活**
     （去重分支 `existing.status = "active"`），却没有让槽内**当前活跃的竞争值**退休。
     这是第一轮修复后残留 8 个冲突的**全部**成因，实测时间线：
     `酸辣/酸汤(2026-04-17) 到达时 → {酸辣/酸汤 active, 重口味/麻辣 active}`，
     它们此前的到达顺序是 `重口味/麻辣(2024-12-18)` → `酸辣/酸汤(2026-04-17)`。
     即：**"改回去"被记录成了"两个值并存"，而不是一次改变**。
- **改动**（通用状态迁移机制，无 user/task/candidate 特例、无按 benchmark 输入调过的正则、无新词表条目）：
  1. **统一槽身份**：单值边界只声明一次（`agent/memory/facts.py` 的 `SCALAR_DIMENSIONS`），
     `fact_store`、`drift`、两个零模型审计全部引用它（此前是**四份手工维护的副本**）。
     `DriftDetector._slot_key` 改为从**事实维度**判定，直接返回 `PreferenceFact.slot_key`
     ——与 `FactStore` 取代用的键**逐字节同一个**。
  2. **按维度放开漂移参与**：由"predicate 白名单"改为"结果维度 ∈ 单值集合"，
     `explicit_preference` 等 predicate 只要落在 `time`/`room_type`/`transport`/`budget`
     就参与；`avoid`/`safety`/`brand`/`product`/`like`/`searches` 由构造被排除。
     **负向事实显式排除**（`polarity == "negative"` → 不参与漂移）：厌恶是累加的，
     新增一条"不吃 X"不是对"不吃 Y"的改主意。
  3. **取代与 `confirmed_drift` 解耦**：新值落在**已被占用的同 (scope, facet, dimension, category)、
     同极性单值槽**时，旧的 active 事实置为 `superseded`。漂移检测器**保留**（它仍做自己的
     置信衰减与检索抑制），两条路径同时命中是幂等的。
  4. **重新激活也解析槽**：去重分支激活旧值时同步退休槽内竞争值（根因 4）。
- **前/后对照**（8 用户整段历史，启发式路径；`facts` 4,933 与 `forgotten` 2,968 **未变**，
  即取代没有以增加遗忘为代价）：

  | 指标 | 前 | 后 |
  | --- | --- | --- |
  | status = superseded | **0** | **27** |
  | drift 触发 | 15 | 14 |
  | 单值槽同时持有多个 active 值 | **18** | **0** |
  | active 事实 | 4,933 | 4,906 |

  逐用户 superseded（前 → 后）：E057330 0→3、E941775 0→1、J365414 0→2、M793481 0→1、
  O309411 0→3、P722245 0→4、Q089190 0→7、U000828 0→6；**8 个用户的单值冲突前为 2/1/2/1/2/2/5/3，后全部为 0**。
  E-088 点名的两个矛盾 `('local_commerce','beverage','temperature','奶茶')` 与 `(…,'咖啡')`
  不再出现在冲突列表里——同一槽现在只留一个 active 值。
  `drift_events` 15→14 属预期：漂移槽键按 `category` 拆分后，原先被并到一个"default"槽的冲突
  不再互相计数。取代不再依赖漂移计数，所以该下降**不是**取代减少。
- **多值集合的反例门禁**（本次最重要的不回归项）：新增 `agent/tests/test_preference_evolution.py`
  （22 条，零模型），直接断言而非间接统计：
  - 新增厌恶**不驱逐**旧厌恶（`avoid`/`safety` 同槽），品牌、商品同理累加；
  - `SCALAR_DIMENSIONS` 不含 `avoid`/`safety`/`brand`/`product`/`like`/`searches`；
  - 跨 scope、跨 category、跨 facet 的事实**不**互相取代；
  - 同值重复观测只增强（不新增事实、不改 `observed_at` 倒退）；
  - 负向事实**没有漂移槽**，连续负向信号不产生 drift；
  - 主动问答的答案仍落成事实（`commit_question`/`record_user_answer`），
    并且"不是，这次少糖"式的更正会让旧值退休。
  这些断言是**方向性**的：总量可以变好而单条保证退化，所以逐条钉住迁移方向。
- **一处旧测试的更新（如实记录，非弱化）**：
  `test_agent_architecture.py::test_weak_or_conflicting_room_history_does_not_suppress_question`
  原先断言"同一 `room_type` 槽的两个 active 值使槽保持未解决、于是继续提问"——
  **这正是 E-090 要修掉的行为**（单值槽永久持有新旧两值）。改为断言该不变量真正的内容：
  弱的 search 轨迹不钉死槽，后到的值被采纳为 `大床房`，且后续问题不再问房型。
  它比原断言**更强**（钉住具体解析值＋问题内容），不是放宽。
- **不做的声明（no score claim）**：本条**不声称任何分数变化**。`memory_evolution_audit.py`
  是**零模型机制检查**（重放可观测历史，只跑启发式路径），不是评测：
  它不调用 agent/user/evaluator 任何模型，不读 rubric、reward 或答案。E-088 的推断
  "漂移过保守 → 取代被卡住"至此有了实测支撑（取代 0→27、单值冲突 18→0），
  但**"记忆机制开火"仍不等于"任务得分提高"**——这是 E-072 的同一纪律。
- **移除的断言**：*"取代的前置条件是 `confirmed_drift`，漂移触发次数就是取代次数的上界"*——
  实测取代 27 次而漂移 14 次，两者已解耦；也移除
  *"单值槽出现多个 active 值是因为漂移阈值太保守"*——真正的原因是槽键定义不一致。
- **适用边界**：`llm=None` 只覆盖启发式路径；真实运行还跑 LLM 抽取与摘要，
  其引入的额外取代路径本条**未覆盖**。`SCALAR_DIMENSIONS` 仍是人工声明的边界集合，
  本条只保证它**被所有机制一致引用**，不声称它已经完备（例如 `topping` 的单值性仍可再议）。
- **能力抽象**：preference updating / long-horizon consistency（状态迁移机制）。

## E-091：给 agent 加第三个机制——本任务状态观察（非指令、默认关闭），并先量它的 reach：44/283

- **日期**：2026-09-13
- **状态**：OPEN。机制已实现并通过零模型门禁；**reach 已实测**；开关默认 **False**；**本条不声称任何分数变化**。
- **通用性**：GENERAL-STRUCTURAL（机制、开关、落盘字段、零模型测试）+ GENERAL-EMPIRICAL（`stock_avg4_8u.json` 400 次运行重放）。
- **动机（两个机制、一个根因）**：E-089 的互斥主标签里有两类相邻失败：
  **规划缺陷 `planning_defect` 66 条（23.3%）**——搜索了、给了选项、然后**一次写入都没尝试**就停了；
  **不该问乱问 `over_asking` 42 条（14.8%）**——任务需要的槽**全部已定**却仍然提问。合计 **108 条（38.2%）**。
  共同根因：agent 没有"**本子任务还缺什么、已经有什么**"的表示，因此分不清"我做完了吗"和"我漏了什么"。
- **变更**：
  - `agent/adapt_agent.py`：新增第三个开关 `enable_task_state`（默认 **False**），与另外两个开关相互独立。
    工具结果到达时，把一行**有界的、只陈述状态**的观察**追加**到 `deepcopy` 出来的副本上。
  - 内容全部来自仓库**已经算出来的东西**：必需槽与已定/待定来自 `TaskSpec.compile`（`required_slots` /
    `unknown_slots` / `resolved_slots`）加上记忆后端自己的 `resolve_task_slots`；候选计数来自
    `CandidateLedger` 对工具结果**实际打印出来的 id** 的解析；两个标志是对本子任务**已经发生过的事**的观察
    （是否已发起 commit 工具调用、是否已发出过问句）。**没有新词表，没有 benchmark 专用规则。**
  - 形如：`【本任务状态】必需字段：product=已定 | address=待定　已观察候选：3 商家 / 12 商品　已发起写入：否　已提问：否`。
  - 封顶常量为 `_MAX_TASK_STATE_CHARS = 600`；渲染按**整字段**取舍，绝不把一个字段切一半。
    追加只发生在副本上，环境自己的消息对象——评测与离线审计读取的那条 trajectory——**逐字节不变**；
    任何异常都被静默吞掉，记账永不打断一次运行。
  - `loop_events` 增加 `task_state_enabled`、`task_states_annotated`；`agent/vitabench_runner.py` 增加
    `--task-state`，穿到 `AdaptAgent`，并写入 `info["adapt_agent"]["task_state"]`。参数在**每一个**函数签名
    （`run_stock_personalization_task` / `_run_one_simulation` / `run_selected`）与每一处调用点都已同步。
- **非指令是构造性的，并且被测试钉住**：块里**没有祈使句、没有建议、没有下一步动作**。
  "所有必需槽已定"是关于本子任务的事实，**不等于**告诉模型去写入；块不阻断工具调用、不改写模型消息、
  不替模型或用户做决定（设计主线 (a)：只传递观测值）。`agent/tests/test_task_state.py` 的
  `test_the_block_contains_no_imperative_or_recommendation` 对 `请/建议/应该/必须/需要您/现在可以/should/must`
  **逐词**断言，并在四种状态下各渲染一次，未来的改动无法悄悄把它变成指令。
- **reach 实测（先量再决定是否花钱，E-074 的纪律）**：
  - 复现命令：`python scripts/task_state_reach.py data/simulations/stock_avg4_8u.json`
    （只用编译器与保存下来的 trajectory；无模型、无 rubric、不读 target/distraction 标注；
    reward 只用来把 400 条评分运行分成通过与失败。）
  - 400 条评分运行 / **283 条失败**；其中 **275 条会收到至少一个块**（共 2120 个块）。
  - **(a) "必需槽全部已定 + 尚未发起写入"：44 条**（44/283 = 15.6%；44/400 = 11.0%）；
    其中**从未发起过写入调用的只有 14 条**（14/66 = planning_defect 的 21.2%）。
  - **(b) "没有开着的槽 + 已经问过"：44 条**，与 (a) **是同一批 44 条**，且这 44 条最后一轮**全部**是问句
    ——规模与 E-089 的 42 条 over_asking 相当。两条判据在本 checkpoint 上不区分，已如实记录。
  - 上限：44 × 0.0025 = **+0.110 Avg@4**（假设 100% 挽救）；严格的 planning_defect 半边
    14 × 0.0025 = **+0.035**，**即使 100% 挽救也低于 ±0.0582 的噪声下限**。
  - 诊断（**不是块显示的内容**）：失败运行里开着的槽频次为 `product 128 / address 46 / room_type 21 /
    departure 15 / time 13 / quantity 12 / taste 8 / transport 8 / caffeine 4 / shop_or_service 3`。
    若改用问题策略**自己**的"可问缺口"定义（`USER_ONLY_SLOTS` 减去工具可查/上下文可解析），
    **214 条失败运行没有任何可问缺口**（其中 87 条从未写入）。即：reach 的瓶颈是**编译器槽定义**，
    不是机制本身——`product` 一处就占 128 条（`_PRODUCT_CATEGORIES` 覆盖不到的食物名，E-068 已记录过）。
- **判定（明确写出来）**：**reach 不足以仅凭本机制花一次 8 用户 Avg@4**。
  它高于 E-074 的杀线（21/400 = 5.3%，上限 +0.0525），但也只是**勉强**：可分辨余量要求挽救
  ≥53% 的 44 条可达运行，而严格的 planning_defect 半边（14 条）无论怎样都读不出来；
  同时这个块会落到 **275/283 条失败运行**（以及几乎所有通过运行）上，**坏处是全域的、好处只集中在 44 条里**。
  下一步应当先处理诊断指出的瓶颈（把"已定"对齐到仓库**已经存在**的可问性规则，不引入任何新词表），
  再重新量 reach；**在此之前不建议启动测量运行**。这是把问题交回用户（设计主线 (c)），不是替用户做决定。
- **测试门禁**（零模型，无 API 调用）：
  - 新增 `agent/tests/test_task_state.py`（28 条）：开关关闭时仍是直通且不追加；块与 `TaskSpec.compile` 一致；
    **无祈使/建议词**；原消息逐字节不变；有界；只有**观察到** commit 调用后才显示"已发起写入：是"；
    无可报告内容时不追加；runner 转发 flag 并写入 checkpoint。
  - 两处**如实更新**（不是弱化，断言仍是精确相等）：`test_proactive_loop.py::test_loop_events_are_exposed_for_attribution`
    与 `test_candidate_evidence.py::test_run_selected_records_both_switches_in_the_checkpoint`
    各自把新增的 `task_state` 键补进精确字典断言。**未改动的**
    `test_adapt_agent_defaults_to_a_pass_through` 继续守护三开关全关时的直通。
- **复现命令与实测结果**（全部零模型）：
  - `python -m pytest agent/tests -q` → **470 passed**（442 → 470，新增 28；无跳过、无弱化断言）
  - `python -m pytest archive -q` → **35 passed**
  - `python -m compileall -q agent archive scripts` → 退出 0
  - `ruff check agent scripts --select F401,F811,F821` → `All checks passed!`，退出 0
  - `git -C evaluation/vitabench diff --exit-code HEAD -- src/vita` → 退出 0（vendored 未改）
  - `python -c "import sys, agent.memory.adapt_memory; print([...])"` → `[]`（数据层导入闭包仍不含 ledger 与 runtime ranker）
  - `python -m agent.vitabench_runner --help` → 退出 0，`--task-state` 在列
- **移除的断言**：*"审计里频率最高的失败类别就等于该修的瓶颈"*——E-053 已用一次预注册干预否证；
  以及 *"把状态摆到模型面前就等于模型会行动"*——**reach 是必要条件，不是效果**。本条只给出可达规模，不给效果。
- **适用边界**：8 用户队列上 ±0.0582 的噪声下限不变；重放用的是 `--memory-type rewrite` 的 stock 基线，
  没有结构化记忆，因此"已定"只按编译器口径计算——真实 ADAPT 配置下记忆可能再多定几个槽，故实测是**下界**；
  "已问过"在重放里用现有的 `_looks_like_a_question` 读助手轮（stock 没有 proactive 引擎，没有已提交问句记录），
  这个代理偏松，已如实标注。**"已实现且测试通过"与"reach 够大"都不等于"有效"。**
- **能力抽象**：planning / proactiveness / utilization（**观测**，非干预）。

## E-092：`--cohort dev` 与缓存基线不是同一批 8 个用户（重叠 2/8）——一次会跑错 11.5 小时的运行被拦下

- **日期**：2026-09-13
- **状态**：OPEN。**不改动任何 agent 行为**；本条约束的是**测量设备**本身。
- **通用性**：GENERAL-STRUCTURAL（checkpoint 的 `tasks` 字段是队列身份，`info["cohort"]` 只是 CLI 标签）。
- **难点**：按用户批准的主线配置启动 8 用户 ADAPT 臂时用了 `--cohort dev`。**首个 `task_id` 是 `B865629`**，
  而 `stock_avg4_8u.json` 的 8 人是 `E057330 / E941775 / J365414 / M793481 / O309411 / P722245 / Q089190 / U000828`。
  `B865629` 不在其中。运行已进入第 1 个用户第 1/16 个子任务（会话完成、已评测），
  照此跑完约 **11.5 小时**（基线实测 **86.4 min/(用户·试次)**），产出的将是一份**与缓存基线不可比**的检查点。
- **证据**（零模型，无 API 调用）：
  - `python -c "from agent.vitabench_runner import get_tasks, stable_user_split; ..."` →
    56 个任务；`dev = ['U200109','W974351','U010122','U901652','B865629','Q089190','Y208341','E057330']`；
    `blind = ['U973458','U778202','E941775','X193757','Z544664','U778201','U820719','O309411']`；
    **dev 与基线重叠 2 人**（`E057330`、`Q089190`），且 `E941775`、`O309411` 现在被分到 blind。
  - `python -c "json.load(open('data/simulations/stock_avg4_8u.json'))['tasks']"` →
    `['E057330','E941775','J365414','M793481','O309411','P722245','Q089190','U000828']`；
    `simulations` 里的 `task_id` 集合与之一致（8 人、32 条 (user,trial)）。
- **根因**：`stable_user_split` 对**当前** `get_tasks(language)` 的**全集**按 `sha256(f"{SPLIT_SEED}:{user_id}")`
  排序取前 8（`vitabench_runner.py:226-234`，`SPLIT_SEED = "ADAPT-2026"`）。种子没变，
  **但被排序的全集变了**：基线是 2026-09-06 的快照，此后任务集合发生了变动，于是"dev"这个名字指向了另一批人。
  CLAUDE.md 已经把这个陷阱写成"两个都叫 8 dev users 的队列，只重叠 2 人"，但那条警告**在运行命令里看不出来**：
  `--cohort dev` 打出的标签是 `dev`，基线打出的标签也是 `dev`，两者长得一样。
- **险些造成的错误**：若按 `--cohort dev` 跑完再与 `stock_avg4_8u.json` 对照，
  **8 人里有 6 人不同**，这个差异会被读成"本次改动的效果"，而它其实只是换了队列。
  这是本轮最贵的一个坑，且**只有在看 `tasks` 字段时才会暴露**。
- **有效方案**：
  1. 一切与缓存基线的对照必须用**显式** `--task-ids`（`nargs="*"`，直接绕过 split，
     见 `vitabench_runner.py:429` 的 `set(task_ids or split[cohort])`）。
     本轮最终命令：`--task-ids E057330 E941775 J365414 M793481 O309411 P722245 Q089190 U000828`。
  2. **队列身份取 `tasks` 字段，不取 `info["cohort"]`**：checkpoint 写入的是
     `"tasks": sorted(selected_ids)`（`:485`），产物因此自我描述；`info["cohort"]` 只是 CLI 标签。
     本次显式指定 8 人时标签仍是 `dev`，已知并如实记录。
  3. 新增设备 `scripts/_trial_slice_metrics.py`：把官方单位 `(task_id, subtask_idx)` 按试次切片，
     以解决"1 试次臂 vs 缓存 4 试次基线不是同一个量"的问题。**校准**：在基线上复现出
     官方 `Avg@4 = 0.2925` 与用户等权 `0.2940`，与文档逐位一致；
     四个单试次切片为 `0.2900 / 0.3200 / 0.2900 / 0.2700`——即**单试次的公平对照就是 ≈0.29**，
     这同时给出单试次切片的抽样抖动（sd≈0.019，仍**远小于** 8 用户对照下限 ±0.0582）。
- **附带发现（对后续重跑有用）**：`run_selected` 支持**断点续跑**——若 `save_to` 已存在
  且 `info` 与 `tasks` 完全一致，则从已完成集合 `done` 继续（`:488-498`）；
  配置不一致会直接 `raise ValueError`。所以被拦下的这次运行没有污染任何产物（未写出检查点文件）。
- **验证**：`scripts/_trial_slice_metrics.py data/simulations/stock_avg4_8u.json` →
  `official units=100`、`pooled Avg@4=0.2925`、`equal-user-weight=0.2940`、
  `sim durations: n=32 mean=86.4 min total=46.1 h`。
- **适用边界**：本条不声称任何能力或分数变化。它只在**跨运行比较之前**生效：
  先比对两个 checkpoint 的 `tasks` 字段，再谈 delta。若将来任务全集再次变动，
  `stable_user_split` 的 `dev`/`blind` 会再次漂移，而旧的显式 ID 列表仍然稳定。
- **后续风险/下一步**：`--cohort dev` 这个名字在仓库里已经指过至少两批不同的人；
  建议在 `scripts/paired_arms.py` 里加一条硬门禁——两个检查点 `tasks` 不同则拒绝比较，
  而不是打印一个看似有效的数字。（本条已记录，尚未实现。）
- **能力抽象**：不在六类能力之内——属于 **measurement integrity（评测设备完整性）**。

## E-093：8 用户 ADAPT 主臂实测——**不可分辨**（+0.0375，低于 ±0.0582）；主动性问句 11 问 3 落值；主失败类 state_loss 未降

- **日期**：2026-09-14
- **状态**：**VERIFIED（否定性结果）**。这是本条最重要的性质：它**不是**"有效"，也**不是**"无效"，而是按预注册判据落进了**不可分辨**带。
- **通用性**：GENERAL-EMPIRICAL（8 用户 × 1 试次，官方单位 100 个，零模型复现命令见下）。
- **代码版本**：`7d1b610`（运行期间未改动 agent/memory 代码，这是归因成立的前提）。
- **臂配置**（逐字）：`--agent adapt --proactive-loop --memory-type adapt --profile-summary --num-trials 1`，
  队列用**显式** `--task-ids E057330 E941775 J365414 M793481 O309411 P722245 Q089190 U000828`
  （`--cohort dev` 已不等于基线队列，见 E-092）。checkpoint：`data/simulations/adapt8_1t.json`（15.5 MB，8/8 用户，
  `evaluation_status` 全 ok，终止原因全 `user_stop`）。耗时 **545 分钟**（约 68 min/用户）。
- **主结果（官方单位 100 个）**：

  | 量 | 值 |
  |---|---|
  | 本臂 pooled Avg@1 | **0.3300** |
  | 基线 pooled Avg@4（同 100 单位） | 0.2925 |
  | 基线 trial-0 切片（同粒度） | 0.2900 |
  | 差值 vs pooled / vs trial-0 | **+0.0375 / +0.0400** |
  | 8 用户噪声下限 | **±0.0582** |
  | 目标 | 0.35（**未达到**，差 0.02） |
  | 官方单位配对 | **fixes 14 / breaks 10**，net +4，z=+0.82，**p=0.4142**，门禁**未过** |
  | 门禁算术 | 24 个翻转对 → 需要 net ≥ 10（delta ≥ +0.10），实测 net 4 |
  | 逐用户 rollup | 8 用户，better 4 / worse 3 / 持平 1，平均 +0.0362 |

  **判定（预注册三档）**：`NOT RESOLVABLE`。落在 0.32–0.35 带内——**既不能声称有效，也不能声称无效**。
  按仓库纪律，**禁止**把它写成"机制无效"或"机制有效"。
- **逐用户明细**（臂为 1 试次；基线为该用户 4 试次均值与其**自身极差**）：

  | user | 臂 | 基线均值 | 差值 | 基线自身极差 |
  |---|---|---|---|---|
  | E057330 | 0.5385 (7/13) | 0.2885 | +0.2500 | 0.2308 |
  | E941775 | 0.2857 (4/14) | 0.2857 | +0.0000 | 0.0000 |
  | J365414 | 0.2727 (3/11) | 0.4091 | −0.1364 | 0.0909 |
  | M793481 | 0.3636 (4/11) | 0.2727 | +0.0909 | 0.0000 |
  | O309411 | **0.0000 (0/12)** | 0.1250 | −0.1250 | 0.2500 |
  | P722245 | 0.4545 (5/11) | 0.3636 | +0.0909 | 0.0000 |
  | Q089190 | 0.2857 (4/14) | 0.3036 | −0.0179 | 0.0714 |
  | U000828 | 0.4286 (6/14) | 0.3036 | +0.1250 | 0.0714 |

  **单用户自身的单试次抖动实测**（同一用户四个基线切片）：E057330 **0.2308**、O309411 **0.2500**、
  J365414 0.0909、Q089190/U000828 0.0714、E941775/M793481/P722245 **0.0000**。
  所以"某用户变差"本身证明不了变差：O309411 的 0.0000 也在它自己四次历史之内（其中一次就是 0.0000）。
- **机制级发现（本条真正的价值，与分数无关）**：
  - **主动性问句循环近乎空转**：8 用户合计 `questions_committed 11 / answers_linked 11 /
    answers_resolved_to_a_value **3**`——**转化率 27%**；其中 **J365414 一次都没触发（0 问）**。
    问句只有落成槽值才可能影响选择，所以**本次成绩变化不能归给主动性**。
  - **记忆/画像路径没有打中它该打的目标**：`state_loss`（personalization / long-term memory）
    在臂上的占比 **46.3%（31/67）vs 基线 44.9%（127/283）——没有下降**。
    这正是 E-090 取代修复与极性卡片应当改善的那一类。按份额方向性比较（1 试次只有 67 条失败运行，
    SE≈6pp，只能看方向）：`planning_defect` 23.3% → 19.4%（降）、
    `over_asking` 14.8% → **17.9%（升）**、`missed_question` 0.7% → 4.5%（升，仅 3 条）。
- **复现命令（全部零模型）**：
  - `python scripts/adapt_arm_report.py --arm data/simulations/adapt8_1t.json --baseline data/simulations/stock_avg4_8u.json`
    → `pooled Avg@1 = 0.3300`、`delta +0.0375`、`VERDICT: NOT RESOLVABLE`、`fixes 14 / breaks 10`、`p=0.4142`
  - `python scripts/mechanism_attribution.py data/simulations/adapt8_1t.json --json`
    → `graded=100 failing=67 pass_rate=0.3300`；`state_loss 31 / 46.3%`
  - `python scripts/arm_early_look.py --arm data/simulations/adapt8_1t.json`（逐用户 + 自校准停止线）
  - 由 `scripts/_arm_watch.ps1` 自动落盘：`data/simulations/adapt8_1t_report.txt`、
    `adapt8_1t_attribution.json`
- **预注册的有效性**：判据在 20:49（结果存在之前）写入 `docs/ADAPT_ARM_PREREG_2026-09-13.md`，
  并且在 §6b 事先算清了"配对门禁需要 net ≥ 1.96·√n"——实测 24 个翻转对、net 4，
  与事先给出的表完全对应。**本条没有事后改判据。**
- **停止规则的实测表现**：`scripts/arm_early_look.py` 用**每个用户自身**的基线极差做阈值
  （不是固定阈值——0.25 对 E057330 是噪声、对 M793481 是不可能）。运行中它只对 **J365414**
  （−0.1364 vs 自身极差 0.0909）发出 STOP，未对 O309411（−0.1250 vs 自身极差 0.2500）发出——
  事后看这个区分是**正确的**。
- **成本/收益**：约 **9 小时**算力换一个"不可分辨"的结论。这是 8 用户单试次设计的结构性代价
  （见预注册 §6b：单试次要过门禁需要 net ≥ 1.96·√n，而 24 个翻转对意味着 delta ≥ +0.10 才行）。
  下次若还要在这个队列上做判定，应当**直接上 4 试次**，并把预算事先讲清（8 用户 × 4 试次 ≈ 46 h）。
- **适用边界**：臂为 1 试次、基线为 4 试次，是**非配对**比较；差值落在不可分辨带内，
  因此本条**不支持**任何"ADAPT 比 stock 更好/更差"的表述。8 用户队列本身只有 ±0.0582 的分辨率，
  目标 0.35 需要 +0.0575，**恰好压在可分辨边界上**——这个队列对目标而言没有余量。
- **后续风险/下一步**（按证据排序，不是按偏好）：
  1. **`state_loss` 占 46.3% 且未降**：这是最大的一类，且已被两次独立测量（E-089 的 44.9% 与本次 46.3%）
     指为同一类。目标所需的 +0.0575 ≈ **6 个"从未通过"单位被完全解决**（100 单位里 60 个 0/4），
     应当直接攻这一类，而不是再加观测层机制。
  2. **主动性问句循环应当先修转化率或先下线**：27% 落值率加上"某用户 0 触发"，
     说明它在当前形态下既不稳定也不产生可用信息；继续在它上面加码没有证据支持。
  3. 若要判定任何改动是否有效，**不要再做单试次 8 用户**（结构性读不出来）。
- **能力抽象**：personalization / long-term memory（**未改善**）、proactiveness（**转化率 27%，近空转**）、
  long-horizon consistency（未测）。

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

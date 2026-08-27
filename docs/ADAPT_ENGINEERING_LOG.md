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

## E-030：自然交易请求被编译为推荐终态

- **日期**：2026-08-24
- **状态**：PARTIAL
- **通用性判定**：`GENERAL-INVARIANT`。规则只识别 agent-directed transaction grammar 及否定/信息查询边界，不包含用户、商品、商家或任务 ID。
- **难点**：完整 dev trace 中，部分“帮我团张/订下/买点”等明确交易请求被 `TaskSpec` 编译为 `recommend`，框架在搜索或推荐后直接 `DONE`，模型无法看到 CREATE 工具。
- **根因**：动作意图分散在 TaskSpec 关键词、runtime marker 和 prompt 中，对动词变体覆盖不一致；这是 completion contract 缺失，不是模型参数问题。
- **有效方案**：新增集中的 `CompletionContract(INFORM/TRANSACT/MODIFY)`，TaskSpec 和后续用户授权共用同一解析器；明确区分“帮我看看有没有团购券”与“帮我团张券”，也支持“可以，就订第一个”将 recommendation-only `DONE` 重开为 CREATE。
- **聚合审计**：`data/analysis/action_contract_audit_dev.json` 重放 8 用户/112 子任务：旧 `commit` 79 条全部保持，24 条信息请求保持 `recommend`，9 条明确交易请求由 `recommend -> commit`，无反向降级。
- **smoke**：OTA 和 instore 两种工具形态都从“不执行”进入成功 CREATE；OTA 个案 reward 仍为 0，因此只证明执行路由接通，不声称该修改单独带来全局分数提升。
- **能力抽象**：candidate-to-action execution / authorization semantics。

## E-031：口语地址别名与话语尾词混入 WRITE 参数

- **日期**：2026-08-24
- **状态**：PARTIAL
- **通用性判定**：`GENERAL-INVARIANT`。地址解析依赖 profile alias 和参数字段角色，不依赖具体地址或用户。
- **难点**：trace 中出现“送到家/公司来”未解析，以及“就行/即可”被当成 address 的情况，导致重复询问或错误 WRITE 参数。
- **有效方案**：将“家/公司/单位/店里”解析为用户 profile address alias，剥离话语尾词并对无语义值做 invalid-value 门禁。
- **验证**：112 子任务反事实重放中 invalid address value 为 0；新增 alias、尾词、明确地址优先级单测。尚未以独立 delivery paired smoke 验证 reward，所以保持 PARTIAL。
- **能力抽象**：missing-information detection / candidate-to-action execution。

## E-032：住宿日期范围被误当成单候选的并发硬约束

- **日期**：2026-08-24
- **状态**：PARTIAL
- **通用性判定**：`GENERAL-INVARIANT`。结论来自未修改的 VitaBench CREATE schema：`room_id` 绑定单日房型，checkout 是 workflow 边界，不是同一 room candidate 的第二个 date 字段。
- **难点**：“30 号到 3 号”被编译为两个同时作用于已选 room 的 date 约束，导致所有 CREATE 在 preflight 被拒绝。
- **有效方案**：日期范围编译为 check-in `ARGUMENT/candidate binding` 与 checkout `WORKFLOW`；单日任务保持原有精确日期校验，紧凑的“23-25 号”同样归一化。
- **验证**：修复前 smoke 因两个 date 约束不可同时满足而无 WRITE；修复后 `adapt_goal_contract_Q089190_rangefix` 成功创建入住日房型订单并进入支付检查。reward 仍为 0，不将候选选择问题归功于本修复。
- **能力抽象**：candidate-to-action execution / typed constraint binding。

## E-033：唯一偏好证据领先者被辅助排序顺序掩盖

- **日期**：2026-08-24
- **状态**：SUPERSEDED
- **通用性判定**：`GENERAL-INVARIANT`。修复的是“在合规集合中求 decisive score 唯一最大值”这一排序不变量，合成单测使用虚构实体。
- **难点**：framework 已在 trace 中计算出 `best_decisive_score` 且只有一个最高候选，但 WRITE 仍选择了较低偏好覆盖候选；自进化 harness 只对后续子任务学习 strict policy，无法拯救当前任务。
- **根因**：`unique_evidence_leader()` 只比较 scalar shortlist 第 1 位与其他位置，默认第 1 位就是 decisive maximum。但 scalar rank 还包含精确词、库存、时间和价格等辅助 tie-breaker；两种排序目标不同，可导致真正唯一 decisive leader 不在第 1 位，进而返回 `None`。
- **有效方案**：在全部合规 shortlist 中直接求 decisive score 最大值，仅当最大值大于 0 且唯一时锁定 WRITE。并列时仍由模型决策，用户明确选择始终优先，弱证据不升级为 decisive。
- **历史影响面**：8 用户 dev trace 共有 6 次 `preference_undercoverage`；只有 1 次满足“唯一且正的 decisive leader”，其余 5 次为并列或无 decisive leader，新逻辑不会对它们强制锁定。
- **paired smoke**：同一开发子任务、模型、seed 和工具返回下，修复前执行低证据候选，reward=0；修复后执行唯一可观测证据领先候选，CREATE 成功且 reward=1。产物：`data/simulations/adapt_goal_contract_W974351_leaderfix.json`、`data/traces/adapt_goal_contract_W974351_leaderfix.jsonl`。
- **验证**：新增“scalar 首位不是 decisive maximum”回归测试，并保留“并列不锁定”和“用户显式选择覆盖”反例；全量 `207 passed`，compileall 通过。
- **适用边界**：该修复只解决已观测候选中的唯一强证据错选，不解决未搜到候选、父子展开不足或证据并列；其硬锁部分现已被 E-042 取代。
- **后续结论（2026-08-26）**：该 paired smoke 只能证明当次排序相关，不能证明 framework 应把软偏好排名升级为硬 WRITE 裁决。硬锁会放大偏好抽取或 grounding 误差，现由 E-042 取代：`unique_evidence_leader` 保留为诊断/排序能力，只有用户明确选择可以锁定具体 WRITE 候选。
- **能力抽象**：preference-to-candidate grounding / candidate-to-action execution。

## E-034：成功 WRITE 可被恢复分支或重复生成再次执行

- **日期**：2026-08-24
- **状态**：VERIFIED
- **通用性判定**：`GENERAL-INVARIANT`。幂等键只包含用户意图 epoch、工具角色和当前调用，不包含用户、商品、商家或任务 ID。
- **难点**：历史 dev trace 中，同一交易子任务曾连续发出 7 次 CREATE；仅靠 phase 和重试次数不能区分“参数错误后的合法重试”与“成功后的重复副作用”。
- **根因**：工具错误恢复、模型重规划和 runtime phase 各自维护局部状态，没有统一记录不可逆操作是否已经成功；恢复分支也未先检查成功终态。
- **有效方案**：新增 per-subtask `OperationJournal`。失败调用允许用修正参数重试；同一 user-intent epoch 一旦 CREATE 成功，模型生成和框架恢复两条路径都禁止再次 CREATE。只有后续明确的新交易或修改授权才能显式开启新 epoch。
- **验证**：测试覆盖失败后可重试、成功后重复 CREATE 被拒绝、新 epoch 可再次执行，以及恢复分支在成功后停止；相同文本的连续子任务仍会独立初始化。全量 `211 passed`，VitaBench 源码零 diff。真实 smoke `sub_E057330_2` 只执行 1 次搜索和 1 次 CREATE，正常 `user_stop` 且 reward=1；历史同一失败模式曾连续 CREATE 7 次。产物：`data/simulations/adapt_operation_gapfix_smoke.json`、`data/traces/adapt_operation_gapfix_smoke.jsonl`。
- **能力抽象**：candidate-to-action execution / side-effect idempotency。

## E-035：memory 建议的问题与 runtime 允许的问题不是同一份契约

- **日期**：2026-08-24
- **状态**：PARTIAL
- **通用性判定**：`GENERAL-INVARIANT`。问题以 facet-independent dimension 表示，来源可以是 TaskSpec、memory 或候选歧义，不包含具体用户或实体特例。
- **难点**：历史 dev trace 中出现 memory prompt 提示 ASK，但 runtime 仍处于 SEARCH 或认为该维度并非关键缺口，导致模型提出的问题被 question gate 拒绝，继而重复搜索。
- **根因**：memory、TaskSpec、候选决策和 QuestionGate 分别推导信息缺口，字符串问题没有稳定的 typed dimension，形成多个事实源。
- **有效方案**：新增统一 `InformationGapContract`，将 TaskSpec 硬缺口、memory 条件缺口和候选歧义合并为带 `dimension/question/source/critical` 的 typed gap；runtime、问题发送计数和回答落槽消费同一个 gap。
- **验证**：测试确认 memory 产生的 `room_type` gap 被 runtime 以同一维度发送、计数并写回；重复 `read()` 仍纯函数，相同指令的新子任务获得独立询问预算。全量 `211 passed`。真实模型行为验证待双 bad-case smoke。
- **能力抽象**：missing-information detection / conditional preference / proactive interaction accounting。

## E-036：推荐终态在“选择并授权但候选不可执行”时重新进入 SELECT

- **日期**：2026-08-24
- **状态**：PARTIAL
- **通用性判定**：`GENERAL-INVARIANT`。失败由终态、授权和候选可执行性三者组合触发，不依赖具体用户、活动或商家。
- **难点**：recommendation-only 任务已经输出一次最终推荐；用户随后说“可以，就第一个吧”，runtime 识别出选择和交易授权，但当前候选只有 shop 层实体，`execution_ready=False`。状态被重开到 SELECT，framework 又输出同一推荐，如此循环至 `max_steps`。
- **根因**：DONE 重开逻辑把 `execution_ready=False` 映射为 SELECT，却没有区分“仍可通过层级展开得到可执行实体”和“没有可用 enrichment 路径”；framework recommendation 也没有本 epoch 的一次性终态输出账本。
- **反例验证**：`sub_Q089190_5` 只搜索 1 次，却重复 `recommendation_finalized` 49 次，101 条消息后 `max_steps`，reward=0。两次完整 8 用户运行中，重复推荐子任务虽由 8 个降至 5 个、过量推荐由 225 次降至 151 次，但最大单任务仍为 49 次，说明仅要求明确授权不足以关闭该循环。
- **候选通用方案**：增加 terminal-response idempotency；推荐在同一候选版本只允许输出一次。用户授权后若存在 enrichment 工具则进入 ENRICH/SEARCH，若不存在则明确说明当前候选不可直接预订并 DONE，禁止退回 SELECT 重复播报。
- **能力抽象**：long-horizon consistency / terminal-state idempotency / candidate-to-action execution。

## E-037：preflight 将被拒绝的模型提案计入真实搜索预算

- **日期**：2026-08-24
- **状态**：OPEN
- **通用性判定**：`GENERAL-INVARIANT`。问题来自提案、校验和提交的事务边界，不依赖搜索关键词或实体。
- **难点**：最新 8 用户 trace 有 75 次 preflight rejection，其中搜索预算拒绝 15 次；错误信息甚至出现“已尝试 3/4/5 次”，但结果文件中的真实重复搜索 excess 为 0。
- **根因**：`register_search()` 在整条 assistant action 通过 preflight 之前就递增计数。一个附带问题、参数不合法或最终未发给环境的 tool proposal 也会消耗搜索预算；达到上限后 `search_allowed()` 又始终返回 True，使模型继续看到已不可用的搜索工具并反复被拒绝。
- **候选通用方案**：改为 `propose -> validate -> commit` 两阶段 action transaction；preflight 只做无副作用的 `would_allow()`，仅在动作真正发往环境时登记搜索执行。family 达上限后从 allowed tools 中移除对应搜索工具。
- **能力抽象**：long-horizon consistency / action accounting / loop control。

## E-038：Decision Card 的内部偏好池绕过了 8 条事实边界

- **日期**：2026-08-24
- **状态**：OPEN
- **通用性判定**：`GENERAL-INVARIANT`。问题由任务卡、候选诱导召回和排序视图的职责混合造成，不依赖具体偏好类型。
- **难点**：prompt 的 `render()` 虽限制为 8 条事实，但 `alignment_preferences()` 仍合并未截断的 `card.prefer` 与最多 64 条 pool。最新 8 用户中，`card.prefer` 的分位均值从 33.0 增长到 57.2、104.3、64.8；同期四分位 reward 从 0.250 降到 0.214、0.179、0.179，前后半程差为 -0.068。
- **根因**：Decision Card 同时承担用户可见决策摘要、全量召回池和候选排序输入。候选 grounding 还会原地修改 card，使长序列实体证据逐渐扩大排序噪声。
- **候选通用方案**：将不可变 `TaskDecisionCard` 与派生 `CandidateGroundingView` 分离。前者严格最多 8 条；后者只保留对当前候选实际落地的 safety/explicit/conditional 和分维度公平的少量事实，并按候选版本重建而非累积修改。
- **能力抽象**：preference-to-candidate grounding / long-horizon consistency / bounded memory projection。

## E-039：语义 TaskSpec 与实际工具工作流没有显式分层

- **日期**：2026-08-24
- **状态**：OPEN
- **通用性判定**：`GENERAL-INVARIANT`。工作流由当前环境暴露的工具 schema 和候选 ID 类型推导，不依赖 benchmark 隐藏字段。
- **难点**：自然语言可将“活动”编译为 OTA/attraction，但实际环境暴露的是 instore shop/product/book/reservation 工具。当前 enrichment 依赖硬编码 facet 映射且只覆盖 OTA，导致 shop 层候选到 product、book 或 reservation 的可执行路径不完整。
- **根因**：同一个 `domain/facet` 同时服务偏好召回和工具执行；`update_tools()` 只建立角色元数据，没有用 schema 构建候选实体到 CREATE 参数的工作流图。
- **候选通用方案**：保留 semantic facet 用于偏好召回，另建 tool-derived `ExecutionWorkflow`。根据工具前缀、required ID 参数和返回候选类型推导 SEARCH/ENRICH/CREATE 边；状态机只依据该图判断 `execution_ready` 和下一层动作，删除按 facet 编写的 enrichment 表。
- **能力抽象**：candidate-to-action execution / schema grounding / cross-domain generalization。

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

## E-030：任务语义被分散词表和候选重放状态限制

- **日期**：2026-08-24
- **状态**：PARTIAL
- **通用性判定**：`GENERAL-INVARIANT`。修复依据当前工具拓扑、TaskSpec 声明槽、候选可观察属性和显式状态事件，不使用用户、任务、商品、商家、reward、rubric 或 target 信息。
- **难点**：未见商品/服务只有预先进入多个中文品类词表后才能形成硬约束；到店新服务可能被默认成 delivery/retail。用户选择候选时又重放 `observe_candidates()`，把选择事件混成新的搜索观察。agent 层另有固定小料查询改写和餐饮甜品/口味追问，会把少量开发案例的策略扩散到不相关阶段。
- **根因**：domain、facet、missing information 和 query rewrite 分散在 TaskSpec、memory proactive、retrieval、signal parser 与 agent controller；候选身份没有从当前工具结果反向落地，状态机也缺少显式 selection transition。
- **已实现方案**：工具名称拓扑成为 domain 高置信 hint；当 typed category/entity 无可落地证据时，从当前指令与候选 name/dynamic field/tag 诱导一个最长的 task-identity anchor，用于过滤错误品类。新增 `TaskRuntime.select_candidate()`，不再借候选观察推进选择。runtime 询问只接受 TaskSpec 的 required/unknown slot，memory 只能解析已声明槽；搜索只规范参数形状，不再改写成固定品类配方。
- **反例门禁**：候选 identity 只在无可落地 typed category/entity 时启用；包含“或者/任选/都行”的替代请求不被收紧成合取；商家名不作为 task identity；显式 current instruction 仍优先于 memory；search keywords 语义保持不变。
- **验证**：新增四个未见零售品类（家电、环境电器、视光、户外）和一个未见到店服务的虚构测试，并验证 selection 只产生一次候选观察。完整测试 `214 passed`。尚未运行真实模型 smoke 或 8 用户 dev，因此不声称 benchmark 收益。
- **适用边界**：最长公共片段只处理候选字段中可观察的连续词面证据；同义词、完全隐含属性和候选未暴露字段仍无法确定性 grounding。工具拓扑只纠正 domain，未知细 facet 仍回退为 service/retail/travel。
- **能力抽象**：missing-information detection / preference-to-candidate grounding / candidate-to-action execution / long-horizon consistency。

---

## E-031：候选决策依赖封闭品类词表，未见 schema 无法复用执行不变量

- **日期**：2026-08-24
- **状态**：PARTIAL
- **通用性判定**：`GENERAL-INVARIANT`。字段角色、候选身份、父子关系和硬约束均由工具 schema 声明；实现和测试不包含 VitaBench 用户、商品、商家或目标 ID。
- **难点**：TaskSpec、CandidateRanker 和 WRITE preflight 曾分别维护商品品类、规格、小料、口味和服务词表。同一约束在未见实体或字段改名后会消失，而候选的近似字段又可能被错误提升为全局硬约束。
- **已实现方案**：`ToolMeta` 从工具输入/输出 schema 建立 entity、ID、name、inventory、price、parent、attribute 和 question roles；只有显式 `x-adapt-constraint` 字段能成为 WRITE 硬约束。CandidateLedger 按 schema 解析任意嵌套记录、任意 ID 字段和父子关系；TaskSpec 不再枚举商品类别/规格；排序只消费当前候选诱导的可观察属性；WRITE 校验只接受本子任务账本中的 ID，并按 schema 类型和父子关系校验。OperationJournal、授权、库存、重复搜索和支付状态等硬不变量保留。
- **关键反例**：未声明为硬字段的候选属性只能参与排序，不能把不同候选的两个值提升成不可满足的合取；父实体 ID 不能冒充叶候选；选中的子实体必须属于同时提交的父实体；显式 schema 硬字段仍会拒绝错误值。
- **跨结构验证**：虚构 `nebula.echoes[] -> relic` schema 使用不透明字段和单 ID；虚构根数组 `glyph` schema 使用另一组不透明字段、复数 ID 和显式硬字段。两套结构均验证观察、排序、WRITE、非候选 ID、错误父子关系和 schema hard constraint，且不依赖领域词汇。
- **验证**：完整 `agent/tests` 为 `219 passed`，`python -m compileall -q agent` 通过，`git -C evaluation/vitabench diff --exit-code HEAD -- src/vita` 通过。静态改动移除了候选决策层的品类、规格和配方表；memory extraction 中保留的 facet marker 仅用于历史召回元数据，不再直接决定候选或 WRITE。
- **适用边界**：schema 没声明的语义同义关系仍不能确定性推导；schema-driven 解决结构迁移，不等同于开放世界语义分类。真实模型 smoke 的这类缺口见 E-032。
- **能力抽象**：preference-to-candidate grounding / candidate-to-action execution / long-horizon consistency。

## E-032：开放世界结构迁移通过，但需求文本到候选语义仍可能失配

- **日期**：2026-08-24
- **状态**：OPEN
- **通用性判定**：`UNRESOLVED`。一个完整开发用户 smoke 已复现多个能力类失败，但尚不足以选择安全的静态修改；本条只记录，不增加品类或用户规则。
- **证据**：`data/simulations/adapt_schema_runtime_smoke_U010122.json` 与 `data/traces/adapt_schema_runtime_smoke_U010122.jsonl`。1 个用户、10 个子任务、106 条对话消息；6 次 `create_delivery_order` 成功，环境工具错误为 0，10 个子任务 reward 均为 0。运行只使用正常 task、user、environment 和 evaluator 接口，未读取 rubric、target/distraction 或 target product。
- **已分离的通用失败类**：推荐任务在用户接受候选后重复输出 shortlist，缺少 recommendation completion transition；6 个 CREATE 均进入 unpaid 后询问支付，未形成端到端完成闭环；火车和酒店父子候选展开消耗有限 step budget；条件到店任务在工具族间路由错误；酒店模型连续选择 24 日房型时，`23号` 写前校验正确拒绝三次；“买衣服”最终选择运动鞋，说明 schema 正确并不保证需求词面/语义与候选一致。
- **排除的误诊**：`23号` 与 ISO `YYYY-MM-23` 的等价校验已有虚构回归并可直接通过；该 trace 的拒绝是选错候选日期，不是日期格式 bug，不能通过放宽校验修复。
- **当前结论**：本轮证明新架构能跨结构解析并稳定执行多次 CREATE，也证明 0 分的主导瓶颈已从“候选字段/ID 解析”迁移到 completion、authorization、hierarchical exploration 和 semantic grounding。不能把 6 次 CREATE 描述为指标提升，也不能用单用户 0 分否定 schema 不变量测试。
- **下一步**：为“需求到候选”构造不含真实品类的语义正反例，比较受约束 policy selector 与纯词面 anchor；必须同时测试短词、同义表达、替代需求和错误高分候选。只有跨至少两个虚构工具形态成立，才接入 runtime。支付授权继续按 E-023 独立评估，不因 benchmark STOP 放宽安全边界。
- **能力抽象**：preference-to-candidate grounding / candidate-to-action execution / proactiveness calibration。

## E-033：只看平均 reward 会混淆整轨迹与子任务成功率

- **日期**：2026-08-24
- **状态**：VERIFIED
- **通用性判定**：`GENERAL-INVARIANT`。指标直接复现 VitaBench `reward == 1.0` 的 strict-success 定义，不依赖用户、领域或候选内容。
- **难点**：现有 `agent.trace_metrics` 只报告每用户聚合 reward 的均值，无法同时观察 task-level `Avg@1/Pass@1` 和 personalization subtask-level `Avg@1/Pass@1`。这会把“没有任何完整用户满分”误读为全部能力没有改善，也会诱导逐用户追求满分。
- **已实现方案**：汇总结果同时输出 `task_avg_at_1`、`task_pass_at_1`、`subtask_avg_at_1`、`subtask_pass_at_1` 和子任务数；comparison 同时报告四项 delta。`avg_reward` 保持为 task-level Avg@1 的兼容别名。
- **聚合重算**：`adapt_dev_tiered` 的 task Avg@1=`0.200424`、task Pass@1=`0`，而 112 个子任务的 Avg@1/Pass@1 均为 `0.214286`；`adapt_dev_pre_operation_gapfix` 分别为 `0.196778/0` 和 `0.205357/0.205357`。task Pass@1 为 0 只表示没有一个用户的整段轨迹满分，符合 VitaBench 2.0 长序列难度，不能作为要求逐用户修满的理由。
- **失败簇证据**：`adapt_dev_tiered` 中 59 个 commit 子任务成功 CREATE 后进入支付询问，20 个成功；14 个 commit 子任务搜索后未完成 WRITE，全部失败；29 个 recommendation 被框架 finalize，仅 3 个成功。该统计用于排序能力投入，不推断隐藏目标候选。
- **验证**：指标单测覆盖 task reward=0.5、两个二值子任务以及 comparison delta；只读取聚合 reward 和可观察 trace，不读取 rubric、target 或 evaluator 文本。
- **评测取舍**：开发、smoke 和 56 用户单次验证只以 task-level `Avg@1` 决定晋级；task/subtask Pass@1 仅作失败诊断。最终四次完整评测同时报告并优化 `Avg@4` 与 `Pass@4`。
- **下一步**：优先减少 commit 的 search-without-completion，再评估候选语义和支付策略。不得通过放宽 WRITE 校验或默认自动支付换取表面动作率。
- **能力抽象**：evaluation harness / candidate-to-action execution / long-horizon consistency。

## E-040：grounded policy 放宽了偏好硬限制，但未恢复历史开发集 Avg@1

- **日期**：2026-08-25
- **状态**：PARTIAL
- **通用性判定**：`GENERAL-EMPIRICAL` 失败信号。完整 8 用户开发集支持能力簇层面的诊断，但尚不能把任一单独机制判为根因，也不能据此增加品类、用户或实体规则。
- **证据**：`data/simulations/adapt_dev_schema_driven_v1.json` 和 `data/traces/adapt_dev_schema_driven_v1.jsonl` 完成 8 用户、112 子任务、单 trial 运行。task Avg@1=`0.158848`，低于同 cohort 历史 `adapt_dev_tiered` 的 `0.200424`，差值 `-0.041576`；subtask Avg@1 从 `0.214286` 降至 `0.169643`。本次运行环境工具错误为 `0`、三次以上重复搜索为 `0`，但未完成支付诊断为 `8`（历史为 `7`）。因此本版本不通过开发晋级门槛。
- **可观察失败簇**：离线 action audit 只读取 instruction、domain/facet/action 和 runtime lesson，得到 candidate validation `57`、preference undercoverage `18`、candidate choice re-ask `12`、repeat search `11`、mixed question/tool `9`、user correction `9`、missed write `2`。这些是事件计数，不等同于互斥失败任务，也不使用 evaluator 答案。
- **当前判断**：schema/ID/父子关系和 OperationJournal 等硬不变量仍有独立测试价值，但“结构正确”不足以保证需求到候选的语义选择、选择后的阶段迁移和端到端动作完成。候选校验事件过多与 Avg@1 下降同时出现，只能作为“可能过约束或上游选择质量差”的假设；不能直接通过放宽校验修复。
- **无效结论**：不能因工具错误降为 0 就声称 benchmark 提升；也不能将 57 次校验拒绝全部视为误拒。支付未完成仍可能包含未获授权的安全停止，不能默认自动支付。
- **已实现的通用修正**：取消 `preference_undercoverage` 到硬 WRITE policy 的编译，保留其只读诊断事件；RuntimePolicyStore 的硬策略只接受封闭的显式状态失败类，并把工具族与候选实体/父子拓扑加入作用域。候选排序先以当前指令建立 task identity，未可靠 grounding 时不让历史偏好决定候选类别；对“水/杯/酒”等短实体，只允许候选属性后缀提供任务锚点。ID、库存、父子关系、日期、地址、授权和 OperationJournal 均未放宽。
- **静态验证**：新增“短任务不得被牛奶偏好带离饮用水”“未见实体 grounding 后才允许历史偏好族内排序”“policy 不跨工具族/候选结构迁移”等正反例；完整 `agent/tests` 为 `223 passed`，compileall 和 VitaBench `src/vita` 只读检查通过。
- **公平复验**：`data/simulations/adapt_dev_grounded_policy_v2.json` 与 `data/traces/adapt_dev_grounded_policy_v2.jsonl` 已按相同 cohort、seed、单 trial 和 `max_steps=100` 完成 8 用户、112 子任务。task Avg@1=`0.167628`，相对 schema-driven v1 的 `0.158848` 回升 `+0.008780`，但仍低于历史 `adapt_dev_tiered` 的 `0.200424`，差值 `-0.032796`；subtask Avg@1=`0.178571`，仍低于历史 `0.214286`。因此“取消偏好覆盖硬限制即可恢复并反超”的假设未获支持，本版本不进入盲验。
- **公平复验失败簇**：只读 action audit 记录 candidate validation `93`、candidate choice re-ask `39`、repeat search `19`、tool error `15`、user correction `9`、mixed question/tool `7`、missed write `2`；状态转移中 commit→commit `88`、recommend→recommend `24`。真实工具错误由历史 `4` 增至 `15`，未完成支付均为 `7`，三次以上真实重复搜索均为 `0`。这些结果说明主要瓶颈已从偏好覆盖硬限制转向提案/预检事务边界、候选选择后的动作闭环和工具实体角色混淆。
- **下一步**：保留本条中的 task grounding 与结构化 policy scope，但不得把本轮回升解释为已验证提升。优先修复 E-037 的无副作用 preflight 事务和 E-039 的 schema-derived execution workflow，并用虚构 schema 验证 product/order、parent/child 与 SEARCH/ENRICH/CREATE/PAY 角色，再做 1–2 用户 smoke；不得为本轮低分用户增加特例。
- **能力抽象**：preference-to-candidate grounding / candidate-to-action execution / missing-information detection / long-horizon consistency。

## E-041：工具生命周期、预检记账与候选/状态角色混淆

- **日期**：2026-08-25
- **状态**：PARTIAL
- **通用性判定**：`GENERAL-INVARIANT`。修复由子任务 epoch、工具输入 schema、操作提交边界和 ID 来源类型决定，不依赖用户、商品或商家实体。目前只通过静态与集成测试，尚未通过 1–2 用户 smoke，因此不声称指标提升。
- **难点**：真实 VitaBench 工具没有 `x-adapt-*` 注解，`search_delivery_orders` 曾被当成候选 SEARCH，订单 status/detail 返回又被写入 CandidateLedger；同时被 preflight 拒绝的搜索和详情提案会提前消耗预算。`update_tools()` 还可在旧子任务已开始后重编译 TaskSpec，导致 stale context rebound。
- **根因**：旧架构只有 `SEARCH/READ/WRITE` 粗角色，候选发现、父子 enrichment、workflow state read 和 utility 共用一条 observation 路径；preflight 同时承担校验与提交记账；指令与工具更新没有独立 epoch。
- **有效方案**：新增 `SubtaskContext(instruction_epoch, tool_epoch)` 并在旧任务有活动后暂存新工具；将工具角色拆为 `SEARCH/ENRICH/STATE_READ/UTILITY/CREATE/PAY/...`，依据名称、必需 ID 参数和 schema 观测分类；CandidateLedger 与 workflow `state_ids` 分离，错误工具返回不再入账；preflight 只 preview 搜索/enrichment 计数，仅 `_observe_assistant()` 在真实发出后 commit；新增 `ResponseJournal` 以 operation epoch + candidate version 去重终态响应。
- **执行依赖**：提交任务初始阶段隐藏无关订单状态工具；ENRICH 只在其必需父 ID 已观测时暴露；执行就绪只检查必需 ID 和合规候选，不再把“展开 6 个酒店”之类品类覆盖策略作为硬 WRITE 门禁。多父展开仍可用于排序，但不阻断已具备完整父子 ID 的动作。
- **验证**：真实未注解 DeliveryTools 回归覆盖 product search、product enrichment、order search/status/detail 分类及初始可见性；完全虚构 `quasar_id` schema 验证无 facet 词表的 enrichment；另覆盖 preflight 重复调用不改变计数/阶段、state 不污染 candidate、tool epoch 暂存以及 response epoch 去重。`python -m compileall -q agent` 通过，`agent/tests` 为 `232 passed`，VitaBench `src/vita` 只读检查和 `git diff --check` 通过。
- **smoke 结果**：`data/simulations/adapt_deterministic_smoke_2u_v1.json` 完成固定开发用户 `U200109` 的 15 个子任务，`max_steps=100`、seed=42。Avg@1=`0.20`，与同用户的 `adapt_dev_tiered` 和 `adapt_dev_grounded_policy_v2` 完全相同；工具错误、阻止 WRITE 和超额重复搜索均为 `0`。因此确定性改造在该用户上未降分，但也没有指标提升证据。第二用户在已发现通用回归后停止，未计入结果。
- **smoke 中的通用回归及修复**：推荐后用户说“行，就第一个吧”时，旧 `ResponseJournal` 把重复推荐关闭为 DONE 却继续落入 LLM/preflight，导致三次拒绝和虚假操作声明。修复后，只有“已展示当前 shortlist + 用户明确选择序号”才升级 CREATE 授权；单纯“可以”不授权；响应去重关闭 DONE 后立即返回，且不再禁止 VitaBench 合法 `###STOP###` 协议。`data/simulations/adapt_ordinal_transition_smoke_v1.json` 定向复验已将路径从“推荐 → 第一个 → fallback/虚假声明”修正为“推荐 → 第一个 → CREATE → 待支付询问”；该单子任 reward 仍为 `0`，所以只能证明控制流修复，不能声称指标改善。
- **适用边界**：无注解工具仍需使用通用 workflow 词干区分 order/booking/reservation；room/ticket/seat 到 product ID 的兼容映射仍是 VitaBench 无输出 schema 时的退化层，未声称已解决开放世界语义 grounding。
- **后续风险/下一步**：不继续围绕 `U200109` 调优。下一个延伸验证应使用另一固定开发用户，检查序号选择授权、无关状态工具隐藏和 enrichment 依赖是否保持；在跨用户证据出现前保持 PARTIAL。
- **能力抽象**：candidate-to-action execution / long-horizon consistency / preference-to-candidate grounding。

## E-042：候选排名、候选族和展示指代缺少清晰边界

- **日期**：2026-08-26
- **状态**：PARTIAL
- **通用性判定**：`GENERAL-INVARIANT`。修改只依赖软/硬证据类型、CREATE 输入 schema、已观测父子拓扑和实际展示顺序；合成测试使用 quasar/node 等虚构实体，不包含用户、商品、商家或目标 ID。
- **难点**：候选决策层仍有三处职责混合：`unique_evidence_leader` 会把软偏好最高分升级为硬 WRITE 拒绝；`shortlist()` 以 product/hotel/attraction/flight/train/shop 固定顺序挑选候选类型；用户说“第 N 个”时重新计算 shortlist，而不是绑定先前真正展示的顺序。
- **根因**：ranking、admissibility、execution schema 和 presentation state 共用一个动态 shortlist。框架自己的偏好评分既用于排序又用于否决动作；候选族缺少 CREATE schema 视图；ResponseJournal 只记录“已发送”，没有记录展示内容。
- **有效方案**：软偏好仅产生排序和 `preference_undercoverage` 诊断，`validate_ranked_choice` 只硬锁用户明确选择，ID provenance、库存、MUST/AVOID、参数和父子关系仍由 `validate_write` 强制。ToolRegistry 根据 CREATE 必需 ID 类型和 CandidateLedger 父子边推导可执行候选，已观测父子关系中的父节点只作为 WRITE 关系参数，不占候选 shortlist；无输出注解的单一未解析类型只允许映射到唯一观测子类型。ResponseJournal 保存 `(operation_epoch, candidate_version, response_type) -> ordered candidate IDs`，序号选择消费实际展示快照且在选择触发新 transaction epoch 前完成绑定，不重新排名。
- **反例门禁**：九个以上候选时，排在展示 top-8 之外但满足硬约束的候选不会仅因软排名被拒；用户明确选择仍会拒绝不同 WRITE ID；未知 `quasar` CREATE schema 不受旧实体优先级影响；父子同为 `node` 类型时仍只展示叶节点；没有展示快照的裸序号不获得 CREATE 授权。
- **验证**：新增软排名非硬门禁、虚构 schema 候选族、同类型父子叶节点、推荐函数写入展示快照、快照顺序与重新排名不一致时仍按展示选择，以及真实 `_observe_input` 中“选择后开启新 epoch”顺序测试。完整 `agent/tests` 为 `237 passed`；compileall、VitaBench `src/vita` 只读检查和 `git diff --check` 通过。
- **适用边界**：真实 VitaBench 未标注输出 schema 时仍需基于可观察 ID 字段和父子关系做保守退化；本轮尚未运行跨用户 smoke，因此只证明确定性边界正确，不声称 Avg@1 已提升。
- **后续风险/下一步**：下一次 1–2 用户 smoke 只观察错误候选/参数调用、重复询问和 `SELECT -> CREATE`，不得围绕单个 reward 追加实体规则。
- **能力抽象**：preference-to-candidate grounding / candidate-to-action execution / long-horizon consistency。

## E-043：CREATE 覆盖上升但 Avg@1 下降，动作量与动作质量脱钩

- **日期**：2026-08-26
- **状态**：PARTIAL
- **通用性判定**：`GENERAL-EMPIRICAL`。证据来自同配置 8 用户/112 子任务聚合与通用 runtime 代码路径；没有读取隐藏 rubric/target，也没有形成用户或实体特例。
- **难点**：公平版 `adapt_dev_grounded_policy_v2` 的 commit 子任务中有 75 个产生 CREATE 提案，高于历史版的 63 个，但 task Avg@1 从 `0.200424` 降至 `0.167628`。这否定了“提高 WRITE 覆盖即可提升”的简单假设。
- **证据**：公平版 12 个 commit 子任务搜索后无 CREATE、8 个 repeat-search 子任务、6 个重复 recommendation 子任务和 3 个真实 tool-error 子任务均为 0 成功；这些路径的去重并集为 21 个子任务且 0 成功。candidate-choice re-ask 影响 14 个子任务，其中仍有 4 个成功；candidate validation 影响 26 个子任务，其中 5 个成功，因此两类事件不能直接等价为失败或全部放宽。
- **根因假设**：候选 admissibility、task relevance、preference ranking、CREATE schema binding 和 runtime phase 分散在多个模块，导致“动作已授权”仍可停留在 SELECT、不同模块重算不同 shortlist、模型提出错误父子/参数组合，以及被拒提案污染后续上下文。当前 longest-common-substring task identity 和固定 room/ticket/seat 兼容层也可能把语义选择与结构校验混在一起。
- **明确无效的方案**：继续提升工具调用积极性、把 candidate validation 全部视为误拒、把 payment question 视为自动支付授权，或按低分实体添加规则。CREATE 覆盖与分数已经显示动作量不是充分条件。
- **已实施的通用方案**：按 `docs/AVG1_OPTIMIZATION_BLUEPRINT.md` 完成 CandidateDecision、CandidateBindingGraph/CallLineageLedger、结构化 ToolOutcome、无副作用 ActionTransaction、当前任务优先的分层 grounding 与 schema-driven information gap。候选 admissibility 与最终调用参数完整性分离：未标注的必填执行参数不再被误判为用户问题，但真实 WRITE 仍由 preflight 严格检查。历史偏好只参与排序；父实体文本不参与叶候选 task identity；展示快照、ID provenance、库存、父子关系、日期、地址、授权和 OperationJournal 仍是硬边界。
- **静态验证**：五阶段迁移按 schema contract/lineage shadow、CandidateDecision、ActionTransaction/ToolOutcome、task grounding/schema question、受限 runtime harness 分成五个顺序 commit。三类通用门禁分别为 structural metamorphic、transaction invariant 与 counterfactual semantic tests，全部使用虚构 schema/实体并含反例。完整 `agent/tests` 为 `267 passed`，`compileall`、VitaBench `src/vita` 只读检查和 `git diff --check` 通过。
- **验证门禁**：先用虚构 schema 和反例测试，再以相同 `max_steps=100` 做 1–2 用户 smoke；完整 8-user dev 第一门槛为 task Avg@1 不低于 `0.200424`、成功子任务至少 `24/112`，同时重复 recommendation 为 0、commit search-no-create 至少下降 50%。未达门槛不得进入 blind。
- **适用边界**：21 个零成功子任务是可寻址池而非可保证新增成功；恢复 4 个仅能说明 subtask 成功数回到历史水平，不能直接预测 task Avg@1。
- **后续风险/下一步**：尚未跑迁移后的 1–2 用户同配置 smoke，因此不能声称 Avg@1 已恢复或提升。下一验证只比较 `search -> admissible -> CREATE`、错误参数/父子绑定、重复搜索、重复询问和最终 task Avg@1；若静态正确但分数下降，按五个 commit 的里程碑边界定位回归，不为单个用户或实体加规则。
- **能力抽象**：preference-to-candidate grounding / candidate-to-action execution / missing-information detection / long-horizon consistency。

## E-044：自进化硬策略缺少可审计证据源，且拓扑作用域曾依赖 product 特例

- **日期**：2026-08-26
- **状态**：SUPERSEDED（硬策略边界由 E-046 进一步收紧）
- **通用性判定**：`GENERAL-INVARIANT`。证据来源、事件是否真实提交、同用户延迟生效和父子拓扑均为 runtime 类型不变量，不依赖 benchmark 用户、商品或商家。
- **难点**：旧 `RuntimePolicyStore.observe()` 只接收 failure class，调用方无需证明它来自真实工具错误还是框架自评；`repeat_search` 虽有模板却没有从真实提交计数接入；policy entity signature 只为 `product` 保存父类型，虚构实体父子结构会丢失。这样的 harness 可能把自身排名/拒绝放大成硬 WRITE 规则，也无法稳定迁移到开放世界结构。
- **根因**：failure class、evidence source、commit boundary 和 policy scope 分散在 Agent、CandidateLedger 与模板表中，没有一个闭合的编译契约。
- **无效方案**：继续允许 `preference_undercoverage`、`candidate_choice_reask` 或 `mixed_question_tool` 先触发后学习；它们分别属于软排名诊断或应由 CandidateDecision/ActionTransaction 固定保证的核心不变量，不应等犯错后才启用。
- **有效方案**：新增闭集 `TrajectoryEvidenceSource`，每个硬模板声明唯一允许的来源；`observe()` 缺少来源或来源不匹配即拒绝。硬模板保留真实工具错误、已真实执行的重复搜索和明确 unresolved operation；E-046 进一步证明 `missed_write` 仍来自 Agent 自身的候选判断，已从硬模板删除。用户纠正只保留为当前会话证据，不生成跨任务硬候选规则。规则继续在同一用户下一子任务才生效，并要求 domain/facet、observable tool family 与任意实体父子 signature 全部相同；切换用户清空。工具错误策略只降低相同结构后续任务的同签名失败额度，不保存错误 ID 或参数值。
- **验证**：覆盖错误/缺失 evidence source、框架 ranking/self-diagnostic 不学习、真实提交 search count 才触发、下一子任务延迟、跨用户清空、工具族/结构不匹配不迁移、虚构 `glyph(origin)` 父子 signature，以及 ToolErrorLedger 硬策略接线。完整测试门禁见 E-043，为 `267 passed`；尚无 smoke 分数证据。
- **适用边界**：核心正确性不能依赖 harness；它只能对同用户后续同结构任务做有界调节。用户纠正仍必须由当前会话约束立即处理，不能等待在线学习。没有可靠工具族或实体结构时不做跨任务硬迁移。
- **后续风险/下一步**：smoke 中同时记录 active policy、evidence source、scope 和作用前后的实际事件；若规则未改善对应能力指标则回退该 effect，不扩大 failure vocabulary。
- **能力抽象**：candidate-to-action execution / long-horizon consistency / missing-information detection。

## E-045：业务失败被 transport success 包装后污染操作状态

- **日期**：2026-08-26
- **状态**：PARTIAL
- **通用性判定**：`GENERAL-INVARIANT`。判定只依赖可见工具返回的结构化状态、文本失败语义、工具角色和状态 ID 来源，不依赖用户、实体或 evaluator。
- **难点**：VitaBench 工具会以普通字符串返回 `No available rooms`、`does not have the specified seat` 或 `Order not found`；旧逻辑只看 `ToolMessage.error`，会把这些业务失败当作 CREATE/CANCEL/MODIFY 成功，继而污染 OperationJournal、pending payment、ToolErrorLedger 和 runtime phase。另一方面，`No delivery orders available` 对 STATE_READ 是成功的空观察，不应进入错误重试。
- **有效方案**：ToolOutcomeNormalizer 先解析结构化 `status/success/error`，再使用隔离的 legacy 文本兼容层；业务失败统一产出 `ok=False/effect=no_change`，空状态查询产出 `ok=True/effect=observed`。所有 ledger、lineage、journal、lesson 和 phase 迁移只消费 normalized outcome，不再消费 transport error。STATE_READ 无记录时直接进入 REPORT；取消/修改工具只暴露与已观察 state ID 类型和来源工具族相容的动作，并自动绑定唯一状态 ID 与 profile user ID。
- **反例门禁**：`{"status":"success","error":null}` 必须保持成功；STATE_READ 的空列表/无记录不得学习 tool-error；错误 CREATE 不得写入成功 journal；多个 cancel 工具只能由 state-read provenance 排序，无法唯一判定时仍保留模型选择而不猜 ID。
- **验证**：新增普通失败字符串、结构化失败、结构化 success+null error、空状态、虚构 order/booking cancel 工具和 `search_train_order -> cancel_train_order` provenance 测试。完整门禁见 E-047；尚未获得 dev 指标证据。
- **能力抽象**：candidate-to-action execution / long-horizon consistency。

## E-046：候选计划、当前纠正和 runtime harness 的权威边界不闭合

- **日期**：2026-08-26
- **状态**：PARTIAL
- **通用性判定**：`GENERAL-INVARIANT`。测试使用 glyph/quasar/order 等虚构 schema，修改依据任务约束、工具输入结构、可见候选与证据来源，不包含用户、商品或商家特例。
- **难点**：CandidateDecision 虽返回 selected binding，但 READY_CREATE 仍可暴露多个 CREATE，模型可重新选择工具和 ID；不同 CREATE 的缺失参数被全局合并，错误阻断正确工具；profile address alias、party size、quantity 和 modify date 没有统一绑定层。当前用户纠正只记录 lesson，不会立即替换活动 TaskSpec；漂移 detector 又强制使用 default category。harness 还能依据自身 `has_executable_candidate` 推断 missed-write 并生成 FORCE_CREATE 硬策略。
- **有效方案**：selected CandidateBinding 成为唯一 ExecutionPlan，runtime 固化 selected tool 与 planner-owned arguments，工具暴露和 prepared call 都消费同一计划；新增 schema-driven ArgumentBindingResolver 处理 profile alias、人数、数量、日期/路线和 JSON 类型。当前会话 correction overlay 立即重编译并替换同槽 scalar/entity，独立禁项保持集合；漂移使用事实真实 `(scope, facet, dimension, category)`。LLM preference extraction 只接收 user turns，输出对象必须在用户原话有锚点。`missed_write` 从硬模板和轨迹收尾推断删除，仅用户纠正、工具错误和明确状态失败能进入相应证据层。
- **反例门禁**：纯推荐中的序号不自动授权交易；只有实际展示快照中的明确选择才可重开 CREATE；软偏好不能写入 planner-owned ID；不同 category 的同维偏好不漂移；assistant 自述偏好和无 user-text anchor 的 LLM 输出必须丢弃；一个工具缺参数不能阻断另一个已完整工具。
- **验证**：新增 per-tool missing、唯一 CREATE 暴露/ID freeze、party/address binding、多禁项 correction、跨 category drift、LLM user anchor、protected lifecycle 和 `missed_write -> no hard rule` 测试。尚未声明 Avg@1 提升。
- **能力抽象**：preference extraction / preference drift / missing-information detection / preference-to-candidate grounding / candidate-to-action execution。

## E-047：checkpoint 无实现指纹且本地汇总不等价于官方 Avg@k/Pass@k

- **日期**：2026-08-26
- **状态**：VERIFIED（静态/聚合定义）
- **通用性判定**：`GENERAL-INVARIANT`。只涉及运行配置、代码内容和公开 reward 聚合，不读取 rubric、target 或逐案例隐藏信息。
- **难点**：同一路径 checkpoint 只比较模型名、seed 和 max_steps，代码或模型配置改变后仍可能续跑，造成混合版本结果；旧 trace_metrics 只报告 @1，无法正确汇总最终四 trial。把 Pass@4 简化为普通均值也不等价于 VitaBench 的组合公式。
- **有效方案**：runner 在 checkpoint 中写入 ADAPT runtime 源码/config 内容指纹与 Vita 实际解析的 model YAML 指纹，并把 task/trial/seed/implementation fingerprint 写入 debug context。续跑必须完整匹配。trace_metrics 按 task/subtask 分组，使用 VitaBench `1-C(n-c,k)/C(n,k)` 计算 Pass@k，并按官方定义计算 Avg@k；单 trial 开发评测仍以 task Avg@1 为晋级指标。
- **验证**：两任务四 trial 合成结果验证 Avg@4=`0.4875`、Pass@4=`0.5`、Pass@1=`0.125`，并验证代码与实际模型配置均产生 20 字符指纹。VitaBench 源码保持只读。
- **适用边界**：聚合器只能汇总已完成的 trial；中断 checkpoint 会把 `evaluation_k` 降为当前完整可见 trial 数，不能伪装成最终 @4。
- **能力抽象**：evaluation harness / reproducibility / long-horizon consistency。

## E-048：合成单测通过但真实 WRITE schema 与候选阶段仍发生确定性回归

- **日期**：2026-08-26
- **状态**：PARTIAL（静态与反事实门禁已通过，模型端点未恢复，尚缺 1–2 用户 smoke）
- **通用性判定**：`GENERAL-INVARIANT`。根因分别是 JSON schema 递归类型、候选 admissibility 与动作参数完整性的职责边界、中文强调词的实体证据强度，以及 enrichment 总预算；不依赖用户、商品、商家或隐藏目标。
- **难点与失败证据**：`adapt_dev_execution_plan_v3` 在 8 用户运行到 4 个完整用户时 task Avg@1 仅 `0.052713`、通过 `3/53` 个子任务，故提前终止并保留 partial checkpoint。可见 trace 中 14 次 delivery、2 次 train、1 次 flight CREATE 将计数传为字符串（例如 `product_cnts=["1"]`、`quantity="一"`），环境统一产生类型错误。另有一个咖啡子任务因未绑定 `dispatch_time`，CandidateDecision 把日期动作约束误作候选失败并连续展开 140 个详情；“猪瘾就犯了”“下雨就找个室内的”还被宽泛 `就...` 正则错误提升为精确实体 MUST。
- **为何旧门禁漏检**：此前测试只覆盖标量 `"1" -> 1` 和合成单层 schema，没有覆盖真实 `array[integer]` 的 item 类型；虚构 CandidateDecision 用例全部预先具备动作参数，没有测试“候选完整但日期/地址稍后由 proposal 填入”；验收也没有回放历史可见 tool calls/results 统计结构性不可执行子任务。因此 `281 passed` 只能证明局部不变量，不能作为进入 8 用户评测的充分条件。
- **无效/过早判断**：不能用所有 `no_admissible_binding` 事件的总占比直接证明绑定过严；其中包含只看到父实体、尚未 enrichment 的正常中间态。修复前先按子任务切段，确认真正异常的是候选已完整但仍无 binding 的终态。也不能把 CREATE 后支付确认计为缺陷，因为公开工具协议要求 PAY 独立授权。
- **有效方案**：ArgumentContract 保存公开 JSON schema 片段，ArgumentBindingResolver 对 array/object/nullable union 递归归一化并在最终 preflight 再验类型，支持阿拉伯与中文数量；CandidateDecision 在候选阶段允许尚未出现的动作参数，但最终 WRITE 对日期、地址、数量、ID、库存和父子关系继续硬校验；精确实体只接受 `就选/指定/要的是/就要` 或带结尾 `吧` 的裸 `就...吧`；每子任务 enrichment 详情读取上限为 12，阻止候选展开穷举。
- **新增发布门禁**：真实 Delivery/InStore/OTA public schema 遍历所有 CREATE/PAY/CANCEL/MODIFY 工具并递归检查参数类型；跨域测试覆盖三条 `SEARCH -> SELECT -> CREATE` 与真实 OTA `STATE_READ -> CANCEL`；`agent.counterfactual_gate` 只回放用户指令、公开 schema、tool call/result，不读取 reward/rubric/target。对 partial checkpoint 的 53 个子任务、41 个有候选的 commit 子任务和 48 个 WRITE 调用回放后，结构性不可绑定为 `0`，原始类型错误 `17`，归一化后 `0`。
- **静态验证**：完整 `agent/tests` 为 `289 passed`；compileall、Ruff `F/B023`、`git diff --check` 与 VitaBench `src/vita` 只读检查通过。第一次全量回归发现 schema-less 轻量测试桩被类型门禁误判，已改为只在存在公开 contract 时执行类型校验，并重跑全部测试，不跳过失败。
- **运行门禁状态**：1 用户 smoke 启动前，`localhost:8000/8002/59863` 三个模型端点连续 20 秒无响应；因此没有启动会混入服务超时的评测。端点恢复后先跑一个固定开发用户，只有无确定性类型错误、enrichment 不超 12、结构绑定不退化且对应用户 Avg@1 不低于历史，才允许第二用户和完整 8 用户。
- **能力抽象**：candidate-to-action execution / preference-to-candidate grounding / missing-information detection / evaluation harness / long-horizon consistency。

## E-049：偏好事实把抽取置信度、持久性与候选满足语义混为一层

- **日期**：2026-08-26
- **状态**：PARTIAL（确定性实现与合成门禁完成，尚未获得开发集指标证据）
- **通用性判定**：`GENERAL-INVARIANT`。只依赖用户可见 interaction、当前候选字段与公开 CREATE schema；测试使用 glyph/relic 等虚构实体，不包含用户、商品、商家、target、rubric 或 reward。
- **难点**：旧 `PreferenceFact.confidence` 同时表示抽取可靠性、重复证据和决策强度；实时用户说“这次不要 X”也容易被长期化。候选字段匹配又只返回全局事实，无法说明哪个候选、哪个字段使其相关。最后，CREATE 的 `note/attributes` 等请求字段可能在返回订单中回显，不能被误判成实际规格已满足。
- **有效方案**：新增 append-only `PreferenceEvidenceStore`，以 `(scope, facet, dimension, category, condition, persistence)` 聚合独立可观察证据，并将置信度、来源多样性和证据数投影回兼容的 FactStore 视图；仅 schema 标记为持久或包含明确未来默认语句的 runtime 用户话语写入长期记忆，其他回答只写当前 TaskSpec slot。SEARCH 后构造显式 `fact -> current candidate attribute` edge（字段来源、精确/部分匹配、grounding confidence），排序消费候选级 edge 分数且最多保留八个公平覆盖的正向事实。工具 schema 将备注/属性类参数标为“可传达请求”；框架可转发显式 AVOID，但仍保持候选内在规格、库存、ID、父子关系和 safety 校验，绝不把回显请求当 fulfillment 证据。
- **反例门禁**：相同可见事件即使被重放并赋予不同 event ID，也不会提升 confidence；不同条件下的同维偏好不会互相 supersede；当前一次性禁项不会进入长期 profile；未匹配候选不得取得 edge 分；带 `special_instructions` 的 CREATE 能收到请求文本，但内在包含禁项的候选仍被 WRITE validator 拒绝。
- **验证**：新增 `agent/tests/test_preference_evidence.py` 覆盖 runtime persistence、condition slot、独立证据 confidence、候选级 edge 与虚构 schema action capability；该文件及历史开放世界负向投影门禁已通过。完整测试和真实 smoke 尚待本轮修改收尾后执行。
- **适用边界**：公开 schema 只能证明“可以传达”而不能证明供应方会执行自由文本请求；没有暴露候选属性时，历史偏好仍不应决定当前候选类别。自然语言条件提取仍保守地使用显式 `condition=>choice` 表达，未声称解决隐含条件理解。
- **能力抽象**：preference extraction / preference drift / conditional preference / preference-to-candidate grounding / candidate-to-action execution / long-horizon consistency。

## E-050：支付确认后的延期回复触发无界状态查询循环

- **日期**：2026-08-27
- **状态**：VERIFIED（循环修复已由 paired smoke 复现；该用户 Avg@1 未提升）
- **通用性判定**：`GENERAL-INVARIANT`。失败由支付授权阶段、状态转移和工具白名单共同决定；合成测试使用虚构 workflow 与工具族，不包含用户、商品、商家或隐藏评测字段。
- **难点与证据**：可见 trace `data/traces/adapt_pref_evidence_smoke_U200109_v1.jsonl` 中，CREATE 后框架已询问支付，用户表达暂不处理，runtime 仍停在 `READY_TO_PAY`，随后同一 `get_delivery_order_status` 在该阶段连续调用 46 次。另有代码反例：普通阶段出现“买票”也会被全局 substring 当作支付授权；同一 assistant 批次的两个不可逆调用在 journal 提交前可分别通过 preflight。
- **根因**：支付回复只由少量全局关键词写入两个布尔值，没有“已发送支付问题”这一上下文边界，也没有授权、拒绝、延期、自己支付、询问和未知的互斥状态。`READY_TO_PAY` 的工具条件还通过布尔优先级无条件暴露 `STATE_READ`，而状态读取没有推动支付状态变化。
- **有效方案**：新增纯函数 `classify_payment_intent` 与结构化 `PaymentDisposition`；短确认只在支付问题发出后授权，拒绝/延期/自己支付安全进入 DONE 并保留未支付订单，询问或未知最多澄清一次后安全延期。支付问题与授权分别绑定当前 `payment_round`，只有 `READY_TO_PAY + 当轮问题 + 当轮授权` 同时成立才可执行 PAY；新订单、修订或阶段切换都使旧授权失效。`READY_TO_PAY` 未授权时不暴露任何工具，授权后只暴露 PAY；多个 PAY 依据可见 CREATE/state provenance 选择工具族。工具暴露和 preflight 共用同一 `can_execute_payment()` 门禁，且同批次最多允许一个 CREATE/PAY/CANCEL/MODIFY，终止消息明确区分未支付、自己支付和支付完成。
- **验证**：新增 `agent/tests/test_payment_state_machine.py`，覆盖上下文纯分类、确认授权、过期布尔授权拒绝、新支付轮次使旧授权失效、工具白名单与 preflight 双门禁、延期终止、自付/拒绝、交易词不提前授权、一次澄清、多 PAY 工具族选择、真实终止文案及双不可逆调用拒绝。加固后完整 `agent/tests` 为 `318 passed`，compileall、`git diff --check` 和 VitaBench `src/vita` 只读检查通过。相同 U200109/seed=42/max_steps=100 paired smoke 中，原复现语句“晚点再说”从 46 次 `get_delivery_order_status` 变为直接 `DEFERRED -> DONE`，全轨迹 tool proposal 从 99 降至 52；但新旧 Avg@1 均为 `0.133333`，因此只确认执行正确性和效率提升，不声称分数提升。轮次加固后又单独运行 `U200109/sub_U200109_6`：CREATE 进入待支付、框架询问、用户回复“不用了，我自己弄就行”后直接 DONE，无 PAY 和状态查询，12 条消息正常 `user_stop`；子任务 reward 仍为 0，不能归因于支付阶段。该轨迹还显示支付拒绝被误记为 `user_correction` lesson，属于独立的 harness 污染问题，待单独修复。当前 `trace_metrics.incomplete_payments` 仍把用户主动延期记为 1，属于诊断口径待修正，不代表 runtime 未终止。
- **适用边界**：本机制不自动支付，也不把订单 ID 从账本删除；创建后要求更换订单属于独立 revision/cancel 状态机，不由支付延期逻辑擅自取消或重建订单。
- **能力抽象**：candidate-to-action execution / long-horizon consistency / runtime safety。

## E-051：多个原始文本解释器使阶段正确的回复污染 harness

- **日期**：2026-08-27
- **状态**：VERIFIED（同用户、同子任务、同 seed paired trace replay 已通过；不声称 reward 提升）
- **通用性判定**：`GENERAL-INVARIANT`。根因是用户回复被状态机、当前纠正检测和 lesson harness 分别从原始文本重新解释；修复仅依赖 phase、当前问题合同和可见事件，不包含用户、商品或商家特例。
- **难点与证据**：`U200109/sub_U200109_6` 的旧可见 trace 中，用户对支付询问回复“不用了，我自己弄就行”，支付状态机正确终止，但通用 `不用` 关键词检测又写入 `user_correction` lesson。零模型历史 replay 计得 `harness_event_conflicts=1`。
- **有效方案**：建立单一纯函数 `classify_user_event`，根据当前 phase、支付问题轮次和待回答 slot 输出互斥的 `UserEventKind`。`TaskRuntime` 返回该事件，当前 TaskSpec 纠正、候选选择、记忆持久化和 lesson harness 只消费它；支付事件不得进入通用纠正路径。用户纠正 lesson 只能通过类型化 `CURRENT_CORRECTION` 入口写入，直接传入原始 `user_correction` 会被 harness 拒绝。硬执行内核仍只消费 ID/库存/父子关系/阶段/授权/OperationJournal；preference 和 user-correction lesson 仅作排序或后续提示，不生成硬 WRITE 授权。
- **可归因验证**：新增可见 `decision_surface` 和 `failure_attributed`事件，区分 `framework / model_policy / validator / environment / harness`；`agent.action_audit` 可零模型 replay 新旧 trace，统计类型化用户事件、不可执行决策面和 harness 事件冲突。新增合成测试覆盖同一否定语句在支付/补充信息/候选纠正阶段的不同语义、类型化 lesson 门禁及五类归因。
- **实测结果**：旧 v1 replay 为 `harness_event_conflicts=1`；修复后 v2 将用户回复唯一标记为 `payment_self_pay`，`observable_failure_events={}`、`harness_event_conflicts=0`、`blocked_action_surfaces=0`，无 PAY、无状态查询、无 lesson 污染。该子任务 reward 仍为 0，说明支付/harness 问题已与候选质量问题成功分离，不将整体零分误归因于本修复。
- **能力抽象**：missing-information detection / candidate-to-action execution / long-horizon consistency / observable self-evolution safety。

## E-052：候选排名无法区分请求宾语、后置限定与展平父实体文本

- **日期**：2026-08-27
- **状态**：PARTIAL（可见 trace shadow replay、两个 runtime smoke 和全量单测已通过；尚缺 8 用户 Avg@1 证据）
- **通用性判定**：`GENERAL-INVARIANT`。根因是中文请求的语法证据层级与 legacy `key=value` 候选解析的信息损失；修复依赖请求动词—宾语、后置限定、当前候选字段和公开拓扑，不包含用户、商品、商家或类别词表。
- **难点与可见证据**：首个 smoke 中“晚饭…想吃粉类”使“晚饭”中的单字“饭”与当前请求宾语“粉”同 band，历史卤肉饭因 intrinsic grounding 更高成为首选。加入宾语层后，未见实体 smoke 又暴露“一本小说，日文原著的”中限定语与中心名词分离，先选中文小说，再选名称含日文“小説家”但可见类别为随笔的候选。
- **根因**：`TaskRelevanceMatrix` 将整句所有 n-gram 视为同层当前需求；第一次修复又只强化中心宾语，未强化逗号后的后置限定。同时 `CandidateLedger` 的 legacy 字段解析只保留复合 `attributes=` 的第一个逗号片段，排名看不到后续语言、类别和 tags。
- **尝试过但无效的方案**：不应通过删除 band-0 候选来降低 Top-3 异类率；该做法曾将反事实比例从 `4.32%` 人为降低，但破坏完整 shortlist 和开放世界回退，被全量测试拦截后撤销。仅强化请求宾语也不足，因为强限定可以出现在后置分句。
- **有效方案**：新增 `CandidateAttributionRecord` 及三种显式分层 key；production 采用 `current-task-first + availability + intrinsic grounding + preference tie-break`，偏好不再先于库存和 intrinsic 证据。当前任务证据分为请求宾语/后置限定强层与全句上下文弱层。legacy raw 仅在删除关系 ID 和不同父实体 `*_name` 值后作为 intrinsic 补充，恢复语言/类别/tags，不改变 ID、库存、父子关系、日期、地址、支付和 `OperationJournal` 门禁。
- **反事实结果**：对旧 8 用户可见轨迹的 53 个已完成子任务零模型 replay 中，Top-3 band-0 从 `6/139 (4.32%)` 降为 `2/139 (1.44%)`，无可执行候选保持 `5/53`，历史偏好压过更高任务匹配为 `0`，request-only 与 intrinsic 混淆为 `0`。task-only 改变 `34/53` 个 Top-3，task-first preference tie-break 仅改变 `8/53`，因此选后者作为更保守的 production policy。旧 CREATE 与新 top-1 的 `11/41` 仅是版本漂移 proxy，不是 runtime CREATE 退化证据。
- **runtime 验证**：`U200109/sub_U200109_6` 从“卤肉饭首选、无 CREATE”恢复为三个粉类候选，用户选第一项后 `create_consistency=true`，订单成功进入待支付。未见实体 `U901652/sub_U901652_15` 经过两次可归因修复后最终选择可见 tags 同时满足“日文原版+小说”的《暗夜行路》并成功 CREATE。两个子任务 evaluator reward 都为 0，因此本记录只声称可见候选/执行闭环修复，不声称 Avg@1 提升。
- **验证门禁**：虚构 schema 覆盖请求宾语与上下文同字冲突、后置限定、复合 legacy attributes 及父实体名称防泄漏；完整 `agent/tests` 为 `334 passed`，compileall 与 VitaBench `src/vita` 只读检查通过。
- **后续风险/下一步**：当前语法抽取是保守规则，不声称覆盖所有隐含限定；先跑同版本 8 用户 Avg@1，再依候选归因报告判断是否扩展，禁止为已知书名或粉类添加词表。
- **能力抽象**：preference-to-candidate grounding / candidate-to-action execution / long-horizon consistency / evaluation harness。

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

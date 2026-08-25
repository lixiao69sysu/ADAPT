# ADAPT Avg@1 优化技术蓝图

日期：2026-08-26
目标：先最大化固定 8 用户开发集与完整 56 用户的 task Avg@1，再在冻结代码上最大化 Avg@4 与 Pass@4。
边界：只分析 ADAPT 可见指令、工具调用、工具结果、Agent debug trace 与聚合 reward；不读取 rubric、`target_product_ids` 或 target/distraction 标记，不修改 VitaBench。

## 1. 结论先行

当前最值得实现的不是更多 prompt 规则，也不是放宽 WRITE 校验，而是把 Agent 中分散的候选选择与动作阶段收敛为一个确定性 `CandidateDecision`，并让所有 ID 参数通过同一套 schema/topology binding 与 provenance 校验。

按预期收益、证据强度与回归风险排序：

1. **P0：候选到动作的确定性闭环**。统一决定 `SEARCH / ENRICH / SELECT / CREATE / PAY / DONE`，直接消除“已有可执行候选却继续询问或搜索”的路径。
2. **P0：通用 CandidateBinding 与 ID lineage**。用 CREATE schema、候选父子图和调用 epoch 绑定参数，替换 `room/ticket/seat -> product`、固定参数对等残留映射。
3. **P0：ToolOutcome 与完成契约**。结构化识别 CREATE/PAY 成功、待支付和失败；保留支付二次授权，禁止默认自动支付。
4. **P0：真正的 propose -> validate -> emit -> commit 事务**。拒绝的提案不得污染持久对话、询问额度、响应快照、搜索预算或 OperationJournal。
5. **P1：当前任务到候选的分层 grounding**。当前指令决定任务相关性；历史偏好只在任务相关候选内部排序，永不升级为 WRITE admissibility。
6. **P1：schema-driven 信息缺口与回答绑定**。按 CREATE 必需参数提问，替换固定 `taste/caffeine/room_type` 等运行时槽解析。
7. **P1：收紧自进化 harness 的硬触发源**。只允许真实工具错误、真实重复执行、用户纠正和明确未完成状态改变硬策略；框架自己的候选排名或拒绝不得生成硬 lesson。
8. **P2：指标与 @4 多样性层**。先稳定 Avg@1；最终只在完全同分的软候选上做 seed 驱动的稳定 tie-break，避免牺牲 Avg@4 换取表面 Pass@4。

## 2. 当前证据与口径

### 2.1 可公平比较的运行

| 版本 | max_steps | task Avg@1 | subtask Avg@1 | 成功子任务 | 工具错误 | 未完成支付 |
|---|---:|---:|---:|---:|---:|---:|
| `adapt_dev_tiered` | 100 | 0.200424 | 0.214286 | 24 / 112 | 4 | 7 |
| `adapt_dev_grounded_policy_v2` | 100 | 0.167628 | 0.178571 | 20 / 112 | 15 | 7 |
| `adapt_dev_schema_driven_v1` | 12 | 0.158848 | 0.169643 | 19 / 112 | 0 | 8 |

前两组使用同一 8 用户 dev cohort、同一模型、seed=42、单 trial、`max_steps=100`，可作为版本方向判断。`schema_driven_v1` 只有 12 步，只能作为旁证，不能用于公平归因。

`grounded_policy_v2` 比历史版本少 4 个成功子任务。若在不损失现有成功任务的前提下多恢复 4 个，subtask Avg@1 回到 `24/112 = 0.214286`；恢复 8 个则为 `28/112 = 0.25`。这只是规划情景，不是 task Avg@1 的分数预测。

### 2.2 失败事件必须按受影响子任务而非事件总数解释

公平版 112 个子任务中：

| 可观察路径 | 受影响子任务 | 成功 | 解释 |
|---|---:|---:|---|
| commit 搜索后没有 CREATE | 12 | 0 | 最直接的动作闭环机会 |
| 同一子任务触发 repeat-search lesson | 8 | 0 | 与其他路径重叠，不能直接相加 |
| recommendation 被重复 finalize | 6 | 0 | 当前 ResponseJournal 改造已静态覆盖，仍待跨用户 smoke |
| 真实工具错误 | 3 | 0 | 15 次错误集中在 3 个子任务，说明存在重复错误与实体绑定问题 |
| candidate-choice re-ask | 14 | 4 | 会浪费步骤，但不是所有 re-ask 都直接导致失败 |
| candidate validation | 26 | 5 | 与总体成功率接近，不能据此放宽硬校验 |
| CREATE 提案存在 | 75 | 18 | 动作更多不等于选择正确或执行正确 |
| 支付询问存在 | 76 | 18 | 多数是安全授权边界，不是自动支付的证据 |

`commit 搜索无 CREATE`、重复 recommendation、repeat-search 和真实工具错误的并集为 21 个子任务，成功数为 0。它是优先修复池，不是 21 个可保证新增成功。恢复其中 4 个且不引入回归，才足以回到历史 subtask 成功数。

## 3. P0：必须先实现的确定性层

### 3.1 新增 CandidateDecision，成为唯一候选决策入口

建议新增 `agent/runtime/candidate_decision.py`：

```python
@dataclass(frozen=True)
class CandidateBinding:
    create_tool: str
    arguments: dict[str, object]
    leaf_ids: tuple[str, ...]
    parent_ids: tuple[str, ...]
    hard_failures: tuple[str, ...]
    task_score: float
    preference_score: float
    provenance: tuple[str, ...]

@dataclass(frozen=True)
class CandidateDecision:
    next_phase: RuntimePhase
    admissible: tuple[CandidateBinding, ...]
    ordered: tuple[CandidateBinding, ...]
    selected: CandidateBinding | None
    missing_arguments: tuple[str, ...]
    needs_enrichment: tuple[ToolCall, ...]
    selection_basis: str
```

编译顺序必须固定：

1. 根据可见 CREATE tool schema 枚举可绑定的候选/父节点组合。
2. 只用硬证据过滤：当前明确指令、用户纠正、安全禁项、库存、日期、地址、授权、ID 来源、父子关系和 OperationJournal。
3. 用当前任务语义计算 `task_score`；不读取历史偏好。
4. 只在最高任务相关性候选带内计算 `preference_score`。
5. 用户明确选择时锁定展示快照中的候选；直接 commit 且没有关键参数缺口时由 Agent 选择，不再询问候选选择。
6. 产出唯一 `next_phase`，其他模块不得再次自行判断 `execution_ready` 或重算 shortlist。

需要替换的分散入口：

- `ToolRegistry.execution_ready()`
- `ToolRegistry.candidate_entity_types()` / `shortlist()`
- `ADAPTAgent._framework_enrichment()` 中的独立父节点排序
- `ADAPTAgent._framework_recommendation()` 中的 `best_evidence` 过滤
- `TaskRuntime.observe_candidates()` 中基于多个布尔量的阶段跃迁
- preflight 中再次独立判断 explicit selection

必须保留的硬边界：

- 软偏好只排序，永不产生硬 WRITE 拒绝。
- 用户明确选择始终覆盖软排序。
- 非当前 subtask/epoch 候选 ID 拒绝。
- 库存为 0、禁项、错误日期/地址、错误父子关系拒绝。
- direct commit 可以授权候选选择，但不能自动授权 PAY。

预期主要作用：覆盖 12 个 `search -> no CREATE` 子任务，并使 39 次 direct-execution re-ask 不再依赖模型重规划。

### 3.2 用 CandidateBindingGraph 替换固定实体映射

当前 `validate_write()` 仍包含：

- `room/ticket/seat -> product` 兼容表；
- `room_id -> hotel_id`、`ticket_id -> attraction_id`、`product_id -> shop_id` 固定关系；
- product/store 专用父子检查。

这些是 VitaBench 兼容逻辑，不应继续存在于通用决策核心。建议：

1. `ToolContractCompiler` 从参数 schema 生成 ID 变量、必需参数、角色和候选类型要求。
2. `CandidateBindingGraph` 从 `Candidate.parent_ids` 生成有向图。
3. 每个 CREATE 参数绑定到一个观测节点；若同时选择父和子，必须存在已观测边。
4. 无 schema 注解时，只允许“唯一未解析参数类型 -> 唯一观测叶类型”的保守退化。
5. 旧 VitaBench 名称/ID 退化逻辑集中到 `legacy_vitabench_adapter.py`，只能产生结构注解，不能影响排名或策略。

这会让虚构 `quasar/node/mark` schema 与真实工具走同一算法，同时保留 VitaBench 所需的兼容性。

### 3.3 所有 ID-bearing 工具都进行 provenance 校验

当前 WRITE 会检查 ID，但 ENRICH 主要只检查重复读取，STATE_READ 尚无完整来源校验。建议新增 `CallLineageLedger`：

```text
call_id
instruction_epoch
tool_epoch
operation_epoch
tool_role
input_ids
output_candidate_ids
output_state_ids
success/error
```

校验规则：

- SEARCH/READ 可产生候选 ID；ENRICH 只能消费当前 epoch 已观测父 ID。
- STATE_READ 只能消费用户 profile 中允许的 user ID 或当前 workflow state ID。
- CREATE 只能消费当前 CandidateBinding 中的候选/父 ID。
- PAY/CANCEL/MODIFY 只能消费当前 operation epoch 的 workflow ID。
- 错误结果不得进入 CandidateLedger；错误中明确返回的纠正 ID只能进入单独 `CorrectionEvidence`，绑定原失败参数并最多重试一次。
- tool result 的 call ID、tool name 或 epoch 不匹配时不得改变候选、状态机或 OperationJournal。

预期主要作用：解决 15 次工具错误集中在 3 个零分子任务的问题，同时降低 @4 多 trial 中的随机参数错误。

### 3.4 建立结构化 ToolOutcome 和完成状态机

当前阶段迁移仍依赖结果文本中的 `unpaid`、`successful`、`成功`。应编译为：

```python
@dataclass(frozen=True)
class ToolOutcome:
    ok: bool
    effect: Literal[
        "observed", "created", "created_pending_payment",
        "paid", "cancelled", "modified", "no_change"
    ]
    workflow_ids: tuple[str, ...]
    correction: CorrectionEvidence | None
```

解释优先级：结构化 result field / result schema > tool role/state effect > 隔离的 legacy 文本解析。

确定性转移：

| 当前动作 | ToolOutcome | 下一阶段 |
|---|---|---|
| CREATE | created | DONE |
| CREATE | created_pending_payment | READY_TO_PAY |
| CREATE | error + 可验证纠正 | READY_TO_CREATE，最多一次纠正重试 |
| PAY | paid | DONE |
| PAY | error | READY_TO_PAY，不得重复相同签名 |
| 任意不可逆动作 | result call/epoch 不匹配 | 保持原阶段并记录 lineage error |

支付规则不变：订单待支付后只询问一次；实际发送问题后才登记；用户授权才暴露 PAY；用户拒绝则 DONE。不得为提高动作率默认自动支付。

OperationJournal 应扩展到 CREATE/PAY/CANCEL/MODIFY 的 at-most-once success，而不只保护 CREATE。

### 3.5 完成真正无副作用的 ActionTransaction

建议统一所有模型和框架输出：

```text
propose -> prepare(copy) -> validate(pure) -> emit -> commit
```

具体修改：

- `_normalize_search_call()` 与 `_normalize_profile_arguments()` 改为返回新 ToolCall，不修改原提案。
- `_preflight()` 只返回 `PreparedProposal + ValidationIssue[]`，不改 budget、phase、journal 或参数对象。
- 被拒绝的 assistant/tool proposal 不追加到持久 conversation；只作为当前 replan 的临时 `ReplanContext`。
- `_framework_question()` 只 propose；实际 assistant message 进入 state 后再同时 commit `TaskRuntime` 与 `ADAPTMemory`。
- `_framework_payment_question()` 不提前设置 `payment_question_sent`。
- `_framework_recommendation()` 不提前写 ResponseJournal；实际发送后保存有序快照。
- 搜索、enrichment、OperationJournal 和 ToolErrorLedger 只在真实 tool call 被发出后登记。

93 次 preflight rejection 说明这是高频路径。即使拒绝本身正确，把未发给环境的提案长期写入上下文也会造成模型锚定和长序列污染。

## 4. P1：恢复候选质量的开放世界语义层

### 4.1 取消最长公共子串形成硬 task identity

当前候选 alignment 会从当前指令与候选属性的最长公共子串生成 `task_identity_atom`，随后在 ranker 中硬过滤不匹配候选。它容易把偶然的短重合当成候选类别，并与“软偏好不形成硬限制”的原则不一致。

改为三层分数，按字典序而非线性混合：

```text
hard_admissible
  -> current_task_relevance_band
    -> current-session correction score
      -> historical preference score
        -> availability / stable tie-break
```

`current_task_relevance` 只使用当前指令与 live candidate attributes，并先屏蔽动作词、日期、地址、数量、profile alias 和已解析 workflow 参数。历史偏好不能参与候选类别/实体族判断。

只有两类语义可以升级为硬约束：

1. 用户明确选择了已展示候选或精确候选名；
2. 当前指令值精确匹配 schema 声明的 hard constraint field。

其余 task relevance 只排序；没有可靠 grounding 时保留多个候选族给模型决策，历史偏好分数在跨族比较中置零。

### 4.2 recommendation 不再只展示最大 preference coverage 集合

当前 recommendation 先取 shortlist，再只保留 `best_evidence` 候选。这虽然不再限制 WRITE，却会让展示面被单一偏好分数硬裁剪，损害推荐任务成功率和 @4 多样性。

建议直接展示 CandidateDecision 的有序 top-3：

- 首项为当前 task relevance 与偏好综合最优；
- 其余项来自同一任务相关性带；
- 显示可解释的当前需求/偏好证据，但不宣称未观测属性；
- ResponseJournal 保存实际展示 ID 与名称；
- 后续支持 ordinal、精确展示名称和无歧义指代，均绑定该快照。

### 4.3 schema-driven 信息缺口和回答绑定

当前 `TaskRuntime._record_slot_answer()` 与 `_promote_current_answer()` 仍包含固定的 size/caffeine/taste/room-type 逻辑。建议改为：

```python
@dataclass(frozen=True)
class PendingQuestion:
    question_id: str
    tool_family: str
    argument_name: str
    expected_schema: dict
    persist_as_preference: bool
```

- 缺口来自 CREATE required arguments。
- user/profile 可解、candidate ID 可搜索、结果字段可绑定、schema 有默认值的参数不问。
- 每个子任务最多问两个不同 argument。
- 回答绑定原 `question_id + argument_name`，不靠 facet 词表猜槽。
- operational 参数（日期、地址、数量、支付授权）默认不写成持久偏好。
- 只有回答明确表达稳定选择，才转成 PreferenceFact。

这项排在 P1，因为公平 trace 只有 13 次真实问题提交；当前更大损失来自动作阶段和候选绑定。

## 5. P1：自进化 harness 应保留什么、删除什么

### 5.1 可触发硬策略的闭集

允许：

- `tool_error`：真实环境返回的错误；策略仅针对同工具族、参数角色和实体拓扑。
- `repeat_search`：相同规范化签名已真实执行，且已有可用观察；不能基于 proposed call。
- `missed_write`：用户已授权，CandidateDecision 至少有一个 hard-admissible binding，但子任务结束前没有真实 CREATE。
- `unresolved_operation`：CREATE/PAY/CANCEL/MODIFY 的明确状态未闭合。
- `user_correction`：只提高当前会话纠正优先级，不自动生成跨任务候选规则。

禁止：

- `preference_undercoverage`；
- “模型没有选择框架排名第一”；
- evaluator reward/rubric；
- user/product/merchant/task ID；
- 单次 preflight 的自我判断直接生成硬策略；
- 从失败 candidate 名称推导品类词表。

建议把 `candidate_choice_reask` 与 `mixed_question_tool` 从硬 policy templates 删除；它们应由固定 ActionTransaction 和 CandidateDecision 直接保证，而不需要先犯错再学习。

### 5.2 lesson 的必要字段

```json
{
  "capability_target": "candidate_to_action_execution",
  "failure_class": "missed_write",
  "evidence_source": "actual_trajectory_state",
  "tool_family": "schema-derived-family",
  "entity_signature": "parent>leaf",
  "effect": "force_ready_to_create",
  "hardness": "hard",
  "evidence_count": 2,
  "active_from_subtask": 8,
  "forbidden_specificity": [
    "user_id", "product_id", "merchant_id", "task_id",
    "target marker", "rubric text"
  ]
}
```

核心正确性不应依赖 harness。harness 只对同一用户后续相关结构做有界调节；切换用户清空。

## 6. P2：trace、实验与 @4 优化

### 6.1 trace 指标升级

`trace_metrics.py` 除事件总数外，应增加：

- 每个 failure class 的 `affected_subtasks`；
- failure cluster 的并集和重叠矩阵；
- `commit_search_no_create`；
- `search -> admissible -> create -> pending/pay/done` 转化；
- rejected proposal 是否进入持久 state；
- question/recommendation/payment 的 proposed/sent/committed 差异；
- unknown/stale/wrong-parent ID 按 tool role 计数；
- CandidateDecision 的 admissible 数、task grounding margin 与 selection basis；
- task Avg@1 为主指标，subtask success 仅作诊断。

每次结果保存：git commit/dirty fingerprint、Agent prompt hash、feature switches、模型配置、`max_steps`、seed、trial 和 trace schema version。当前结果没有 stock dev baseline，也没有 llm_args fingerprint；在 blind 前必须补齐一次缓存 baseline。

### 6.2 实施和验证顺序

#### Milestone A：P0.1 + P0.2

实现 CandidateDecision、CandidateBindingGraph 和 ID lineage；保留现有 hard invariants。

单测门禁：

- 虚构父/子/CREATE schema 可完整绑定；字段名与实体名全部未见。
- direct commit 有 admissible binding 时不再问候选选择。
- explicit ordinal/name 只绑定实际展示快照。
- unknown/stale/wrong-parent ID 在 ENRICH、STATE_READ、CREATE、PAY 均被拒绝。
- 软偏好最高分候选不是 WRITE 硬锁。

#### Milestone B：P0.3 + P0.4

实现 ToolOutcome、ActionTransaction、OperationJournal 扩展和纯 preflight。

单测门禁：

- 连续 preview 不改变任何运行状态。
- rejected proposal 不进入持久上下文。
- question/recommendation/payment 只有实际发送才各记一次。
- CREATE/PAY 成功后不能重复；失败且参数不变也不能重复。
- 待支付保留独立授权，拒绝支付安全结束。

#### Milestone C：P1 semantic grounding

移除最长公共子串硬过滤与 recommendation 最大覆盖裁剪；接入任务相关性带和 schema-driven gap。

单测门禁：

- 当前任务语义优先于冲突历史偏好。
- 无可靠 grounding 时历史偏好不能决定跨族候选。
- 在同一任务相关候选族内，历史偏好可以改变顺序。
- 当前指令的软语义不得成为 hard WRITE failure。
- 无词表的虚构 schema 可通过 task/candidate overlap 排序。

### 6.3 运行门槛

1. 每个 milestone 先跑完整单测、compileall、VitaBench 只读检查。
2. 用同一 `max_steps=100`、模型、seed 跑 1–2 个固定 dev 用户 smoke；只能观察能力簇，不为具体实体改代码。
3. P0 完成后跑完整 8-user dev：
   - 第一门槛：task Avg@1 不低于历史 `0.200424`；
   - 诊断门槛：成功子任务至少恢复到 `24/112`；
   - `recommendation_finalized > 1` 的子任务为 0；
   - `commit_search_no_create` 至少从 12 降至 6；
   - 真实工具错误影响子任务从 3 降至不超过 1；
   - direct candidate-choice re-ask 受影响子任务显著低于 14。
4. 达到恢复门槛后，缓存同配置 stock dev baseline；在没有它之前不能声称“相对 stock +0.03”。
5. 只有 task Avg@1 达到 stock baseline +0.03 且无明显结构性回归，才冻结代码进入 blind。
6. blind 只看聚合；通过后跑完整 56 用户 Avg@1。
7. 完整 Avg@1 通过后才跑最终四 trial 的 Avg@4/Pass@4。

### 6.4 @4 的后期策略

最终四 trial 中保持完全确定的部分：授权、ID provenance、库存、禁项、日期、地址、父子关系、OperationJournal 和阶段完成契约。

只允许在以下条件同时成立时产生 trial 多样性：

- 候选全部 hard-admissible；
- current task relevance 同带；
- preference score 相同或差异低于预注册 margin；
- 没有用户明确选择。

此时可用 trial seed 做稳定 tie-break。不得通过放宽硬约束、提高错误工具调用率或随机支付来追求 Pass@4。

## 7. 暂不实现

以下工作当前没有足够 Avg@1 证据，先推迟：

- 大规模 prompt 关键词/品类词表；
- 为具体用户、商品、商家、酒店或 ID 编写规则；
- 默认自动支付；
- evaluator reward 驱动的在线 policy；
- 全量代码自修改 harness；
- 在 Avg@1 未恢复前调 temperature、thinking 或做大规模模型参数矩阵；
- 完整通用异步/并发 workflow engine；VitaBench trace 尚未证明其为主损失源。

## 8. 置信度与限制

结论置信度为 **中等，适合进入实现但不能承诺分数**：

- 高置信度：版本配置、Avg@1、成功子任务数、受影响子任务数和零成功路径均可从现有产物复算。
- 中等置信度：CandidateDecision、binding 与事务边界会减少可观察错误；这些是由代码路径和 trace 共同支持的通用机制。
- 低置信度：任何具体 task Avg@1 预测。失败簇重叠，修复控制流不保证候选满足 evaluator，外部模型也可能有运行波动。

因此“确保提升”的正确工程含义不是预先保证一个分数，而是：每个改动都有零特例的结构性复现、成对反例、明确可观察中间指标、同配置 smoke 和 8-user score gate；不通过就回退该 milestone，不把无分数证据的架构变化继续带入 blind。

## 9. 2026-08-26 实施状态

本蓝图的七项 runtime 重构已按整体迁移法落成五个顺序 commit：

1. schema contracts、CandidateBindingGraph 与 CallLineageLedger shadow；
2. CandidateDecision 成为 shortlist、admissibility、enrichment 与 phase 的统一权威；
3. ActionTransaction、ToolOutcome 与不可逆 OperationJournal 提交边界；
4. 当前任务优先的 lexicographic grounding 与 schema question/answer binding；
5. 只接受可审计真实事件的同用户受限 runtime harness。

实现中保留 ID provenance、库存、父子关系、日期、地址、授权、支付二次确认和 OperationJournal 等硬不变量；软偏好只排序。未标注的 required action 参数不自动制造用户问题，候选是否可落地与最终 tool call 是否参数完整分层处理，后者仍由 preflight 严格拒绝。

当前静态证据为：structural metamorphic、transaction invariant、counterfactual semantic 三类通用测试均已接入；完整 `agent/tests` 为 `267 passed`，`python -m compileall -q agent`、VitaBench `src/vita` 只读检查和 `git diff --check` 通过。该状态只证明迁移边界和通用反例成立，尚未完成 1–2 用户 smoke，不构成 Avg@1 提升声明。

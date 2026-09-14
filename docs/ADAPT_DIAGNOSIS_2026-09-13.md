# ADAPT 提分瓶颈与取舍建议（2026-09-13）

> **2026-09-13 之后的变更（E-086）**：本报告是当时工作区的时点快照。
> 其中提到的 EvidenceAgent、CandidateMarkingAgent 以及 `agent/candidate_marking.py`、
> `agent/marking_agent.py` 已被删除；agent 侧收敛为唯一的纯观察者 `AdaptAgent`
> （`agent/adapt_agent.py`）。下列分析保留原样作为历史记录，不再代表现状。

## 当前工作区复核补充（11:49）

用户随后授权自研 agent，不限官方插件，目标固定为 stock 的同开发集用户 Avg@4 >= 0.35。
目前 `--agent adapt --memory-type rewrite` 是新增 EvidenceAgent + 原始 RewriteMemory，
并不调用 ADAPTMemory 的主动提问、漂移或候选排序。下文关于摘要输入丢字段的缺陷已修复，
词面标注也已改成中性提示；其分数收益均未验证。

P2 冒烟进程已中断，仅有日志，前两项评分为 0、1，第三项停在评分窗口，没有整用户
checkpoint。此前把短时间无日志判作“卡死”的推断缺乏充分证据，不能作为服务故障结论。

本次新复现（只用虚构输入、无模型调用）：

| 输入 / 路径 | 当前输出 | 问题 |
| --- | --- | --- |
| 用户“我对花生过敏”→ ADAPTMemory.read() | `PREFER: 花生` | 结构化事实本身为 negative/safety，但整体读取丢失极性；read_preference_memory 工具使用此路径 |
| DecisionCard 含三条忌口、四条 MUST | 只输出前两条忌口、前三条 MUST | 硬条件被固定槽位预算静默丢弃 |
| propose_question("帮我推荐一本书", "", None) | 询问清淡、麻辣、烧烤等口味 | 默认 delivery 加关键词规则误判领域 |
| propose_question("帮我预约理发", "", "instore") | 询问人数和包间 | “预约”被当成聚餐 |
| 买出行票，记忆“我不喜欢高铁，我喜欢飞机” | “您之前偏好高铁出行，这次也一样吗？” | 词面扫描忽略否定 |
| 主动问题“这次仍然不加糖吗？”，回答“是的” | fact(value="是的", dimension="explicit") | 保存了证据原话但没有解析为可复用槽值；问题存在 raw 中不等于下游已能使用 |

优化顺序：统一记忆读取的极性与作用域；保障硬约束完整可见；将主动提问从固定场景问句
改成基于当前候选差异和最新用户输入的提案；再测量 EvidenceAgent 的额外计算是否值得。
新 reviewer 每条用户输入或 >=1200 字工具结果触发，最多八次；没有确定性的结构化关系校验、
也没有持久候选账本，因此暂不能把它描述为已完成候选绑定能力。

删减建议（本次没有删除运行代码）：无生产调用的 `_execution_hint`、`_summary_fallback` 与
`memory/reflexion.py` 优先移出主线；CandidateMarkingAgent 作为替代策略应冻结或归档；
旧 CandidateLedger 的强制排序/门禁功能先从 TaskSpec/DecisionCard 的共享实现拆开，再决定
是否保留。`runtime/ranking.py`、`runtime/location.py` 与 ledger 的引用仍存在，不能机械整包删除。
tool_recovery 保留为可选恢复库或归档，不能因为有测试就算已上线。保留证据存储、极性/作用域、
官方评测隔离和逐项评分审计能力。

本报告基于当前工作区代码、已保存开发集产物和零模型复算；没有新跑 agent/user/evaluator，没有修改运行代码或 VitaBench。结论区分代码可复现缺陷、历史观察和待验证假设。仓库存在大量未提交改动，不能把当前实现等同于历史产物对应版本。

## 核心判断

ADAPT 的历史问题是：为增强个性化而增加的处理链，先损失了原始 agent 已拥有的信息与行动自由，再用控制规则弥补损失。部分迭代是在恢复基线能力，不能视为超过基线的创新收益。

当前删掉旧控制器的方向合理，但不能据此认定 ADAPTMemory 已优于 RewriteMemory。下一步应以 stock + RewriteMemory 为保底，优先验证“用户偏好如何可靠绑定到可购买候选及其规格”；新记忆后端需要单独证明价值，不应与候选机制绑在一起接受评测。

## 1. 先校准正在比较的是什么

本次直接调用官方 `_compute_subtask_pass_metrics` 复算 `stock_dev.json`：

| 指标 | 结果 |
| --- | ---: |
| 用户 / simulation / 子任务单元 / 子任务运行 | 8 / 32 / 100 / 400 |
| 官方 Avg@4 | 0.2925 |
| Pass@4 | 0.4000 |
| Pass^4 | 0.2000 |
| 等用户权重四次平均 | 0.2940 |
| personalize 子任务运行平均 | 0.3198，344 次 |
| proactive 子任务运行平均 | 0.1250，56 次 |

官方 Avg 对 `(user, subtask)` 的 trial 均值等权；用户的子任务数量不等，故它与等用户权重平均不同。100 单元中，60 个四次全错、20 个四次全对、20 个结果混合。四次全错是优先分析对象，但不代表数学上永远不可能做对。

该基线记录模型别名为 qwen38-agent / qwen35-user / qwen36-evaluator、max_steps=100。它是开发集用户开发集成绩，不能冒充 56 用户正式成绩；仓库里没有足以核验另一份更高全量成绩的对应产物。

用户当前打开的 `adapt_5tasks_v16a.log` 是 8 月 18 日旧实验，记录 qwen35-agent、1 trial，末尾用户级 Average Reward=0.2138；旧 `rewrite_5tasks.log` 为 0.2102。两者打印的 personalize 为 0.2459/0.2131，proactive 为 0.0625/0.1250。这个观察提示“总平均略升可能同时掩盖主动询问退化”，但不构成同版本、同口径下的因果结论，也不能直接与九月 0.2925 比。

### 小样本隔离产物复算

| 产物 | 范围 | 已评分子任务运行平均 |
| --- | --- | ---: |
| R2：stock + ADAPT 条目记忆 | 2 用户 × 1 trial，27 次 | 0.1852 |
| R5a：stock + ADAPT + 摘要 | 同 2 用户 × 1 trial，27 次 | 0.2963 |
| R5b：旧控制器 + 摘要，按历史实验说明 | 同 2 用户 × 1 trial，27 次 | 0.1111 |
| R8：旧控制器后续修复组合 | 同 2 用户 × 1 trial，27 次 | 0.2222 |
| stock 缓存，同 2 用户 trial 0 | 27 次 | 0.2222 |
| stock 缓存，同 2 用户全部 4 trials | 108 次 | 0.2870 |
| R5a 原产物 + ext | 6 用户 × 1 trial，74 次 | 0.3243 |
| stock 缓存，同 6 用户 trial 0 | 74 次 | 0.2973 |
| stock 缓存，同 6 用户全部 4 trials | 296 次 | 0.3176 |

这些均为描述性复算，trial 数不同的行不可直接据以宣称优势。工程日志隔离表将 R1 baseline 写为 0.2963；它与当前 stock 缓存这两个用户的 trial 0 或四次均值都对不上，引用前必须找回其独立来源。R5b/R8 的历史顶层 metadata 写着 memory_type=rewrite，而实验说明说的是旧 ADAPT 内部记忆；这可能是旧 runner 参数未代表实际分派，但至少说明不能仅凭文件名或顶层字段还原有效配置。

最稳妥的判断是：有界摘要是值得保留的召回路径；现有样本尚未证明 ADAPTMemory 超过 RewriteMemory，更没有证明固定 +0.11 的可泛化收益。

## 2. 为什么原始 Agentic Memory 能做得更好

stock 的核心优势有代码依据：

1. `RewriteMemory._format_interactions()` 将交互中的结构化内容序列化给模型，保留订单、评价、对话中的多数语义字段；摘要由 LLM 归纳，支持把具体历史转成可复用偏好。
2. stock `PersonalizationAgent` 将账户信息和记忆放入 prompt，单步由 LLM 使用完整工具集与当前对话历史决定下一步。
3. 它不需要先服从一个基于不完整字段编译出来的阶段机，能够在搜索、澄清、修改选择和执行之间往返。

这不等于 stock 没问题，也不证明所有控制都无效。它说明自研层必须提供互补能力，否则多一层解析、多一层裁剪、多一次强制决策就多一个出错面。

旧 ADAPT 的工程记录已出现过授权被当成选型完成、搜索阶段隐藏 CREATE、等待问题时既不能提问也不能用工具、日期派生约束无限否决、框架重复发送定稿等机制。它们是可解释的失败原因。R5a/R5b 的巨大描述性落差与这些机制一致，但小样本不支持精确分摊每个模块的贡献。

L3 有注入点并不意味着任何注入都必然抢夺模型决策。增加可追溯证据、报告确定工具错误与重写模型选择是不同干预。无需从“旧控制器失败”推导“以后永远不能有控制”。

## 3. 当前实现中已经能证明的问题

### 3.1 摘要之前就丢信息

`agent/memory/adapt_memory.py::_format_interactions` 对订单主要保留 merchant_name、product_name、remark/note。商品 sugar 等属性不会进入这条摘要路径；评论主要保留 target_name，评论正文未被完整保留；对话每条截 80 字，整个新交互批次截前 2500 字。

本次构造无数据集实体的最小输入：

```json
{"merchant_name":"Shop","items":[{"product_name":"Tea","sugar":"NO_SUGAR"}]}
```

ADAPT 输出 `[日期] 点单 Shop: Tea`；RewriteMemory 输出中仍有 `"sugar":"NO_SUGAR"`。另构造 2700 字订单备注，后接最新用户纠正 `NEW_CORRECTION_NO_SUGAR`，ADAPT 的 2500 字格式化结果中完全没有该纠正。

这是摘要输入的信息损失，不是猜测。它不证明其他结构化分支必然也丢掉相同事实，但证明“加了 LLM 摘要就已恢复 RewriteMemory 的语义覆盖”不成立。

其他压缩点：结构化 LLM 抽取只看前 800 字对话、输出预算 256 tokens；摘要默认 800 字且最终再硬截断；两个 LLM 分支异常后返回空结果而没有充分可观测信息。对于长序列，这些点可能造成偏好更新失败和错误自我强化，实际影响仍需测量。

应把持久存储与 prompt 视图分开：保留可回溯原始证据，任务相关视图有界；新增明确纠正、否定和规格优先保留；摘要压缩不能覆盖唯一证据。不要仅盲目加大上下文预算。

### 3.2 新候选标注把词面出现当成满足

`agent/candidate_marking.py::matching_values` 的条件是 `value in user_text`，输出前缀却是“符合你提到的”。本次实际执行：

```text
用户：不要花生，我对花生过敏
候选：StoreProduct(id=P1, name=花生, attributes=ingredient:花生)
输出：〔符合你提到的：花生〕StoreProduct(...)
```

这会把负向约束变成正向暗示。相同问题还包括他人偏好、已撤销偏好和条件作用域。字符都来自原文，并不保证“符合”这个关系判断正确。

`CandidateMarkingAgent._user_text` 仅在子任务开始时取 instruction + memory 快照，未把后续用户纠正并入标注条件。`_SKIP_FIELDS` 又跳过 price/date/score，当前方案既不能覆盖数值比较，也不能处理日期关系。它是词面提示实验，不能称为可靠约束绑定。

还需注意：该实现原地修改 inbound `ToolMessage.content`；应核查是否因此污染保存给 evaluator 的原始工具证据。更稳妥的做法是保留原始观察，将派生标注作为 agent 可见辅助信息独立记录。当前计数在每个子任务重置，runner 最后只保存一次 marking_events，也不足以做完整的逐子任务归因。

候选标注方向可以继续，但当前实现不宜直接升为默认。最小契约应该是：条件有来源、正负极性、作用域和有效时间；候选属性有来源及父子 ID；关系分为满足/冲突/未知，未知不能当冲突也不能当满足。

### 3.3 拥有模块不代表运行时拥有能力

当前默认是 stock，候选标注和 thrash guard 是可选分支。runner 用 if/elif 选择二者，同时开两个标志不会叠加两个能力。

`apply_candidate_grounding`、工具参数恢复等辅助能力在默认生产链没有对应接线。主动问题可以通过 read 或工具暴露，但 `commit_question` 没有来自当前 stock 对话循环的自动调用，提问预算与答案持久化不能算完整闭环。`record_preference_answer` 仍可能由模型显式调用，不等于自动记录所有回复。

vendored orchestrator 在子任务间注入 `subtask.interactions`；没有看到它自动将刚生成的整段对话写回 RewriteMemory。因此工程日志中“前面 rollout 改变必然通过记忆传到后面”的说法，也不能不看实际写回路径就采用。跳过前序子任务会跳过它们的历史交互注入，这一点仍成立。

## 4. 更值得投入的损失面

对 `stock_dev.json` 跑现有 target_reachability：400 次子任务运行中 396 次有目标标记，以下是运行次数，不是 396 个独立样本。

| 观察 | 次数 / 通过率 |
| --- | --- |
| 目标商品确实出现在工具结果 | 235 / 42.55% |
| 目标商品未出现 | 161 / 10.56% |
| 父商家/酒店已出现但目标商品未展开 | 129 次 |
| 其中 delivery / instore / ota | 47 / 48 / 34 次 |
| delivery 总通过率 | 96/228 = 42.11% |
| instore 总通过率 | 13/104 = 12.50% |
| ota 总通过率 | 8/64 = 12.50% |

这支持优先研究两个相邻问题：选哪个父候选继续展开；展开后，如何把偏好与商品、规格及执行参数绑定起来。不能把“目标在工具结果里”当作“模型已理解”，也不能把所有未绑定写操作的运行都解释成应该强迫下单——推荐型任务未必要求写入。

这些是相关性诊断：难任务可能同时更难召回且更难通过；商品是否出现也可能是前面决策的结果。目标标记不是完整 rubric，有 17 次目标商品未打印仍通过。它们用于选择值得验证的机制，不能给出“修好绑定就能增加多少分”的因果承诺，更不得进入运行时输入。

名字叫 `stock_dev_rubric_detail.json` 的文件实际只有 2 个 simulation、27 个子任务记录，其中 26 个可用、20 个失败；失败中有 10 次只差一个条件。这是很好的小范围诊断入口，但绝不是全开发集用户 400 次的瓶颈分布。字段条件命中均值 0.6285 也不是正式 Avg。

重复调用守卫适合作为效率优化：本基线 13/400 次子任务运行有已识别抖动，覆盖 7/100 个子任务。假设只救回这 13 次且不影响其他运行，直接改善上限为 +0.0325；无法单独填补 0.2925→0.35 的 +0.0575。全局收益可能有间接变化，不能把这个直接记账上限当作一切实现的绝对上限。当前 ThrashGuard 实际是一次提示，不是硬性禁止；相同返回三次也不能一般性证明下一次动态查询永远无新信息。

## 5. 应该做与不应该做

| 优先级 | 应该做 | 不应该做 |
| --- | --- | --- |
| P0 | 用实际有效配置与统一指标重建实验账本 | 用旧文件名、模型别名和文档自述代替运行证据 |
| P1 | 保留 stock + RewriteMemory 保底；补全 ADAPT 摘要输入证据、观测截断和失败 | 先压成少数规则字段，再期待模型恢复被删语义 |
| P1 | 候选与用户条件绑定：来源、极性、当前纠正、父子关系、满足/冲突/未知 | 词面命中就说符合；选中一个“偏好领先者”就强制创建 |
| P2 | 在 instore/OTA 检查父候选选择、详情展开和规格落地 | 照搬外卖品牌规则到酒店和票务；无差别增加搜索次数 |
| P2 | 问会改变当前选择的缺失条件，收到回答后正确更新本轮证据 | 见模糊就问，或见委托就禁止所有后续澄清 |
| P3 | 确定错误提示、重复结果提醒作为有界可选机制 | 让守卫替模型选商品、催成交或制造终局拒绝 |

建议的运行时形态：stock 循环 + 可回溯记忆 + 小型候选证据视图。模型仍负责搜索策略、开放语义判断、澄清与执行。代码负责保真与确定的关系计算；无法从观察证明的判断明确为未知。

候选视图可以列“用户当前要求无糖；候选甜度字段缺失→未知”“用户明确排除花生；候选配料含花生→冲突”。不要在比较层直接宣布“用户肯定会喜欢”。对于复合条件允许模型推理，但保留其依据，不把推断升级为用户硬约束。

## 6. 如何用有限预算决定保留什么

先只选一个假设：在保留 stock + RewriteMemory 的前提下，正确的候选证据视图能否提升最终选择与参数满足率。先用虚构候选验证正负约束、当前纠正、不同主体、数值范围和父子绑定；再跑小范围真实工具投递 smoke，证明确实触发。暂不同时替换 memory。

通过后在相同开发用户、相同 trial 数和完整用户历史上比较 A/B。主要报告官方 Avg、逐用户差值、修复和破坏分布；补充按域、按技能、条件机会归一化的诊断，以及成本与工具次数。新摘要输入修复作为下一次独立假设检验，避免一轮同时改记忆、标注、控制器和预算。

达到 0.35：400 次二值运行从 117 次通过到 140 次，需要净增加 23 次通过。不要只累计“修好了几个 bad case”，必须扣除新损失。开发集证据足够后按项目既定 blind 门禁验证，代码冻结再全量评测，不查看 blind/final 逐案例来调规则。

### 现有测量纪律中需要纠正的推论

* 同 seed 的文本不完全复现，不能当作相同随机轨迹。它**不否定**按相同用户/任务作配对区组比较；应在每臂聚合多 trial，再比较同用户差值，并处理用户内相关性。
* 文本不同也不自动证明两个模型分别都非确定：如果 agent 的上游输出不同，user 即使对固定输入完全确定，也可能产生不同回答。现有三次实验足以说明端到端非复现，单组件归因要固定其输入。
* `noise_floor.py` 的 0.0582 来自单臂用户均值的约 2SE，不是双臂差值、不是官方加权 Avg 的精确区间，也不是任何改动的普适检测下限。因此“不足 +0.06 的功能都不值得做”缺乏该计算的支持。采用当前项目预算门槛是资源决策，不能描述成统计定理。
* `noise_floor.py` 的 hierarchy.note 仍错误宣称等用户均值才是官方 Avg，与官方函数输出矛盾；README 与工程日志对配对有效性的说法也未统一。
* 缓存对照若模型、提示、服务配置、任务、评测器和 runner 可比，仍可作为历史参考。若服务配置可能漂移，同期交错跑 A/B 更可信。同 seed 失效本身不能推出缓存永远无用或基线必须全部重跑。
* 子任务共享用户历史；100 个指标聚合单元不自动等于 100 个统计独立样本。置信区间宜以用户为簇处理；少用户情况下明确报告不确定性，不能靠复制 trial 夸大显著性。

## 7. 零模型复算入口

```powershell
python scripts/_official_metrics.py
python scripts/noise_floor.py data/simulations/stock_dev.json
python scripts/target_reachability.py data/simulations/stock_dev.json
python scripts/rubric_breakdown.py data/simulations/stock_dev_rubric_detail.json
python scripts/runaway_autopsy.py data/simulations/stock_dev.json --repeat-threshold 3
```

隔离实验数字的复算方式：读取各 checkpoint 的 simulations，按 task_id/trial 过滤，将 `reward_info.info.subtask_rewards` 中数值求平均。只有完整且各单元 trial 数相等时，该平铺平均才等于各子任务 trial 均值再平均；不完整产物需额外报告缺失。

代码证据：`agent/memory/adapt_memory.py`、`agent/candidate_marking.py`、`agent/marking_agent.py`、`agent/vitabench_runner.py`、`scripts/noise_floor.py`；对照：vendored `vita/memory/rewrite_memory.py`、`vita/agent/personalization_agent.py`、`vita/orchestrator/personalization_orchestrator.py`、`vita/metrics/agent_metrics.py`。历史机制参考工程日志 E-042～E-057，本文没有把其所有因果表述当作已证事实。

本次 vendored `src/vita` 相对其本地 HEAD 的 diff 为空；这只验证本地边界没有新增修改，不代表逐文件核验了当前远端 main 一致性。

官方背景：[VitaBench 2.0 项目](https://github.com/meituan-longcat/VitaBench-2.0)把个性化与主动询问置于长期多会话交互中评估，并将 Rewrite 列为 Agentic Memory。本文数值全部来自本地开发产物，不与官网不同模型/设置的榜单混比。

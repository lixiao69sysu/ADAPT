# 预注册：8 用户 ADAPT 主臂测量（1 试次）

- **日期**：2026-09-13
- **状态**：结果**尚未看到**时写下本文件。运行启动于 20:47，本文件在其后 2 分钟内写成。
- **目的**：把"看到结果之后再选判据"这条路堵掉。下面的命令、对照、阈值、归因计划在结果落地前固定。

## 1. 冻结声明

运行期间 **agent 代码与 memory 代码冻结**。本轮之后任何改动都会让这次测量的归因失效，
必须重新跑。冻结的代码版本是 `7d1b610`（`git log --oneline -1` 可核）。

允许的改动：`scripts/`（离线分析）、`docs/`。这些不进入 agent 的导入闭包。

## 2. 运行命令（实际执行的，逐字）

```powershell
$env:VITA_MODEL_CONFIG_PATH = (Resolve-Path models_adapt.yaml).Path
$env:VITA_MEMORY_CONFIG_PATH = (Resolve-Path memory_adapt.yaml).Path
python -m agent.vitabench_runner `
  --agent adapt --cohort dev --num-trials 1 `
  --task-ids E057330 E941775 J365414 M793481 O309411 P722245 Q089190 U000828 `
  --memory-type adapt --profile-summary --proactive-loop `
  --agent-llm qwen38-agent --user-llm qwen35-user --evaluator-llm qwen36-evaluator `
  --save-to data/simulations/adapt8_1t.json `
  --debug-to data/simulations/adapt8_1t.log
```

臂配置：`agent=adapt`、`proactive_loop=True`、`candidate_evidence=False`、`task_state=False`、
`memory_type=adapt`、`profile_summary=True`、`num_trials=1`。

`--cohort dev` 在这里**不参与选择**（显式 `--task-ids` 优先，`vitabench_runner.py:429`）；
`info["cohort"]` 因此会写着 `dev`，那是标签不是身份（E-092）。
**队列身份以 checkpoint 的 `tasks` 字段为准。**

## 3. 队列一致性（已在启动前核对）

| | 用户 |
|---|---|
| 本臂 `--task-ids` | E057330, E941775, J365414, M793481, O309411, P722245, Q089190, U000828 |
| 基线 `stock_avg4_8u.json` 的 `tasks` | 同上（完全一致） |
| 当前 `stable_user_split` 的 `dev` | U200109, W974351, U010122, U901652, B865629, Q089190, Y208341, E057330（**只重叠 2 人**） |

这是 E-092 拦下的错误：`--cohort dev` 已经不是基线那批人。

## 4. 对照的定义（三个视图，都必须报）

1 试次臂不能直接对照 4 试次 `Avg@4`。基线的单试次切片实测为：

| 切片 | Avg@1 |
|---|---|
| trial 0 | 0.2900 |
| trial 1 | 0.3200 |
| trial 2 | 0.2900 |
| trial 3 | 0.2700 |
| **pooled（= Avg@4）** | **0.2925** |

- **主对照**：`python scripts/paired_arms.py data/simulations/stock_avg4_8u.json data/simulations/adapt8_1t.json --label stock --label adapt --trial 0`
  —— 同一粒度（都是单次抽样），做官方单位上的配对符号检验。
- **次对照**：去掉 `--trial 0`，与基线 4 试次单位均值比（对照均值更精，翻转更粗）。
- **聚合**：`python scripts/_trial_slice_metrics.py data/simulations/adapt8_1t.json`
  —— 本臂的 pooled Avg@1 与基线 pooled 0.2925 是同一个量。

单试次切片之间的抖动是 sd≈0.019（0.2700–0.3200），**小于** 8 用户对照下限 ±0.0582。

## 5. 可达性算术（基线，零模型）

官方单位 100 个，按 4 试次通过计数：

| 通过次数 | 单位数 |
|---|---|
| 0/4 | **60** |
| 1/4 | 7 |
| 2/4 | 9 |
| 3/4 | 4 |
| 4/4 | 20 |

- 当前 `Avg@4 = 0.2925`。
- 到 `0.35` 需要净增 **0.0575 单位分** ≈ **6 个 0/4 单位被完全解决**，或 **23 个试次被挽救**。
- 即目标**不是**"全面提升"，而是**在 60 个从未通过的单位里啃下约 6 个**。这是本轮的靶子。

## 6. 判定规则（先定，后看）

以 pooled Avg@1（100 单位）为观测量，基线对照 0.2925：

| 观测量 | 判定 | 后续动作 |
|---|---|---|
| **≤ 0.32**（delta ≤ +0.0275，即不到所需位移的一半） | **NO RESOLVABLE MOVEMENT**：否证的是"**当前机制能交付到 0.35 所需的 +0.0575**"这个具体主张，**不是**"机制完全无效" | 停止在"观测层"继续加码；回到 E-089 的 44.9% 状态丢失做机制替换 |
| **0.32 – 0.35** | **NOT RESOLVABLE**：既不能声称有效也不能声称无效 | 如实报告为不可分辨，**不升格**，不据此改架构 |
| **≥ 0.35** | **MEETS TARGET, UNCONFIRMED** | 仅在同 8 用户上跑 4 试次确认；确认前不得对外声称达标 |

注意 0.32 **恰好是基线自己最好的那个单试次切片**（切片区间 0.2700–0.3200）。
所以落在 0.32 以下意味着"没有超出基线自身单次抽样范围的位移"，
按仓库纪律只能记为**不可分辨**，不得写成"证明无效"。

**任何**升格还必须同时满足：官方单位配对对比中 `fixes > breaks` 且符号检验 `p < 0.05`。
只有聚合均值上升、翻转方向却是抛硬币的，一律判为未证实（本轮之前那次 3 用户运行就是
7 fixes / 6 breaks，即抛硬币，因此当时没有作任何声称）。

单试次臂若落在 0.32–0.35 之间，**不允许**用"再跑一次试试"来挑一个好看的结果；
要动就动 4 试次（同一 8 用户、同配置），且预算事先说清。

## 6b. 配对门禁的算术：为什么"单试次刚好到 0.35"很可能过不了门禁

符号检验（`scripts/paired_arms.py` 与 `adapt_arm_report.py` 用的是同一个正态近似
`p = erfc(|wins − n/2| / sqrt(n/2))`）要求

```
net = fixes − breaks  ≥  1.96 · sqrt(n_discordant)
```

而 pooled 指标位移是 `delta = net / 100`（单试次下每个被挽救的单位记 +1，被破坏的记 −1）。
两条约束一起看：

| n_discordant | 门禁最小 net | 隐含最小 delta | p |
|---|---|---|---|
| 6 | 6 | +0.0600 | 0.0143 |
| 8 | 6 | +0.0600 | 0.0339 |
| 9 | 7 | +0.0700 | 0.0196 |
| 12 | 8 | +0.0800 | 0.0209 |
| 18 | 10 | +0.1000 | 0.0184 |
| 24 | 10 | +0.1000 | 0.0412 |
| 30 | 12 | +0.1200 | 0.0285 |
| 40 | 14 | +0.1400 | 0.0269 |
| 60 | 16 | +0.1600 | 0.0389 |

**含义（事先写下）**：

- 刚好达到目标（net=+6，delta=+0.0575）时，只有 **n_discordant ≤ 9** 才可能过门禁。
  也就是说：一个"成绩略好、但基本没改变行为"的臂能过；一个"行为大改、净收益 6 个单位"的臂
  **过不了**（n=30、net=6 时 p≈0.27）。
- 反过来，若本臂的翻转数很大（n≈30，说明机制确实改变了很多决策），
  要通过门禁就需要 **net ≥ 12，即 delta ≥ +0.12**。
- 因此 **单试次 8 用户设计在结构上很可能无法给出"确认"**，即使分数到了 0.35：
  它能回答的是"**可达性**"（是否朝 0.35 走），而"**确认**"要 4 试次
  （8 用户 × 4 试次 ≈ 46 h，按 86.4 min/(用户·试次) 计）。
- 这是设计限制，**不是**结果出来之后才找的解释。落在 0.35 以上但门禁未过时，
  报告必须写成 "MEETS TARGET, UNCONFIRMED"，不得写成"达标"。

> 附带记录：本臂用的是 1 试次，**不能**通过"续跑"加试次——
> `run_selected` 在 `save_to` 已存在且 `info` 不一致（`num_trials` 1 vs 4）时直接
> `raise ValueError`（`:489-495`）。要 4 试次必须换新文件、重跑 trial 0。

## 7. 归因计划（结果落地前定好读哪些量）

臂相对基线有 **4 处不同**，因此单独一个 delta **不能**归给某一个机制：

1. 新的 agent 子类 + 主动性问句循环（`--proactive-loop`）
2. `--memory-type adapt`（结构化记忆，含 E-090 的取代修复）
3. `--profile-summary`（LLM 画像摘要，召回侧）
4. 试次数 1 vs 4

结果落地时读：

- `info["adapt_agent"] = {proactive_loop, candidate_evidence, task_state}` —— 确认跑的是哪个臂。
- `states["adapt_agent"]`（`loop_events`）：`questions_committed` / `answers_linked` /
  `answers_resolved_to_a_value` / `task_states_annotated` 等**真实计数**。
  上一轮 3 用户运行的实测是 `{questions_committed: 6, answers_linked: 6, answers_resolved_to_a_value: 2}` ——
  即"问出去的 6 个问题里只有 2 个真正落成了一个值"。本次要看这个比例是否改善，
  因为**问句只有落成值才可能影响选择**。
- 记忆层：取代计数（E-090 修复前 0，修复后 8 用户共 27）。
- **失败机制分布**：`python scripts/mechanism_attribution.py data/simulations/adapt8_1t.json`
  （互斥主标签，机械判定，无模型）。**该设备已在基线上校准**，逐位复现 E-089：

  | mechanism | capability | runs | share |
  |---|---|---|---|
  | state_loss | personalization / long-term memory | 127 | 44.9% |
  | planning_defect | planning / task decomposition | 66 | 23.3% |
  | over_asking | proactiveness (over-asking) | 42 | 14.8% |
  | tool_hallucination | tool calling / grounding | 14 | 5.0% |
  | dead_loop_no_recovery | long-horizon control | 13 | 4.6% |
  | transaction_left_unfinished | execution / commitment | 11 | 3.9% |
  | unattributed | – | 8 | 2.8% |
  | missed_question | proactiveness (under-asking) | 2 | 0.7% |

  `graded_runs=400 / failing_runs=283 / pass_rate=0.2925`。
  **这是本轮最强的归因证据**：若本臂的 `state_loss` 占比相对上表明显下降，
  则"长期记忆/状态"这条路径真的被推动了；若 `over_asking` 上升，则主动性机制在**帮倒忙**。
  注意本臂只有 1 试次（100 条评分运行、失败约 70 条），份额的区间比基线宽，
  只做**方向性**比较，不做显著性声称。
- 逐用户 delta 对基线逐用户值：

| user | 基线 Avg@4 | 基线 trial0 |
|---|---|---|
| E057330 | 0.2885 | 0.1538 |
| E941775 | 0.2857 | 0.2857 |
| J365414 | 0.4091 | 0.4545 |
| M793481 | 0.2727 | 0.2727 |
| O309411 | 0.1250 | 0.1667 |
| P722245 | 0.3636 | 0.3636 |
| Q089190 | 0.3036 | 0.2857 |
| U000828 | 0.3036 | 0.3571 |

**禁止**：把逐用户的差异写成"某用户特有的规则"；把 `(user, trial, subtask)` 当独立单位；
引用 ±0.0203 当用户级下限；把审计相关性直接当瓶颈（E-053）。

## 8. 已知会让结果不可解释的情况（提前说清）

- 某个用户崩溃被跳过（runner 会 `continue`，`:531-536`）：该用户不出现在 checkpoint 里，
  8 人对照要改成 7 人并如实标注；可用同配置**续跑**补齐（`save_to` 已存在且 `info`/`tasks` 一致时从 `done` 继续，`:488-498`）。
- 评测失败（`evaluation_status != ok`）：`subtask_rewards` 缺失，该单位按 vendored 语义记 0，
  需单独统计条数并在报告里列出。
- 如果本臂的 `loop_events` 显示问句计数与上一轮同量级（个位数），那么"主动性"这条路径在
  本轮就是**未被真正执行**，任何分数变化都不该归给它。

## 9. 进度监视点

- Checkpoint **增量原子落盘**（每个用户完成后写一次，`:555`），所以
  `data/simulations/adapt8_1t.json` 里 `simulations` 的长度就是已完成用户数（0→8）。
- 实测基线耗时 **86.4 min/(用户·试次)**，预期本臂 5–12 小时。
- 守候作业 `scripts/_arm_watch.ps1` 会在 runner 退出后自动写出
  `data/simulations/adapt8_1t_report.txt` 与 `adapt8_1t_attribution.json`，
  并打印实际落地了几个用户。

## 10. 若用户被跳过：如何补齐（不改动任何配置）

runner 对单个用户的异常是 `logger.exception` + `continue`（`:531-536`），
该用户**不出现在 checkpoint 里**，而 `adapt_arm_report.py` 会因为缺人**中止**
（这是刻意的：不允许把 7 人的均值当 8 人队列的结果）。补齐办法是

**原样重跑同一条命令**：

```powershell
$env:VITA_MODEL_CONFIG_PATH = (Resolve-Path models_adapt.yaml).Path
$env:VITA_MEMORY_CONFIG_PATH = (Resolve-Path memory_adapt.yaml).Path
python -m agent.vitabench_runner `
  --agent adapt --cohort dev --num-trials 1 `
  --task-ids E057330 E941775 J365414 M793481 O309411 P722245 Q089190 U000828 `
  --memory-type adapt --profile-summary --proactive-loop `
  --agent-llm qwen38-agent --user-llm qwen35-user --evaluator-llm qwen36-evaluator `
  --save-to data/simulations/adapt8_1t.json `
  --debug-to data/simulations/adapt8_1t.log
```

`run_selected` 在 `save_to` 已存在、且 `info` 与 `tasks` **完全一致**时，
从 `done` 集合（键为 `(task_id, trial, seed)`）继续，只补跑缺失的那几项
（`:488-498`）；若配置有任何不一致会直接 `raise ValueError`，
**所以这条命令必须逐字与首次运行相同**——包括 `--debug-to`，因为它会写进 `info["debug_sidecar"]`。
不要为了"补一个用户"而修改任何 flag。

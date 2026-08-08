# ADAPT 环境搭建指南

> ADAPT: **A**gent with **D**ynamic **A**daptive **P**references **T**oward Sustained Consumption Goals
> 面向动态偏好与持续消费目标的长序列智能体

## 前置要求

| 依赖 | 版本要求 | 用途 |
|------|----------|------|
| Python | ≥ 3.11 | ADAPT Agent + VitaBench 2.0 |
| Git | ≥ 2.40 | 代码管理 |
| uv / pip | ≥ 最新 | Python 依赖管理 |

> 无需 Docker（VitaBench 2.0 工具为模拟环境，直接在本地 Python 中运行）。

## Phase 0: 部署 VitaBench 2.0（评测骨架）

### 0.1 克隆仓库（已就位）

```bash
cd ADAPT/            # 项目根目录（仓库目录名）
cd evaluation/vitabench/
```

### 0.2 安装依赖

```bash
pip install -e .
# 或
uv pip install -e .
```

核心依赖：
- `openai`（LLM API 调用，OpenAI 兼容）
- `datasets` / `huggingface_hub`（HuggingFace 数据集加载）
- `pydantic`、`loguru`（数据模型与日志）

### 0.3 下载数据集

```bash
pip install -U "huggingface_hub[cli]"
huggingface-cli download meituan-longcat/VitaBench-2.0 \
  --repo-type dataset \
  --local-dir data/vita/domains/personalization
```

验证：`data/vita/domains/personalization/tasks.json`（56 用户、771 子任务）。

### 0.4 配置 LLM

```bash
cp src/vita/models.yaml.example src/vita/models.yaml
export OPENAI_API_KEY=sk-...   # 任意 OpenAI 兼容端点（DeepSeek/Claude/OpenAI）
```

`models.yaml` 支持改 `default.base_url` 指向 DeepSeek / vLLM / Azure 等。

## Phase 1: 配置 ADAPT 智能体

### 1.1 Python 环境

```bash
cd ADAPT/agent/
python -m venv venv
# Linux/Mac: source venv/bin/activate
# Windows:   venv\Scripts\activate
pip install -r requirements.txt
```

### 1.2 核心依赖

```
# agent/requirements.txt
openai>=1.0.0
anthropic>=0.30.0        # 可选（Claude 扫描期）
chromadb>=0.4.0          # 向量记忆（开发期）
faiss-cpu>=1.7.0         # 向量检索（评测期，可选）
sqlalchemy>=2.0.0        # 结构化偏好存储
pydantic>=2.0.0
pyyaml>=6.0
```

### 1.3 ADAPT Agent 结构

```
agent/
├── memory/
│   ├── stream.py            # Memory Stream（事件+时间戳+重要性）[借鉴 GA]
│   ├── retrieval.py         # 三维检索 recency×importance×relevance [借鉴 GA]
│   ├── reflection.py        # Reflection + 偏好漂移检测 [自研]
│   ├── lifecycle.py         # 事实生命周期 + 选择性遗忘 [自研]
│   └── context.py           # 上下文分页 + 记忆注入 [借鉴 MemGPT]
├── proactive/
│   ├── gap_detector.py      # 信息缺口检测 [自研]
│   └── query_policy.py      # 主动询问策略 [自研]
├── tools/
│   ├── registry.py          # 工具注册表 [借鉴 Hermes 模式]
│   └── vitabench_adapter.py # 66 消费工具注册
└── agent.py                 # ADAPT Agent（实现 BaseMemory 接口）
```

## Phase 2: 运行评测

### 2.1 子集快速迭代（开发期）

```bash
# 用 ADAPT 自定义记忆类跑 10 个用户
vita run \
  --domain personalization \
  --memory-class adapt.memory.ADAPTMemory \
  --agent-llm deepseek-v4-pro \
  --user-llm deepseek-v4-pro \
  --evaluator-llm deepseek-v4-pro \
  --num-tasks 10 \
  --save-to mytest/adapt.json
```

### 2.2 对比基线（Memory Arena）

```bash
# 官方基线对照
bash scripts/run_memory_benchmark.sh full_context rewrite rag groundtruth
```

### 2.3 全量对比矩阵

```bash
bash evaluation/scripts/run_full_matrix.sh    # 56 用户 × 6 架构
bash evaluation/scripts/run_ablation.sh       # 消融 A/B/C/D
```

### 2.4 查看结果

```bash
vita view --file <simulation file>
```

## 关键配置约定（保证对比公平）

| 项 | 约定 |
|----|------|
| agent/user/evaluator LLM | 定死后整个对比组不换 |
| rollout | Avg@4（4 次独立） |
| max-steps / max-concurrency | 各组一致 |
| 语言 | `--language chinese`（与官方一致） |

## 常见问题

**Q: HuggingFace 下载慢/失败**
```bash
export HF_ENDPOINT=https://hf-mirror.com   # 国内镜像
```

**Q: API 限流**
```bash
vita run --max-concurrency 5 --max-errors 5
```

**Q: 想加自己的记忆后端**
在 `agent/memory/` 实现 `BaseMemory.read()/update()`，用 `--memory-class` 指定即可。

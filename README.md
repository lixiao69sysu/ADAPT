# ADAPT: Agent with Dynamic Adaptive Preferences Toward Sustained Consumption Goals

> ADAPT: 面向动态偏好与持续消费目标的长序列个性化消费智能体

[![Benchmark: VitaBench 2.0](https://img.shields.io/badge/Benchmark-VitaBench%202.0-green)](https://github.com/meituan-longcat/VitaBench-2.0)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## 概述

ADAPT 是一个长序列个性化消费智能体：它能够跨越数年时间理解用户的**动态偏好**、检测**偏好漂移**、在信息不足时**主动询问**，并代表用户完成复杂的消费决策。

### 核心问题

VitaBench 2.0 揭示的行业现状：SOTA 模型在理想 Full Context 下仅 ~0.50 Avg@4，所有模型随时间显著退化，主动沟通是共同弱点。ADAPT 针对这三个痛点：

1. **记忆**：多层记忆架构在长序列下稳定不崩
2. **漂移**：检测偏好变化并选择性遗忘过时信息
3. **主动**：信息不足时主动询问而非猜测

### 技术方案

- **骨架**：VitaBench 2.0 的 `LLMAgent` + `BaseMemory` 接口（与评测环境同构，零适配）
- **记忆**：Memory Stream + 三维检索 + Reflection（借鉴 Generative Agents）
- **主动**：信息缺口检测 + 主动询问策略（自研）
- **工程**：工具注册表 + 记忆原语（借鉴 Hermes Agent 设计模式）

> **借设计、不借代码**：每个优秀框架的精华用薄薄一层实现，不背重量级框架负担。

## 快速开始

```bash
# 1. 克隆项目
git clone https://github.com/lixiao69sysu/ADAPT.git
cd ADAPT

# 2. 克隆 VitaBench 2.0（评测骨架，单独仓库）
git clone https://github.com/meituan-longcat/VitaBench-2.0.git evaluation/vitabench

# 3. 安装 VitaBench 依赖
cd evaluation/vitabench
pip install -e .

# 4. 下载数据集
huggingface-cli download meituan-longcat/VitaBench-2.0 \
  --repo-type dataset --local-dir data/vita/domains/personalization

# 5. 配置 LLM（OpenAI 兼容端点均可）
cp src/vita/models.yaml.example src/vita/models.yaml
export OPENAI_API_KEY=sk-...   # DeepSeek / Claude / 任意兼容端点

# 6. 运行子集评测（ADAPT vs 基线）
cd ../..
bash evaluation/scripts/run_subsample.sh
```

## 项目结构

```
ADAPT/
├── agent/                 # ADAPT 智能体核心
│   ├── memory/            # 记忆系统（Stream/检索/漂移检测/生命周期）
│   ├── harness/           # Agent Harness（ToolGuard/ProgressGuard）
│   ├── evolution/         # Evolution Harness（坏案例挖掘+策略门控）
│   └── tests/             # 单元测试（43 个）
├── evaluation/
│   ├── vitabench/         # VitaBench 2.0（需单独克隆）
│   ├── run_adapt.py       # 冻结 VitaBench 启动器
│   ├── evolve.py          # Evolution CLI
│   ├── config/            # 模型配置
│   └── scripts/           # 评测脚本
├── docs/                  # 架构文档 + 评测方法论
├── PROJECT_PLAN.md        # 详细项目规划
└── SETUP.md               # 环境搭建指南
```

## 评测方法

在 VitaBench 2.0 Memory Arena 上对比 5 个官方基线（null / full_context / rewrite / rag / groundtruth），指标：Avg@4、Pass@4、Pass^4，外加成本效率与时间衰减曲线。详见 [PROJECT_PLAN.md](PROJECT_PLAN.md) 第三章。

## 引用与致谢

- VitaBench 2.0: `arXiv:2605.27141`（ICLR 2026）— [github.com/meituan-longcat/VitaBench-2.0](https://github.com/meituan-longcat/VitaBench-2.0)
- 设计借鉴: Generative Agents (Park et al. 2023) · MemGPT (Packer et al. 2024) · Hermes Agent (Nous Research)

## 许可证

MIT License

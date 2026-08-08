#!/usr/bin/env bash
# ADAPT 完整基线运行脚本（DeepSeek Flash，避开高峰 9-12/14-18）
# 用法: bash run_baseline_flash.sh
# 输出: evaluation/vitabench/data/simulations/baseline/

set -e
PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VITA_DIR="$PROJECT_ROOT/evaluation/vitabench"

# 加载 .env（不泄露 key 到日志）
set -a
source "$PROJECT_ROOT/.env"
set +a
export PYTHONIOENCODING=utf-8

cd "$VITA_DIR"
echo "[$(date)] ==== ADAPT 基线启动 (DeepSeek Flash) ===="

# 每个 memory type 单独运行，避免交互式 resume 提示
for MT in null full_context rewrite; do
  # 检查结果文件是否已存在，存在则跳过（可续跑）
  if [ -f "data/simulations/baseline/$MT.json" ]; then
    echo "[$(date)] $MT.json 已存在，跳过（如需重跑请先删除）"
    continue
  fi
  echo "[$(date)] >>> 开始 memory-type=$MT"
  vita run \
    --domain personalization \
    --memory-type "$MT" \
    --agent-llm deepseek-v4-flash \
    --user-llm deepseek-v4-flash \
    --evaluator-llm deepseek-v4-flash \
    --max-steps 50 \
    --max-concurrency 3 \
    --num-tasks 10 \
    --save-to "baseline/$MT.json" \
    --log-level ERROR \
    >> "/tmp/vita_baseline_$MT.log" 2>&1
  echo "[$(date)] >>> 完成 memory-type=$MT (exit=$?)"
done

echo "[$(date)] ==== ADAPT 基线全部完成 ===="

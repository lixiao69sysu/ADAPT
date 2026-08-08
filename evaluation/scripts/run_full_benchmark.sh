#!/bin/bash
# ADAPT Full Benchmark — Qwen2.5:7b-32k via Ollama
# 用法: bash run_full_benchmark.sh [memory_type]
#   memory_type: null | full_context | rewrite | rag | groundtruth
# 默认跑全部 5 种

set -e

# ── 环境 ──
source /d/Anaconda3/etc/profile.d/conda.sh
conda activate lcpy311
export DEEPSEEK_API_KEY=$(grep DEEPSEEK_API_KEY "e:/AI超级智能体/Agent-Proj/BiTE/.env" | cut -d= -f2)

# ── 参数 ──
MODEL="qwen2.5:7b-32k"
MEMORY_TYPES=${1:-"null rewrite rag full_context"}
NUM_TRIALS=${NUM_TRIALS:-1}        # 先跑 Avg@1，加量设 NUM_TRIALS=4
MAX_STEPS=30
MAX_CONCURRENCY=1                  # 单卡串行，避免 OOM
VITABENCH_DIR="e:/AI超级智能体/Agent-Proj/BiTE/evaluation/vitabench"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG_DIR="${VITABENCH_DIR}/data/simulations/benchmark_${TIMESTAMP}"
mkdir -p "$LOG_DIR"

echo "============================================"
echo " ADAPT Full Benchmark"
echo " Model: $MODEL"
echo " Trials: $NUM_TRIALS  (Avg@${NUM_TRIALS})"
echo " Memory types: $MEMORY_TYPES"
echo " Log dir: $LOG_DIR"
echo " Start: $(date)"
echo "============================================"

for mem in $MEMORY_TYPES; do
    SAVE_FILE="${LOG_DIR}/${mem}.json"
    LOG_FILE="${LOG_DIR}/${mem}.log"

    echo ""
    echo ">>> [$mem] Starting at $(date)"
    echo "    Save: $SAVE_FILE"
    echo "    Log:  $LOG_FILE"

    cd "$VITABENCH_DIR"
    vita run \
        --domain personalization \
        --memory-type "$mem" \
        --agent-llm "$MODEL" \
        --user-llm "$MODEL" \
        --evaluator-llm "$MODEL" \
        --num-tasks 56 \
        --num-trials "$NUM_TRIALS" \
        --max-steps "$MAX_STEPS" \
        --max-concurrency "$MAX_CONCURRENCY" \
        --save-to "$SAVE_FILE" \
        2>&1 | tee "$LOG_FILE"

    echo "<<< [$mem] Done at $(date)"

    # 休息 30 秒让 GPU 降温
    sleep 30
done

echo ""
echo "============================================"
echo " All done: $(date)"
echo " Results: $LOG_DIR"
echo ""
echo " Quick summary:"
for mem in $MEMORY_TYPES; do
    LOG_FILE="${LOG_DIR}/${mem}.log"
    if [ -f "$LOG_FILE" ]; then
        REWARD=$(grep "Average Reward" "$LOG_FILE" | tail -1 | awk '{print $NF}')
        echo "  $mem: Avg Reward = $REWARD"
    fi
done
echo "============================================"

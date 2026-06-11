#!/bin/bash
# ============================================================
# vllm-ascend 基准测试运行脚本
#
# 对指定模型配置和数据集运行 ais_bench 基准测试。
# 需要先通过 bootstrap.sh --with-benchmark 克隆 benchmark 仓库。
#
# 用法:
#   ./scripts/run-benchmark.sh                                # 使用默认配置运行
#   ./scripts/run-benchmark.sh -m vllm_api_stream_chat        # 指定模型配置
#   ./scripts/run-benchmark.sh -d synthetic_gen -d gsm8k_gen  # 指定多个数据集
# ============================================================

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"

export TORCH_DEVICE_BACKEND_AUTOLOAD=0

# ---- 默认配置 ----
MODEL_CONFIG="vllm_api_stream_chat"
DATASETS=()

# ---- 参数解析 ----
print_help() {
    echo "用法: $0 [选项]"
    echo ""
    echo "选项:"
    echo "  -m, --model <name>   模型配置名称（默认: vllm_api_stream_chat）"
    echo "  -d, --dataset <name> 数据集名称，可多次指定（默认: gsm8k_gen）"
    echo "  -h, --help           显示此帮助信息"
    echo ""
    echo "示例:"
    echo "  $0                                        # 默认配置"
    echo "  $0 -d synthetic_gen -d gsm8k_gen          # 多个数据集"
    echo "  $0 -m my_model -d gpqa_gen                # 自定义模型 + 数据集"
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -m|--model)
            MODEL_CONFIG="$2"; shift 2 ;;
        -d|--dataset)
            DATASETS+=("$2"); shift 2 ;;
        -h|--help)
            print_help ;;
        *)
            echo "[ERROR] 未知参数: $1，使用 -h 查看帮助"; exit 1 ;;
    esac
done

# ---- 默认数据集 ----
if [[ ${#DATASETS[@]} -eq 0 ]]; then
    DATASETS=(gsm8k_gen)
fi

# ---- 环境检查 ----
if [[ ! -d "$SCRIPT_DIR/benchmark" ]]; then
    echo "[ERROR] benchmark 仓库未克隆"
    echo "  请先运行: ./scripts/bootstrap.sh --with-benchmark"
    exit 1
fi

# ---- conda 环境 ----
if ! command -v conda &>/dev/null; then
    echo "[ERROR] conda 未找到"
    exit 1
fi
source "$HOME/miniconda3/etc/profile.d/conda.sh"
conda activate ais_bench 2>/dev/null || {
    echo "[ERROR] conda 环境 'ais_bench' 不存在"
    exit 1
}

export PYTHONPATH="$SCRIPT_DIR/benchmark${PYTHONPATH:+:$PYTHONPATH}"

# ---- 输出目录 ----
BENCH_OUTPUT_DIR="$SCRIPT_DIR/benchmark-outputs"
mkdir -p "$BENCH_OUTPUT_DIR"

# ---- 运行测试 ----
echo "========================================="
echo "  Ais_Bench 基准测试"
echo "  模型配置: ${MODEL_CONFIG}"
echo "  数据集:   ${DATASETS[*]}"
echo "  输出目录: ${BENCH_OUTPUT_DIR}"
echo "========================================="

for DS in "${DATASETS[@]}"; do
    echo ""
    echo ">>> 开始测试数据集: ${DS}"

    unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY

    python -m ais_bench.benchmark.cli.main \
        --models "${MODEL_CONFIG}" \
        --datasets "${DS}" \
        --dump-eval-details \
        --summarizer example \
        --debug \
        -w "$BENCH_OUTPUT_DIR"

    if [ $? -eq 0 ]; then
        echo ">>> ${DS} 测试完成"
    else
        echo ">>> ${DS} 测试失败，退出"
        exit 1
    fi
    echo "-----------------------------------------"
done

echo ""
echo "所有数据集测试结束"

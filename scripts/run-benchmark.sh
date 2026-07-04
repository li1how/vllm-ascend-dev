#!/bin/bash
# ============================================================
# vllm-ascend 基准测试运行脚本
#
# 对指定模型配置和数据集运行 ais_bench 基准测试。
# 需要先通过 bootstrap.sh -b | --with-benchmark 克隆 benchmark 仓库。
#
# 用法:
#   ./scripts/run-benchmark.sh                                              # 使用默认配置运行
#   ./scripts/run-benchmark.sh -m | --model vllm_api_stream_chat            # 指定模型配置
#   ./scripts/run-benchmark.sh -d | --dataset synthetic_gen -d gsm8k_gen    # 指定多个数据集
#   ./scripts/run-benchmark.sh -d gpqa_gen -n 10                            # 测试前 10 条数据
#   ./scripts/run-benchmark.sh -d synthetic_gen --mode perf                 # 性能测试
# ============================================================

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"

export TORCH_DEVICE_BACKEND_AUTOLOAD=0

# ---- 默认配置 ----
MODEL_CONFIG="vllm_api_stream_chat"
DATASETS=()
NUM_PROMPTS=""
MODE="all"

# ---- 参数解析 ----
print_help() {
    echo "用法: $0 [选项]"
    echo ""
    echo "选项:"
    echo "  -m, --model <name>       模型配置名称（默认: vllm_api_stream_chat）"
    echo "  -d, --dataset <name>     数据集名称，可多次指定（默认: gsm8k_gen）"
    echo "  -n, --num-prompts <num>  每个数据集的测试条数，必须为正整数（默认: 全部）"
    echo "      --mode <name>        运行模式（默认: all；性能测试使用 perf）"
    echo "  -h, --help               显示此帮助信息"
    echo ""
    echo "示例:"
    echo "  $0                                        # 默认配置"
    echo "  $0 -d synthetic_gen -d gsm8k_gen          # 多个数据集"
    echo "  $0 -m my_model -d gpqa_gen                # 自定义模型 + 数据集"
    echo "  $0 -d gpqa_gen -n 10                      # 测试前 10 条数据"
    echo "  $0 -d synthetic_gen --mode perf           # 性能测试"
    echo ""
    echo "可用的数据集 (--dataset 参数):"
    echo ""
    echo "  推理与数学 Reasoning & Math:"
    echo "    gsm8k_gen               GSM8K（默认，4-shot CoT str）"
    echo "    gsm8k_gen_0_shot_cot_chat_prompt  GSM8K 0-shot CoT chat"
    echo "    gsm8k_gen_0_shot_cot_str          GSM8K 0-shot CoT string"
    echo "    math_prm800k_500_0shot_cot_gen    MATH-500 0-shot"
    echo "    math_prm800k_500_5shot_cot_gen    MATH-500 5-shot"
    echo "    aime2024_gen            AIME 2024"
    echo "    aime2025_gen            AIME 2025"
    echo "    aime2026_gen            AIME 2026"
    echo "    dapo_math_gen           DAPO-Math"
    echo "    mgsm_gen_0_shot_cot_chat_prompt   MGSM 0-shot CoT chat"
    echo ""
    echo "  知识与问答 Knowledge & QA:"
    echo "    mmlu_gen                MMLU（5-shot string）"
    echo "    mmlu_gen_0_shot_cot_chat_prompt   MMLU 0-shot CoT chat"
    echo "    mmlu_pro_gen_0_shot_str           MMLU-Pro 0-shot"
    echo "    gpqa_gen                GPQA"
    echo "    gpqa_gen_0_shot_str     GPQA 0-shot string"
    echo "    ceval_gen               C-Eval"
    echo "    cmmlu_gen_0_shot_cot_chat_prompt  CMMLU 0-shot CoT chat"
    echo "    cmmlu_gen_5_shot_cot_chat_prompt  CMMLU 5-shot CoT chat"
    echo "    agieval_gen_0_shot_chat_prompt    AGIEval"
    echo "    triviaqa_gen_5_shot_chat_prompt   TriviaQA 5-shot"
    echo "    SuperGLUE_BoolQ_gen     BoolQ（SuperGLUE）"
    echo ""
    echo "  代码生成 Code Generation:"
    echo "    humaneval_gen_0_shot    HumanEval"
    echo "    humanevalx_gen_0_shot   HumanEval-X（多语言）"
    echo "    mbpp_passk_gen_3_shot_chat_prompt        MBPP pass@k"
    echo "    sanitized_mbpp_passk_gen_3_shot_chat_prompt  Sanitized MBPP"
    echo "    livecodebench_code_generate_lite_gen_0_shot_chat  LiveCodeBench"
    echo "    livecodebench_0_shot_chat_v6             LiveCodeBench v6"
    echo ""
    echo "  常识推理 Commonsense Reasoning:"
    echo "    hellaswag_gen_0_shot_chat_prompt        HellaSwag 0-shot"
    echo "    hellaswag_gen_10_shot_chat_prompt       HellaSwag 10-shot"
    echo "    piqa_gen_0_shot_chat_prompt             PIQA 0-shot"
    echo "    siqa_gen                                SIQA"
    echo "    winogrande_gen_0_shot_chat_prompt       WinoGrande 0-shot"
    echo "    ARC_c_gen_0_shot_chat_prompt            ARC-Challenge 0-shot"
    echo "    ARC_e_gen_0_shot_chat_prompt            ARC-Easy 0-shot"
    echo "    bbh_gen                                 BIG-Bench Hard"
    echo ""
    echo "  阅读理解 Reading Comprehension:"
    echo "    race_gen                 RACE（middle + high）"
    echo "    race_middle_gen_5_shot_chat             RACE-Middle"
    echo "    race_high_gen_5_shot_chat               RACE-High"
    echo "    drop_gen_0_shot_str     DROP 0-shot"
    echo ""
    echo "  长文本 Long Context:"
    echo "    longbench                 LongBench"
    echo "    longbenchv2_gen           LongBench v2"
    echo "    needlebench_v2_128k       NeedleBench 128K"
    echo "    needlebench_v2_256k       NeedleBench 256K"
    echo ""
    echo "  多模态 Multimodal (Vision):"
    echo "    mmmu_gen                 MMMU"
    echo "    mmmu_pro_vision_gen      MMMU-Pro Vision"
    echo "    mmstar_gen               MMStar"
    echo "    docvqa_gen               DocVQA"
    echo "    textvqa_gen              TextVQA"
    echo "    ocrbench_v2_gen_0_shot_chat  OCRBench v2"
    echo "    mathvision_gen           MathVision"
    echo "    videobench_gen           VideoBench"
    echo "    videomme_gen             VideoMME"
    echo ""
    echo "  函数调用 Function Calling:"
    echo "    BFCL_gen_all             BFCL 全部"
    echo "    BFCL_gen_single_turn     BFCL 单轮"
    echo "    BFCL_gen_multi_turn      BFCL 多轮"
    echo ""
    echo "  综合/基准测试 Synthetic & Benchmark:"
    echo "    synthetic_gen            Synthetic（通用性能测试）"
    echo "    synthetic_gen_string     Synthetic（字符串输入）"
    echo "    synthetic_gen_tokenid    Synthetic（Token ID 输入）"
    echo "    sharegpt_gen             ShareGPT"
    echo "    mtbench_gen              MT-Bench"
    echo ""
    echo "  其他 Others:"
    echo "    demo_gsm8k_gen_0_shot_cot_str_perf     Demo perf 测试"
    echo "    custom_qa_gen / custom_mcq_gen         自定义数据集"
    echo "    ifeval_0_shot_gen_str   IFEval 指令遵循"
    echo "    hle_llmjudge            HLE（LLM-as-Judge）"
    echo "    mooncake_trace_gen      Mooncake Trace"
    echo "    lambada_gen             LAMBADA"
    echo "    Xsum_gen                XSum 摘要"
    echo "    lcsts_gen               LCSTS 中文摘要"
    echo "    leval_*_gen             LEval 长文本（多个子集）"
    echo ""
    echo "  提示: 完整列表请运行 ais_bench --search --datasets <关键词>"
    echo "        数据集大多有多个变体（不同 shot/COT/chat 组合），"
    echo "        可用文件名前缀精确匹配，如 gsm8k_gen 匹配默认变体。"
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -m|--model)
            MODEL_CONFIG="$2"; shift 2 ;;
        -d|--dataset)
            DATASETS+=("$2"); shift 2 ;;
        -n|--num-prompts)
            NUM_PROMPTS="$2"; shift 2 ;;
        --mode)
            MODE="$2"; shift 2 ;;
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
    echo "  请先运行: ./scripts/bootstrap.sh -b | --with-benchmark"
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
echo "  测试条数: ${NUM_PROMPTS:-全部（每个数据集）}"
echo "  运行模式: ${MODE}"
echo "  输出目录: ${BENCH_OUTPUT_DIR}"
echo "========================================="

NUM_PROMPTS_ARGS=()
if [[ -n "$NUM_PROMPTS" ]]; then
    NUM_PROMPTS_ARGS=(--num-prompts "$NUM_PROMPTS")
fi

for DS in "${DATASETS[@]}"; do
    echo ""
    echo ">>> 开始测试数据集: ${DS}"

    unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY

    if python -m ais_bench.benchmark.cli.main \
        --models "${MODEL_CONFIG}" \
        --datasets "${DS}" \
        --mode "${MODE}" \
        "${NUM_PROMPTS_ARGS[@]}" \
        --dump-eval-details \
        --summarizer example \
        --debug \
        -w "$BENCH_OUTPUT_DIR"; then
        echo ">>> ${DS} 测试完成"
    else
        echo ">>> ${DS} 测试失败，退出"
        exit 1
    fi
    echo "-----------------------------------------"
done

echo ""
echo "所有数据集测试结束"

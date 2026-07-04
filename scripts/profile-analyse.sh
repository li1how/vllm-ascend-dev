#!/bin/bash
# ============================================================
# vLLM Profile 分析归档脚本
#
# 分析 vllm_profile 下的 torch_npu profile 目录，并在分析成功后
# 将本次 profile 目录压缩和归档到独立目录。
# 需要在包含 torch_npu 的 Python/Conda 环境中运行。
#
# 用法:
#   ./scripts/profile-analyse.sh                              # 分析 vllm_profile/*_ascend_pt
#   ./scripts/profile-analyse.sh -p | --profile-dir <dir>     # 指定 profile 根目录
#   ./scripts/profile-analyse.sh -g | --glob <pattern>        # 指定目录匹配模式
#   ./scripts/profile-analyse.sh -n | --archive-name <name>   # 指定归档名称
#   ./scripts/profile-analyse.sh -h | --help                  # 查看帮助
# ============================================================

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"

# ---- 默认配置 ----
PROFILE_DIR="vllm_profile"
PROFILE_GLOB="*_ascend_pt"
ARCHIVE_NAME=""

# ---- 参数解析 ----
print_help() {
    echo "用法: $0 [选项]"
    echo ""
    echo "选项:"
    echo "  -p, --profile-dir <dir>    profile 根目录（默认: vllm_profile）"
    echo "  -g, --glob <pattern>       目录匹配模式（默认: *_ascend_pt）"
    echo "  -n, --archive-name <name>  归档名称（默认: 当前时间戳）"
    echo "  -h, --help                 显示此帮助信息"
    echo ""
    echo "示例:"
    echo "  $0                                      # 分析 vllm_profile/*_ascend_pt"
    echo "  $0 -p vllm_profile -g '*_ascend_pt'     # 指定匹配模式"
    echo "  $0 -n run-qwen3-8b                      # 指定归档名称"
    exit 0
}

require_option_value() {
    local option="$1"
    local value="${2:-}"
    if [[ -z "$value" ]]; then
        echo "[ERROR] 选项 $option 需要参数值"
        exit 1
    fi
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -p|--profile-dir)
            require_option_value "$1" "${2:-}"
            PROFILE_DIR="$2"; shift 2 ;;
        -g|--glob)
            require_option_value "$1" "${2:-}"
            PROFILE_GLOB="$2"; shift 2 ;;
        -n|--archive-name)
            require_option_value "$1" "${2:-}"
            ARCHIVE_NAME="$2"; shift 2 ;;
        -h|--help)
            print_help ;;
        *)
            echo "[ERROR] 未知参数: $1，使用 -h 查看帮助"; exit 1 ;;
    esac
done

# ---- 环境检查 ----
resolve_path() {
    local path="$1"
    if [[ "$path" == /* ]]; then
        echo "$path"
    else
        echo "$SCRIPT_DIR/$path"
    fi
}

validate_archive_name() {
    local name="$1"
    if [[ "$name" == "." || "$name" == ".." || "$name" == *"/"* ]]; then
        echo "[ERROR] 归档名称只能是目录名，不能包含 /，也不能是 . 或 ..: $name"
        exit 1
    fi
}

PROFILE_DIR="$(resolve_path "$PROFILE_DIR")"
ARCHIVE_ROOT="$PROFILE_DIR/analysed"

if [[ -n "$ARCHIVE_NAME" ]]; then
    validate_archive_name "$ARCHIVE_NAME"
fi

if [[ ! -d "$PROFILE_DIR" ]]; then
    echo "[ERROR] profile 根目录不存在: $PROFILE_DIR"
    exit 1
fi

if ! command -v python &>/dev/null; then
    echo "[ERROR] python 未找到，请先激活包含 torch_npu 的环境"
    exit 1
fi

if ! command -v tar &>/dev/null; then
    echo "[ERROR] tar 未找到"
    exit 1
fi

# ---- 查找 profile 目录 ----
PROFILE_DIRS=()
while IFS= read -r -d '' dir; do
    PROFILE_DIRS+=("$dir")
done < <(find "$PROFILE_DIR" -mindepth 1 -maxdepth 1 -type d -name "$PROFILE_GLOB" -print0 | sort -z)

if [[ ${#PROFILE_DIRS[@]} -eq 0 ]]; then
    echo "[ERROR] 未找到匹配目录: $PROFILE_DIR/$PROFILE_GLOB"
    exit 1
fi

# ---- 分析 profile ----
echo "========================================="
echo "  vLLM Profile 分析归档"
echo "  profile 根目录: ${PROFILE_DIR}"
echo "  匹配模式:       ${PROFILE_GLOB}"
echo "  归档名称:       ${ARCHIVE_NAME:-自动时间戳}"
echo "  匹配数量:       ${#PROFILE_DIRS[@]}"
echo "========================================="

for profile_dir in "${PROFILE_DIRS[@]}"; do
    echo ">>> 分析: $profile_dir"
    python -c 'import sys; from torch_npu.profiler.profiler import analyse; analyse(sys.argv[1])' "$profile_dir"
    echo "[OK] 分析完成: $profile_dir"
done

# ---- 压缩与归档 ----
make_archive_dir() {
    local timestamp="$1"
    local candidate="$ARCHIVE_ROOT/$timestamp"
    local suffix=1

    while [[ -e "$candidate" ]]; do
        candidate="$ARCHIVE_ROOT/${timestamp}-${suffix}"
        suffix=$((suffix + 1))
    done

    mkdir -p "$candidate"
    echo "$candidate"
}

TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$ARCHIVE_ROOT"
ARCHIVE_DIR="$(make_archive_dir "${ARCHIVE_NAME:-$TIMESTAMP}")"
ARCHIVE_ID="$(basename "$ARCHIVE_DIR")"
ARCHIVE_FILE="$ARCHIVE_DIR/vllm-profile-$ARCHIVE_ID.tar.gz"

for profile_dir in "${PROFILE_DIRS[@]}"; do
    if [[ "$ARCHIVE_DIR/" == "$profile_dir/"* ]]; then
        echo "[ERROR] 归档目录不能位于待移动 profile 目录内部: $ARCHIVE_DIR"
        exit 1
    fi
done

PROFILE_NAMES=()
for profile_dir in "${PROFILE_DIRS[@]}"; do
    PROFILE_NAMES+=("$(basename "$profile_dir")")
done

echo ">>> 压缩 profile 目录..."
tar -czf "$ARCHIVE_FILE" -C "$PROFILE_DIR" "${PROFILE_NAMES[@]}"
echo "[OK] 压缩包已生成: $ARCHIVE_FILE"

echo ">>> 移动 profile 目录到归档目录..."
for profile_dir in "${PROFILE_DIRS[@]}"; do
    mv "$profile_dir" "$ARCHIVE_DIR/"
    echo "[OK] 已移动: $profile_dir"
done

echo ""
echo "[OK] profile 分析与归档完成: $ARCHIVE_DIR"

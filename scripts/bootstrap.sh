#!/bin/bash
# ============================================================
# vllm-ascend-dev 工作区一键初始化
# 用法:
#   ./scripts/bootstrap.sh                        # 默认初始化（不含 benchmark）
#   ./scripts/bootstrap.sh -b | --with-benchmark  # 含 benchmark 仓库
#   ./scripts/bootstrap.sh -h | --help            # 查看帮助
# ============================================================

set -e
# shellcheck source=./lib/common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/lib" && pwd)/common.sh"
ws_enter_workspace

# ---- 参数解析 ----
CLONE_BENCHMARK=false
print_help() {
    echo "用法: $0 [选项]"
    echo ""
    echo "选项:"
    echo "  -b, --with-benchmark  同时克隆 benchmark 仓库（默认不克隆）"
    echo "  -h, --help            显示此帮助信息"
    exit 0
}

for arg in "$@"; do
    case "$arg" in
        -b|--with-benchmark) CLONE_BENCHMARK=true ;;
        -h|--help)           print_help ;;
        *) ws_log_error "未知参数: $arg，使用 -h 查看帮助"; exit 1 ;;
    esac
done

ws_require_commands git

echo "============================================"
echo " vllm-ascend-dev 工作区初始化"
echo "============================================"
echo ""

clone_if_missing() {
    local repo_url="$1"
    local dir_name="$2"
    if [[ -d "$SCRIPT_DIR/$dir_name" ]]; then
        ws_log_skip "$dir_name 已存在"
    else
        echo "  [CLONE] $repo_url"
        git clone "$repo_url" "$SCRIPT_DIR/$dir_name"
    fi
}

init_file_from_template() {
    local template_path="$1"
    local target_path="$2"
    local mode="${3:-}"

    if [[ -e "$SCRIPT_DIR/$target_path" ]]; then
        ws_log_skip "$target_path 已存在"
        return
    fi

    mkdir -p "$(dirname "$SCRIPT_DIR/$target_path")"
    cp "$SCRIPT_DIR/$template_path" "$SCRIPT_DIR/$target_path"
    if [[ "$mode" == "executable" ]]; then
        chmod +x "$SCRIPT_DIR/$target_path"
    fi
    ws_log_ok "已从 $template_path 初始化 $target_path"
}

# ---- 1. 初始化本机配置 ----
ws_log_step "[1/3] 初始化本机配置..."

init_file_from_template "templates/devcontainer.json.template" ".devcontainer/devcontainer.json"
init_file_from_template "templates/server.sh.template" "scripts/server.sh" "executable"

echo ""

# ---- 2. 克隆代码仓库 ----
ws_log_step "[2/3] 克隆代码仓库..."

clone_if_missing "https://github.com/vllm-project/vllm.git"        "vllm"
clone_if_missing "https://github.com/vllm-project/vllm-ascend.git"  "vllm-ascend"

if $CLONE_BENCHMARK; then
    clone_if_missing "https://github.com/AISBench/benchmark.git" "benchmark"
else
    ws_log_skip "benchmark（默认不克隆，使用 -b | --with-benchmark 启用）"
fi

# 给 vllm 添加个人 fork remote（如已存在则跳过）
if git -C "$SCRIPT_DIR/vllm" remote get-url self &>/dev/null; then
    ws_log_skip "vllm remote 'self' 已存在"
else
    git -C "$SCRIPT_DIR/vllm" remote add self "https://github.com/li1how/vllm.git"
    ws_log_ok "vllm remote 'self' 已添加"
fi

# 给 vllm-ascend 添加个人 fork remote（如已存在则跳过）
if git -C "$SCRIPT_DIR/vllm-ascend" remote get-url self &>/dev/null; then
    ws_log_skip "vllm-ascend remote 'self' 已存在"
else
    git -C "$SCRIPT_DIR/vllm-ascend" remote add self "https://github.com/li1how/vllm-ascend.git"
    ws_log_ok "vllm-ascend remote 'self' 已添加"
fi

echo ""

# ---- 1.5. 配置 benchmark 本地 git ignore（仅当 benchmark 已存在时） ----
if [[ -d "$SCRIPT_DIR/benchmark" ]]; then
    EXCLUDE_FILE="$SCRIPT_DIR/benchmark/.git/info/exclude"
    if grep -q "^outputs/" "$EXCLUDE_FILE" 2>/dev/null; then
        ws_log_skip "benchmark .git/info/exclude 已配置"
    else
        cat >> "$EXCLUDE_FILE" << 'EXCLUDE_EOF'

# === 输出结果（运行时生成，不入仓库）===
outputs/

# === 数据集（动态导入，不入仓库）===
ais_bench/datasets/*/
EXCLUDE_EOF
        ws_log_ok "benchmark .git/info/exclude 已配置"
    fi
fi

echo ""

# ---- 3. 完成 ----
ws_log_step "[3/3] 完成"
echo ""
echo "============================================"
echo " 初始化完成"
echo "============================================"
echo ""
echo " 快速开始:"
echo "   code vllm-ascend-dev.code-workspace"

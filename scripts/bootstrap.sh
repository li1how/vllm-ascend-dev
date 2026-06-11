#!/bin/bash
# ============================================================
# vllm-ascend-dev 工作区一键初始化
# 用法:
#   ./bootstrap.sh              # 完整初始化
# ============================================================

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"

echo "============================================"
echo " vllm-ascend-dev 工作区初始化"
echo "============================================"
echo ""

# ---- 1. 克隆代码仓库 ----
echo ">>> [1/2] 克隆代码仓库..."

clone_if_missing() {
    local repo_url="$1"
    local dir_name="$2"
    if [[ -d "$SCRIPT_DIR/$dir_name" ]]; then
        echo "  [SKIP] $dir_name 已存在"
    else
        echo "  [CLONE] $repo_url"
        git clone "$repo_url" "$SCRIPT_DIR/$dir_name"
    fi
}

clone_if_missing "https://github.com/vllm-project/vllm.git"        "vllm"
clone_if_missing "https://github.com/vllm-project/vllm-ascend.git"  "vllm-ascend"
clone_if_missing "https://github.com/AISBench/benchmark.git"        "benchmark"

# 给 vllm-ascend 添加个人 fork remote（如已存在则跳过）
if git -C "$SCRIPT_DIR/vllm-ascend" remote get-url self &>/dev/null; then
    echo "  [SKIP] vllm-ascend remote 'self' 已存在"
else
    git -C "$SCRIPT_DIR/vllm-ascend" remote add self "https://github.com/li1how/vllm-ascend.git"
    echo "  [OK] vllm-ascend remote 'self' 已添加"
fi

echo ""

# ---- 2. 完成 ----
echo ">>> [2/2] 完成"
echo ""
echo "============================================"
echo " 初始化完成"
echo "============================================"
echo ""
echo " 快速开始:"
echo "   code vllm-ascend-dev.code-workspace"

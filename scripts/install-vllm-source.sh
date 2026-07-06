#!/bin/bash
# ============================================================
# 安装 vLLM 与 vLLM Ascend 源码
#
# 卸载当前 Python 环境中的 vllm / vllm-ascend 包，并从工作区
# vllm/ 与 vllm-ascend/ 目录执行 editable 安装。
#
# 用法:
#   ./scripts/install-vllm-source.sh                         # 完整重装 vllm + vllm-ascend
#   ./scripts/install-vllm-source.sh -s | --skip-uninstall    # 跳过卸载，仅安装源码
#   ./scripts/install-vllm-source.sh -v | --vllm-only         # 仅安装 vllm
#   ./scripts/install-vllm-source.sh -a | --ascend-only       # 仅安装 vllm-ascend
#   ./scripts/install-vllm-source.sh -h | --help              # 查看帮助
# ============================================================

set -euo pipefail
# shellcheck source=./lib/common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/lib" && pwd)/common.sh"
ws_enter_workspace

# ---- 默认配置 ----
CONDA_ENV="vllm-ascend-dev"
PYTHON_BIN=""
SKIP_UNINSTALL=false
INSTALL_MODE="all"
VLLM_BUILD_REQUIREMENTS=(setuptools-rust)

# ---- 参数解析 ----
print_help() {
    echo "用法: $0 [选项]"
    echo ""
    echo "卸载并从工作区源码安装 vllm / vllm-ascend。"
    echo ""
    echo "选项:"
    echo "  -s, --skip-uninstall  跳过卸载步骤，仅执行源码安装"
    echo "  -v, --vllm-only       仅处理 vllm"
    echo "  -a, --ascend-only     仅处理 vllm-ascend"
    echo "  -h, --help            显示此帮助信息"
    echo ""
    echo "示例:"
    echo "  $0                    # 卸载并安装 vllm + vllm-ascend"
    echo "  $0 -s                 # 跳过卸载，仅重新安装源码"
    echo "  $0 -v                 # 仅卸载并安装 vllm"
    echo "  $0 -a                 # 仅卸载并安装 vllm-ascend"
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -s|--skip-uninstall)
            SKIP_UNINSTALL=true
            shift
            ;;
        -v|--vllm-only)
            if [[ "$INSTALL_MODE" == "ascend" ]]; then
                ws_log_error "-v | --vllm-only 与 -a | --ascend-only 不能同时使用"
                exit 1
            fi
            INSTALL_MODE="vllm"
            shift
            ;;
        -a|--ascend-only)
            if [[ "$INSTALL_MODE" == "vllm" ]]; then
                ws_log_error "-v | --vllm-only 与 -a | --ascend-only 不能同时使用"
                exit 1
            fi
            INSTALL_MODE="ascend"
            shift
            ;;
        -h|--help)
            print_help
            ;;
        *)
            ws_log_error "未知参数: $1，使用 -h 查看帮助"
            exit 1
            ;;
    esac
done

INSTALL_VLLM=false
INSTALL_ASCEND=false
case "$INSTALL_MODE" in
    all)
        INSTALL_VLLM=true
        INSTALL_ASCEND=true
        ;;
    vllm)
        INSTALL_VLLM=true
        ;;
    ascend)
        INSTALL_ASCEND=true
        ;;
esac

# ---- 环境检查 ----
if $INSTALL_VLLM && [[ ! -d "$SCRIPT_DIR/vllm" ]]; then
    ws_log_error "vllm 仓库不存在: $SCRIPT_DIR/vllm"
    echo "  请先运行: ./scripts/bootstrap.sh"
    exit 1
fi
if $INSTALL_ASCEND && [[ ! -d "$SCRIPT_DIR/vllm-ascend" ]]; then
    ws_log_error "vllm-ascend 仓库不存在: $SCRIPT_DIR/vllm-ascend"
    echo "  请先运行: ./scripts/bootstrap.sh"
    exit 1
fi

ws_select_python_env "$CONDA_ENV"
if ! "$PYTHON_BIN" -m pip --version &>/dev/null; then
    ws_log_error "当前 Python 无法运行 pip: $PYTHON_BIN -m pip"
    exit 1
fi

# ---- 主逻辑 ----
PACKAGES=()
if $INSTALL_VLLM; then
    PACKAGES+=(vllm)
fi
if $INSTALL_ASCEND; then
    PACKAGES+=(vllm-ascend)
fi

echo "============================================"
echo " vLLM / vLLM Ascend 源码安装"
echo "============================================"
echo "  Python: $("$PYTHON_BIN" -c 'import sys; print(sys.executable)')"
echo "  组件:   ${PACKAGES[*]}"
echo "  卸载:   $([[ "$SKIP_UNINSTALL" == true ]] && echo "跳过" || echo "执行")"
if $INSTALL_VLLM; then
    echo "  vllm 构建依赖: ${VLLM_BUILD_REQUIREMENTS[*]}"
fi
echo "============================================"
echo ""

if $SKIP_UNINSTALL; then
    ws_log_skip "跳过卸载: ${PACKAGES[*]}"
else
    ws_log_step "卸载已安装包: ${PACKAGES[*]}"
    "$PYTHON_BIN" -m pip uninstall -y "${PACKAGES[@]}"
fi

if $INSTALL_VLLM; then
    echo ""
    ws_log_step "安装 vllm 源码构建依赖: ${VLLM_BUILD_REQUIREMENTS[*]}"
    "$PYTHON_BIN" -m pip install "${VLLM_BUILD_REQUIREMENTS[@]}"
    ws_log_ok "vllm 源码构建依赖安装完成"

    echo ""
    ws_log_step "从源码安装 vllm..."
    (
        cd "$SCRIPT_DIR/vllm"
        VLLM_TARGET_DEVICE=empty "$PYTHON_BIN" -m pip install -e . --no-build-isolation
    )
    ws_log_ok "vllm 源码安装完成"
fi

if $INSTALL_ASCEND; then
    echo ""
    ws_log_step "从源码安装 vllm-ascend..."
    (
        cd "$SCRIPT_DIR/vllm-ascend"
        COMPILE_CUSTOM_KERNELS=1 "$PYTHON_BIN" -m pip install -e . --no-build-isolation
    )
    ws_log_ok "vllm-ascend 源码安装完成"
fi

echo ""
ws_log_ok "源码安装流程完成"

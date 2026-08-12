#!/bin/bash
# ============================================================
# 安装并预热 vllm-ascend pre-commit 环境
#
# 使用 APT 或 YUM 补齐系统级 lint 工具和 actionlint 所需的 Go，
# 使用目标 Python 的 pip 安装项目 lint 依赖。Node 运行时由 pre-commit 管理。
#
# 用法:
#   ./scripts/install-pre-commit.sh             # 安装、预热并启用 Git hooks
#   ./scripts/install-pre-commit.sh -h | --help # 查看帮助
# ============================================================

set -e
# shellcheck source=./lib/common.sh
# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/lib" && pwd)/common.sh"
ws_enter_workspace

# ---- 默认配置 ----
CONDA_ENV="vllm-ascend-dev"
VLLM_ASCEND_DIR="$SCRIPT_DIR/vllm-ascend"
GO_PROXY_DEFAULT="https://repo.huaweicloud.com/repository/goproxy/"
NPM_REGISTRY_DEFAULT="https://repo.huaweicloud.com/repository/npm/"
SYSTEM_PACKAGES=()

# ---- 参数解析 ----
print_help() {
    echo "用法: $0 [选项]"
    echo ""
    echo "使用 APT/YUM 和 pip 补齐 vllm-ascend lint 依赖，预热并启用 Git hooks。"
    echo ""
    echo "选项:"
    echo "  -h, --help  显示此帮助信息"
    echo ""
    echo "注意:"
    echo "  检测到 conda 时必须成功激活 vllm-ascend-dev；未检测到 conda 时使用系统 Python。"
    echo "  本脚本不会运行 format.sh，也不会修改 vllm-ascend 中的源码文件。"
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help) print_help ;;
        *) ws_log_error "未知参数: $1，使用 -h 查看帮助"; exit 1 ;;
    esac
done

# ---- 环境检查 ----
if [[ ! -d "$VLLM_ASCEND_DIR" ]]; then
    ws_log_error "vllm-ascend 仓库不存在: $VLLM_ASCEND_DIR"
    exit 1
fi

install_python_dependencies() {
    local -a pip_args=(install -r requirements-lint.txt)

    if ! ws_command_exists conda &&
        "$PYTHON_BIN" -c 'import pathlib, sysconfig, sys; sys.exit(0 if (pathlib.Path(sysconfig.get_path("stdlib")) / "EXTERNALLY-MANAGED").exists() else 1)'; then
        pip_args+=(--break-system-packages)
        ws_log_warn "系统 Python 受 PEP 668 管理，使用 --break-system-packages 安装 lint 依赖"
    fi

    ws_log_step "安装 vllm-ascend Python lint 依赖..."
    "$PYTHON_BIN" -m pip "${pip_args[@]}"
}

prewarm_hooks() {
    export SSL_CERT_FILE="$WS_SYSTEM_CA_FILE"
    export NODE_EXTRA_CA_CERTS="$WS_SYSTEM_CA_FILE"
    export NPM_CONFIG_CAFILE="$WS_SYSTEM_CA_FILE"
    export GOPROXY="${GOPROXY:-$GO_PROXY_DEFAULT}"
    export NPM_CONFIG_REGISTRY="${NPM_CONFIG_REGISTRY:-$NPM_REGISTRY_DEFAULT}"

    ws_log_step "预热 pre-commit hooks（缓存目录: ${PRE_COMMIT_HOME:-$HOME/.cache/pre-commit}）..."
    "$PYTHON_BIN" -m pre_commit install-hooks

    ws_log_step "安装 Git hooks..."
    "$PYTHON_BIN" -m pre_commit install
}

# ---- 主逻辑 ----
ws_select_package_manager
case "$WS_SYSTEM_FAMILY" in
    debian) SYSTEM_PACKAGES=(gitleaks golang-go shellcheck) ;;
    rhel) SYSTEM_PACKAGES=(gitleaks golang ShellCheck) ;;
esac
ws_install_system_packages "${SYSTEM_PACKAGES[@]}"

ws_select_python_env "$CONDA_ENV"

cd "$VLLM_ASCEND_DIR"

install_python_dependencies
prewarm_hooks

ws_log_ok "vllm-ascend pre-commit 环境已安装并预热"
ws_log_info "现在可直接运行: cd $VLLM_ASCEND_DIR && ./format.sh ci"

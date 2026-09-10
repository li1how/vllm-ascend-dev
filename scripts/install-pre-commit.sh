#!/bin/bash
# ============================================================
# 安装并预热 vllm-ascend pre-commit 环境
#
# 使用 APT 或 YUM 补齐系统依赖；YUM 环境缺少的 lint 工具使用官方发行包。
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
GITLEAKS_VERSION="8.24.2"
SHELLCHECK_VERSION="0.10.0"

# ---- 参数解析 ----
print_help() {
    echo "用法: $0 [选项]"
    echo ""
    echo "安装 vllm-ascend lint 依赖，预热并启用 Git hooks。"
    echo ""
    echo "选项:"
    echo "  -h, --help  显示此帮助信息"
    echo ""
    echo "注意:"
    echo "  有 conda 时必须成功激活 $CONDA_ENV；无 conda 时使用系统 Python。"
    echo "  YUM 环境缺少的 Gitleaks/ShellCheck 从 GitHub 下载到 /usr/local/bin。"
    echo "  发行包支持 Linux x86_64/aarch64。"
    echo "  本脚本不会运行 format.sh 或修改项目源码。"
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help) print_help ;;
        *) ws_log_error "未知参数: $1，使用 -h 查看帮助"; exit 1 ;;
    esac
done

# 用子 shell 限定临时目录和 EXIT trap 的生命周期。
install_release_tool() (
    local tool="$1"
    local arch gitleaks_arch archive url member work_dir
    local -a privilege=()

    case "$tool" in
        gitleaks) "$tool" version &>/dev/null && exit 0 ;;
        shellcheck) "$tool" --version &>/dev/null && exit 0 ;;
        *) ws_log_error "不支持的发行包工具: $tool"; exit 1 ;;
    esac

    arch="$(uname -m)"
    case "$arch" in
        x86_64) gitleaks_arch="x64" ;;
        aarch64) gitleaks_arch="arm64" ;;
        *) ws_log_error "不支持的发行包架构: $arch"; exit 1 ;;
    esac

    case "$tool" in
        gitleaks)
            archive="gitleaks_${GITLEAKS_VERSION}_linux_${gitleaks_arch}.tar.gz"
            url="https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/$archive"
            member="gitleaks"
            ;;
        shellcheck)
            archive="shellcheck-v${SHELLCHECK_VERSION}.linux.${arch}.tar.xz"
            url="https://github.com/koalaman/shellcheck/releases/download/v${SHELLCHECK_VERSION}/$archive"
            member="shellcheck-v${SHELLCHECK_VERSION}/shellcheck"
            ;;
    esac

    if (( EUID != 0 )); then
        ws_require_commands sudo
        privilege=(sudo)
    fi
    work_dir="$(mktemp -d)"
    trap 'rm -rf -- "$work_dir"' EXIT
    ws_log_step "安装官方发行包: $archive"
    curl -fsSL --retry 3 --cacert "$WS_SYSTEM_CA_FILE" -o "$work_dir/$archive" "$url"
    tar --no-same-owner -xf "$work_dir/$archive" -C "$work_dir" "$member"
    "${privilege[@]}" install -D -m 0755 "$work_dir/$member" "/usr/local/bin/$tool"
)

install_python_dependencies() {
    local -a pip_args=(install -r requirements-lint.txt)

    if ! ws_command_exists conda &&
        "$PYTHON_BIN" -c '
import pathlib, sys, sysconfig
marker = pathlib.Path(sysconfig.get_path("stdlib")) / "EXTERNALLY-MANAGED"
sys.exit(0 if marker.exists() else 1)
'; then
        pip_args+=(--break-system-packages)
        ws_log_warn "系统 Python 受 PEP 668 管理，使用 --break-system-packages 安装 lint 依赖"
    fi

    ws_log_step "安装 vllm-ascend Python lint 依赖..."
    "$PYTHON_BIN" -m pip "${pip_args[@]}"
}

# ---- 环境检查 ----
if [[ ! -d "$VLLM_ASCEND_DIR" ]]; then
    ws_log_error "vllm-ascend 仓库不存在: $VLLM_ASCEND_DIR"
    exit 1
fi
ws_select_package_manager
ws_select_python_env "$CONDA_ENV"

# 所有下载共用系统证书；Go/npm 镜像可由环境变量覆盖。
export SSL_CERT_FILE="$WS_SYSTEM_CA_FILE"
export PIP_CERT="$WS_SYSTEM_CA_FILE"
export NODE_EXTRA_CA_CERTS="$WS_SYSTEM_CA_FILE"
export NPM_CONFIG_CAFILE="$WS_SYSTEM_CA_FILE"
export GOPROXY="${GOPROXY:-$GO_PROXY_DEFAULT}"
export NPM_CONFIG_REGISTRY="${NPM_CONFIG_REGISTRY:-$NPM_REGISTRY_DEFAULT}"

# ---- 安装系统工具 ----
case "$WS_SYSTEM_FAMILY" in
    debian)
        ws_install_system_packages gitleaks golang-go shellcheck
        ;;
    rhel)
        ws_install_system_packages golang curl tar xz ca-certificates
        install_release_tool gitleaks
        install_release_tool shellcheck
        ;;
esac
hash -r
# 确认后续单独运行 format.sh 时，PATH 中的工具也能正常执行。
gitleaks version
shellcheck --version
go version

# ---- 安装 Python 依赖并启用 hooks ----
cd "$VLLM_ASCEND_DIR"
install_python_dependencies

ws_log_step "预热并安装 Git hooks..."
"$PYTHON_BIN" -m pre_commit install --install-hooks

ws_log_ok "vllm-ascend pre-commit 环境已安装并预热"
ws_log_info "现在可直接运行: cd $VLLM_ASCEND_DIR && ./format.sh ci"

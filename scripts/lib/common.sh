#!/bin/bash
# ============================================================
# vllm-ascend-dev 脚本公共函数库
#
# 本文件供 scripts/ 下的 Bash 脚本 source 使用，不作为独立命令执行。
# ============================================================

_WS_COMMON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WS_ROOT_DIR="$(cd "$_WS_COMMON_DIR/../.." && pwd)"
WS_SYSTEM_FAMILY=""
WS_PACKAGE_MANAGER=""
WS_SYSTEM_CA_FILE=""
WS_CA_ANCHOR_DIR=""
WS_CA_UPDATE_COMMAND=()

ws_enter_workspace() {
    SCRIPT_DIR="$WS_ROOT_DIR"
    export SCRIPT_DIR
    cd "$SCRIPT_DIR" || return 1
}

ws_log_error() {
    echo "[ERROR] $*" >&2
}

ws_log_warn() {
    echo "[WARN] $*"
}

ws_log_info() {
    echo "[INFO] $*"
}

ws_log_ok() {
    echo "[OK] $*"
}

ws_log_skip() {
    echo "  [SKIP] $*"
}

ws_log_step() {
    echo ">>> $*"
}

ws_command_exists() {
    command -v "$1" &>/dev/null
}

ws_select_package_manager() {
    local os_release_file="${WS_OS_RELEASE_FILE:-/etc/os-release}"
    local ID=""
    local ID_LIKE=""
    local os_id
    local os_id_like
    local system_family=""

    if [[ ! -r "$os_release_file" ]]; then
        ws_log_error "无法读取系统信息: $os_release_file"
        exit 1
    fi

    # shellcheck disable=SC1090
    source "$os_release_file"
    os_id="${ID,,}"
    os_id_like="${ID_LIKE,,}"

    case "$os_id" in
        debian|ubuntu)
            system_family="debian"
            ;;
        rhel|centos|rocky|almalinux|ol|fedora|openeuler)
            system_family="rhel"
            ;;
        *)
            case " $os_id_like " in
                *" debian "*|*" ubuntu "*)
                    system_family="debian"
                    ;;
                *" rhel "*|*" centos "*|*" rocky "*|*" almalinux "*|*" ol "*|*" fedora "*|*" openeuler "*)
                    system_family="rhel"
                    ;;
            esac
            ;;
    esac

    WS_SYSTEM_FAMILY=""
    WS_PACKAGE_MANAGER=""
    WS_SYSTEM_CA_FILE=""
    WS_CA_ANCHOR_DIR=""
    WS_CA_UPDATE_COMMAND=()

    # 这些变量是公共返回值，由 source 本文件的调用脚本使用。
    # shellcheck disable=SC2034
    case "$system_family" in
        debian)
            WS_SYSTEM_FAMILY="debian"
            WS_PACKAGE_MANAGER="APT"
            WS_SYSTEM_CA_FILE="/etc/ssl/certs/ca-certificates.crt"
            WS_CA_ANCHOR_DIR="/usr/local/share/ca-certificates"
            WS_CA_UPDATE_COMMAND=(update-ca-certificates)
            if ! ws_command_exists apt-get; then
                ws_log_error "当前系统属于 Debian 家族，但缺少 apt-get"
                exit 1
            fi
            ;;
        rhel)
            WS_SYSTEM_FAMILY="rhel"
            WS_PACKAGE_MANAGER="YUM"
            WS_SYSTEM_CA_FILE="/etc/pki/tls/certs/ca-bundle.crt"
            WS_CA_ANCHOR_DIR="/etc/pki/ca-trust/source/anchors"
            WS_CA_UPDATE_COMMAND=(update-ca-trust extract)
            if ! ws_command_exists yum; then
                ws_log_error "当前系统属于 RHEL 家族，但缺少 yum"
                exit 1
            fi
            ;;
        *)
            ws_log_error "不支持的系统家族: ID=$os_id, ID_LIKE=$os_id_like"
            exit 1
            ;;
    esac
}

ws_install_system_packages() {
    local -a apt_sandbox_option=()
    local -a privilege=()

    if (( $# == 0 )); then
        ws_log_error "未指定要安装的系统包"
        exit 1
    fi
    if [[ -z "$WS_PACKAGE_MANAGER" ]]; then
        ws_log_error "包管理器未选择，请先调用 ws_select_package_manager"
        exit 1
    fi

    if (( EUID != 0 )); then
        if ws_command_exists sudo; then
            privilege=(sudo)
        else
            ws_log_error "安装系统包需要 root 权限，请用 root 运行或安装 sudo"
            exit 1
        fi
    fi

    case "$WS_PACKAGE_MANAGER" in
        APT)
            if [[ -f /.dockerenv || -f /run/.containerenv ]]; then
                apt_sandbox_option=(-o APT::Sandbox::User=root)
                ws_log_warn "检测到容器环境，APT 使用 root 下载用户（仍校验仓库和软件包签名）"
            fi
            ws_log_step "使用 APT 更新软件包缓存..."
            "${privilege[@]}" apt-get "${apt_sandbox_option[@]}" \
                -o Acquire::Retries=3 update
            ws_log_step "使用 APT 安装: $*"
            "${privilege[@]}" env DEBIAN_FRONTEND=noninteractive \
                apt-get "${apt_sandbox_option[@]}" -o Acquire::Retries=3 \
                install -y --no-install-recommends "$@"
            ;;
        YUM)
            ws_log_step "使用 YUM 更新软件包缓存..."
            "${privilege[@]}" yum makecache
            ws_log_step "使用 YUM 安装: $*"
            "${privilege[@]}" yum install -y "$@"
            ;;
        *)
            ws_log_error "不支持的包管理器: $WS_PACKAGE_MANAGER"
            exit 1
            ;;
    esac
}

ws_require_value() {
    local option="$1"
    local value="${2:-}"
    if [[ -z "$value" || "$value" == -* ]]; then
        ws_log_error "$option 需要参数值，使用 -h 查看帮助"
        exit 1
    fi
}

ws_require_commands() {
    local cmd
    for cmd in "$@"; do
        if ! ws_command_exists "$cmd"; then
            ws_log_error "缺少依赖: $cmd"
            exit 1
        fi
    done
}

ws_resolve_path() {
    local path="$1"
    if [[ "$path" == /* ]]; then
        echo "$path"
    else
        echo "$SCRIPT_DIR/$path"
    fi
}

ws_load_env() {
    local env_file="${1:-$SCRIPT_DIR/.env}"
    if [[ -f "$env_file" ]]; then
        set -a
        # shellcheck disable=SC1090
        source "$env_file"
        set +a
    fi
}

ws_select_python_env() {
    local conda_env="$1"

    if ws_command_exists conda; then
        local conda_base
        if ! conda_base="$(conda info --base 2>/dev/null)"; then
            ws_log_error "conda 已安装但初始化失败，拒绝回退系统 Python"
            exit 1
        fi
        if [[ ! -f "$conda_base/etc/profile.d/conda.sh" ]]; then
            ws_log_error "未找到 conda 初始化脚本: $conda_base/etc/profile.d/conda.sh"
            exit 1
        fi

        # shellcheck disable=SC1091
        source "$conda_base/etc/profile.d/conda.sh"
        if ! conda activate "$conda_env" 2>/dev/null; then
            ws_log_error "conda 环境 '$conda_env' 不存在或无法激活，拒绝回退系统 Python"
            exit 1
        fi

        PYTHON_BIN="python"
        ws_log_ok "使用 conda 环境: $conda_env"
        return
    fi

    ws_log_warn "conda 未找到，使用系统 Python"

    if ws_command_exists python; then
        PYTHON_BIN="python"
    elif ws_command_exists python3; then
        PYTHON_BIN="python3"
    else
        ws_log_error "未找到可用 Python"
        exit 1
    fi
    ws_log_ok "使用系统 Python: $("$PYTHON_BIN" -c 'import sys; print(sys.executable)' 2>/dev/null || command -v "$PYTHON_BIN")"
}

ws_require_python_module() {
    local module="$1"
    local hint="$2"

    if [[ -z "${PYTHON_BIN:-}" ]]; then
        ws_log_error "PYTHON_BIN 未设置，请先调用 ws_select_python_env"
        exit 1
    fi

    if ! "$PYTHON_BIN" -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('$module') else 1)" &>/dev/null; then
        ws_log_error "当前 Python 无法导入 $module"
        echo "  $hint"
        exit 1
    fi
}

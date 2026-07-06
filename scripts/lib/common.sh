#!/bin/bash
# ============================================================
# vllm-ascend-dev 脚本公共函数库
#
# 本文件供 scripts/ 下的 Bash 脚本 source 使用，不作为独立命令执行。
# ============================================================

_WS_COMMON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WS_ROOT_DIR="$(cd "$_WS_COMMON_DIR/../.." && pwd)"

ws_enter_workspace() {
    SCRIPT_DIR="$WS_ROOT_DIR"
    export SCRIPT_DIR
    cd "$SCRIPT_DIR"
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
        source "$env_file"
        set +a
    fi
}

ws_select_python_env() {
    local conda_env="$1"

    if ws_command_exists conda; then
        local conda_base
        if conda_base="$(conda info --base 2>/dev/null)" && [[ -f "$conda_base/etc/profile.d/conda.sh" ]]; then
            source "$conda_base/etc/profile.d/conda.sh"
            if conda activate "$conda_env" 2>/dev/null; then
                PYTHON_BIN="python"
                ws_log_ok "使用 conda 环境: $conda_env"
                return
            fi
            ws_log_warn "conda 环境 '$conda_env' 不可用，回退系统 Python"
        else
            ws_log_warn "conda 初始化失败，回退系统 Python"
        fi
    else
        ws_log_warn "conda 未找到，回退系统 Python"
    fi

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

#!/bin/bash
# ============================================================
# Dev Container 创建后初始化
#
# 由 devcontainer postCreateCommand 调用，用于配置容器内通用开发环境。
#
# 用法:
#   ./scripts/devcontainer-post-create.sh            # 初始化 devcontainer
#   ./scripts/devcontainer-post-create.sh -h | --help # 查看帮助
# ============================================================

set -euo pipefail
# shellcheck source=./lib/common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/lib" && pwd)/common.sh"
ws_enter_workspace

# ---- 参数解析 ----
print_help() {
    echo "用法: $0 [选项]"
    echo ""
    echo "初始化 devcontainer 内的通用开发环境。"
    echo ""
    echo "选项:"
    echo "  -h, --help  显示此帮助信息"
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help) print_help ;;
        *) ws_log_error "未知参数: $1，使用 -h 查看帮助"; exit 1 ;;
    esac
done

ws_require_commands git sed

fix_atb_env() {
    ws_log_step "修正 Ascend ATB 环境配置..."
    local env_file
    local changed=false

    for env_file in /root/.bashrc /etc/profile; do
        if [[ ! -f "$env_file" ]]; then
            ws_log_skip "$env_file 不存在"
            continue
        fi

        sed -i -E 's#^[[:space:]]*source /usr/local/Ascend/nnal/atb/set_env\.sh([[:space:]]+--cxx_abi=[01])?[[:space:]]*$#source /usr/local/Ascend/nnal/atb/set_env.sh --cxx_abi=0#' "$env_file"
        ws_log_ok "$env_file 已检查"
        changed=true
    done

    if ! $changed; then
        ws_log_skip "未发现需要检查的环境文件"
    fi
}

configure_git_proxy() {
    ws_log_step "配置 Git 代理..."
    local git_proxy="${devcontainer_proxy:-}"

    if [[ -z "$git_proxy" ]]; then
        git_proxy="${https_proxy:-${HTTPS_PROXY:-}}"
    fi

    if [[ -z "$git_proxy" ]]; then
        git_proxy="${http_proxy:-${HTTP_PROXY:-}}"
    fi

    if [[ -z "$git_proxy" ]]; then
        ws_log_skip "未设置 devcontainer_proxy / http_proxy / https_proxy"
        return
    fi

    git config --global http.proxy "$git_proxy"
    ws_log_ok "git http.proxy 已配置"
}

configure_pip_index() {
    ws_log_step "配置 pip 镜像..."

    if ! ws_command_exists pip; then
        ws_log_warn "pip 未找到，跳过 pip 镜像配置"
        return
    fi

    pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
    ws_log_ok "pip index-url 已配置"
}

fix_atb_env
configure_git_proxy
configure_pip_index

ws_log_ok "devcontainer post-create 初始化完成"

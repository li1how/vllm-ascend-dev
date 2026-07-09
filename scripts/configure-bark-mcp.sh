#!/bin/bash
# ============================================================
# 配置/卸载全局 Bark MCP
#
# 使用 Bark 官方 HTTP MCP，为 Codex 和 Claude Code 写入全局 MCP 配置。
# 默认从 .env 读取 BARK_KEY，并把实际 key 写入全局 MCP URL。
#
# 用法:
#   ./scripts/configure-bark-mcp.sh                         # 配置 Codex + Claude Code
#   ./scripts/configure-bark-mcp.sh -t | --target codex      # 仅配置 Codex
#   ./scripts/configure-bark-mcp.sh -k | --key <key>          # 通过参数传入 Bark key
#   ./scripts/configure-bark-mcp.sh -u | --uninstall          # 卸载全局 Bark MCP
#   ./scripts/configure-bark-mcp.sh -h | --help              # 查看帮助
# ============================================================

set -e
# shellcheck source=./lib/common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/lib" && pwd)/common.sh"
ws_enter_workspace

# ---- 默认配置 ----
ACTION="install"
TARGET="all"
FORCE=false
BARK_KEY_ARG=""
BARK_KEY_VALUE=""
BARK_KEY_SOURCE=""
BARK_MCP_URL=""
SAFE_BARK_MCP_URL="https://api.day.app/mcp/<BARK_KEY>"
CODEX_BARK_AUTH_HEADER="Bearer bark-url-auth"

# ---- 参数解析 ----
print_help() {
    echo "用法: $0 [选项]"
    echo ""
    echo "选项:"
    echo "  -t, --target <codex|claude|all>  配置目标（默认: all）"
    echo "  -k, --key <key>                  直接通过参数传入 Bark key（默认读取 .env 的 BARK_KEY）"
    echo "  -u, --uninstall                  卸载全局 Bark MCP，不需要 Bark key"
    echo "  -f, --force                      安装时覆盖已有 bark MCP"
    echo "  -h, --help                       显示此帮助信息"
    echo ""
    echo "说明:"
    echo "  写入全局配置的真实 URL 会包含 Bark key；日志仅显示脱敏 URL。"
    echo "  通过 -k 传入 key 可能进入 shell history，通常优先使用 .env。"
    echo ""
    echo "示例:"
    echo "  $0                               # 配置 Codex + Claude Code"
    echo "  $0 -t codex -f                   # 仅重写 Codex 配置"
    echo "  $0 -u                            # 卸载 Codex + Claude Code 的 Bark MCP"
    echo "  $0 -f -k xxxxxxxxxxxxx           # 使用参数传入 key 并强制重写"
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -t|--target)
            ws_require_value "$1" "${2:-}"
            TARGET="$2"
            shift 2
            ;;
        -k|--key)
            ws_require_value "$1" "${2:-}"
            BARK_KEY_ARG="$2"
            shift 2
            ;;
        -u|--uninstall)
            ACTION="uninstall"
            shift
            ;;
        -f|--force)
            FORCE=true
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

# ---- 环境检查 ----
case "$TARGET" in
    codex|claude|all) ;;
    *)
        ws_log_error "--target 仅支持 codex、claude 或 all"
        exit 1
        ;;
esac

if [[ "$ACTION" == "install" ]]; then
    ws_load_env

    if [[ -n "$BARK_KEY_ARG" ]]; then
        BARK_KEY_VALUE="$BARK_KEY_ARG"
        BARK_KEY_SOURCE="命令行参数"
    elif [[ -n "${BARK_KEY:-}" ]]; then
        BARK_KEY_VALUE="$BARK_KEY"
        BARK_KEY_SOURCE=".env"
    else
        ws_log_error "未设置 Bark Key，请在 .env 中配置 BARK_KEY，或使用 -k | --key 传入"
        exit 1
    fi

    BARK_MCP_URL="https://api.day.app/mcp/${BARK_KEY_VALUE}"
elif [[ -n "$BARK_KEY_ARG" ]]; then
    ws_log_warn "卸载模式会忽略 -k | --key"
fi

if [[ "$TARGET" == "codex" || "$TARGET" == "all" ]]; then
    ws_require_commands codex
fi

if [[ "$TARGET" == "claude" || "$TARGET" == "all" ]]; then
    ws_require_commands claude
fi

sanitize_output() {
    local text="$1"
    if [[ -n "${BARK_KEY_VALUE:-}" ]]; then
        text="${text//$BARK_KEY_VALUE/<BARK_KEY>}"
    fi
    echo "$text"
}

run_safely() {
    local label="$1"
    local output
    shift

    if ! output="$("$@" 2>&1)"; then
        ws_log_error "$label 失败"
        sanitize_output "$output"
        exit 1
    fi
}

codex_mcp_exists() {
    codex mcp get bark --json &>/dev/null
}

restore_codex_config() {
    local config_file="$1"
    local backup_file="$2"
    local config_existed="$3"
    local output

    if [[ "$config_existed" == true ]]; then
        if ! output="$(cp -p -- "$backup_file" "$config_file" 2>&1)"; then
            ws_log_error "恢复 Codex 配置失败；原配置备份保留在: $backup_file"
            sanitize_output "$output"
            return 1
        fi
    elif ! output="$(rm -f -- "$config_file" 2>&1)"; then
        ws_log_error "清理 Codex 配置失败"
        sanitize_output "$output"
        return 1
    fi

    if ! output="$(rm -f -- "$backup_file" 2>&1)"; then
        ws_log_error "清理 Codex 配置备份失败；备份保留在: $backup_file"
        sanitize_output "$output"
        return 1
    fi

    ws_log_warn "已恢复安装前的 Codex 配置"
}

abort_codex_configuration() {
    local label="$1"
    local output="$2"
    local config_file="$3"
    local backup_file="$4"
    local config_existed="$5"

    ws_log_error "$label"
    if [[ -n "$output" ]]; then
        sanitize_output "$output"
    fi
    restore_codex_config "$config_file" "$backup_file" "$config_existed" || true
    exit 1
}

claude_mcp_exists() {
    claude mcp get bark &>/dev/null
}

configure_codex() {
    ws_log_step "配置 Codex 全局 Bark MCP"
    local codex_config_file="${CODEX_HOME:-$HOME/.codex}/config.toml"
    local backup_file
    local bark_existed=false
    local config_existed=false
    local codex_status
    local output

    if codex_mcp_exists; then
        bark_existed=true
        if ! $FORCE; then
            ws_log_error "Codex 已存在名为 bark 的 MCP；如需覆盖请加 -f | --force"
            exit 1
        fi
    fi

    if ! backup_file="$(mktemp "${TMPDIR:-/tmp}/codex-bark-config.XXXXXX")"; then
        ws_log_error "创建 Codex 配置备份失败"
        exit 1
    fi
    if [[ -f "$codex_config_file" ]]; then
        config_existed=true
        if ! output="$(cp -p -- "$codex_config_file" "$backup_file" 2>&1)"; then
            ws_log_error "备份 Codex 配置失败"
            sanitize_output "$output"
            rm -f -- "$backup_file"
            exit 1
        fi
    fi

    if $bark_existed; then
        if ! output="$(codex mcp remove bark 2>&1)"; then
            abort_codex_configuration \
                "移除 Codex bark MCP 失败" "$output" \
                "$codex_config_file" "$backup_file" "$config_existed"
        fi
    fi

    if ! output="$(codex mcp add bark --url "$BARK_MCP_URL" 2>&1)"; then
        abort_codex_configuration \
            "添加 Codex bark MCP 失败" "$output" \
            "$codex_config_file" "$backup_file" "$config_existed"
    fi

    # Compatibility workaround for https://github.com/openai/codex/issues/22667:
    # a static, non-sensitive marker makes Codex skip broken OAuth discovery.
    if ! output="$({
        printf '\n'
        printf '# Compatibility workaround for https://github.com/openai/codex/issues/22667\n'
        printf '[mcp_servers.bark.http_headers]\n'
        printf 'Authorization = "%s"\n' "$CODEX_BARK_AUTH_HEADER"
    } 2>&1 >> "$codex_config_file")"; then
        abort_codex_configuration \
            "写入 Codex Bark 兼容 Header 失败" "$output" \
            "$codex_config_file" "$backup_file" "$config_existed"
    fi

    if ! codex_status="$(codex --strict-config mcp get bark --json 2>&1)"; then
        if [[ "$codex_status" == *'`--strict-config` is not supported for `codex mcp`'* ]]; then
            ws_log_warn "当前 Codex 不支持 mcp 子命令的 --strict-config，回退到普通 TOML 解析及 URL/Header 字段校验"
            if ! codex_status="$(codex mcp get bark --json 2>&1)"; then
                abort_codex_configuration \
                    "解析 Codex Bark MCP 配置失败" "$codex_status" \
                    "$codex_config_file" "$backup_file" "$config_existed"
            fi
        else
            abort_codex_configuration \
                "严格解析 Codex Bark MCP 配置失败" "$codex_status" \
                "$codex_config_file" "$backup_file" "$config_existed"
        fi
    fi
    if [[ "$codex_status" != *"\"url\": \"$BARK_MCP_URL\""* ||
          "$codex_status" != *"\"Authorization\": \"$CODEX_BARK_AUTH_HEADER\""* ]]; then
        abort_codex_configuration \
            "Codex Bark MCP URL 或兼容 Header 验证失败" "" \
            "$codex_config_file" "$backup_file" "$config_existed"
    fi

    if ! output="$(rm -f -- "$backup_file" 2>&1)"; then
        abort_codex_configuration \
            "清理 Codex 配置备份失败" "$output" \
            "$codex_config_file" "$backup_file" "$config_existed"
    fi

    ws_log_ok "Codex Bark MCP 已配置"
    ws_log_info "请在 VS Code 中执行 Reload Window 后再创建新会话"
}

configure_claude() {
    ws_log_step "配置 Claude Code user-scope Bark MCP"
    local claude_status

    if claude_mcp_exists; then
        if ! $FORCE; then
            ws_log_error "Claude Code 已存在名为 bark 的 MCP；如需覆盖请加 -f | --force"
            exit 1
        fi
        run_safely "移除 Claude Code bark MCP" claude mcp remove --scope user bark
    fi

    run_safely "添加 Claude Code bark MCP" \
        claude mcp add --scope user --transport http bark "$BARK_MCP_URL"

    if ! claude_status="$(claude mcp get bark 2>&1)"; then
        ws_log_error "Claude Code Bark MCP 已写入，但读取配置状态失败"
        sanitize_output "$claude_status"
        exit 1
    fi
    if [[ "$claude_status" == *"Failed to connect"* ]]; then
        ws_log_error "Claude Code 未能连接 Bark MCP，请检查 Bark key 或当前网络"
        exit 1
    fi
    ws_log_ok "Claude Code Bark MCP 已配置"
}

uninstall_codex() {
    ws_log_step "卸载 Codex 全局 Bark MCP"

    if codex_mcp_exists; then
        run_safely "移除 Codex bark MCP" codex mcp remove bark
        ws_log_ok "Codex Bark MCP 已卸载"
    else
        ws_log_skip "Codex bark MCP 不存在"
    fi
}

uninstall_claude() {
    ws_log_step "卸载 Claude Code user-scope Bark MCP"

    if claude_mcp_exists; then
        run_safely "移除 Claude Code bark MCP" claude mcp remove --scope user bark
        ws_log_ok "Claude Code Bark MCP 已卸载"
    else
        ws_log_skip "Claude Code bark MCP 不存在"
    fi
}

# ---- 主逻辑 ----
echo "============================================"
if [[ "$ACTION" == "install" ]]; then
    echo " 配置 Bark MCP"
else
    echo " 卸载 Bark MCP"
fi
echo "============================================"
echo ""
ws_log_info "操作: $ACTION"
ws_log_info "目标: $TARGET"
if [[ "$ACTION" == "install" ]]; then
    ws_log_info "URL: $SAFE_BARK_MCP_URL"
    ws_log_info "Bark key 来源: $BARK_KEY_SOURCE"
fi
echo ""

if [[ "$ACTION" == "install" ]]; then
    if [[ "$TARGET" == "codex" || "$TARGET" == "all" ]]; then
        configure_codex
    fi

    if [[ "$TARGET" == "claude" || "$TARGET" == "all" ]]; then
        configure_claude
    fi
else
    if [[ "$TARGET" == "codex" || "$TARGET" == "all" ]]; then
        uninstall_codex
    fi

    if [[ "$TARGET" == "claude" || "$TARGET" == "all" ]]; then
        uninstall_claude
    fi
fi

echo ""
if [[ "$ACTION" == "install" ]]; then
    ws_log_ok "Bark MCP 配置流程完成"
else
    ws_log_ok "Bark MCP 卸载流程完成"
fi

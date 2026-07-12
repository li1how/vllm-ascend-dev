#!/bin/bash
# ============================================================
# 配置/卸载全局 Bark MCP
#
# 使用 Bark 官方 HTTP MCP，为 Codex 和 Claude Code 配置全局 Bark MCP。
# 默认从 .env 读取 BARK_KEY，并把实际 key 写入全局 MCP URL。
# 默认优先使用 codex / claude CLI；CLI 不存在时回退配置文件 helper。
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
CONDA_ENV="vllm-ascend-dev"
BARK_MCP_CONFIG_HELPER="$SCRIPT_DIR/scripts/lib/bark_mcp_config_helper.py"
CODEX_BACKEND=""
CLAUDE_BACKEND=""
HELPER_REQUIRED=false

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
    echo "  优先使用 codex / claude CLI；对应 CLI 不存在时回退 Python helper。"
    echo "  helper 直接修改 ~/.codex/config.toml 或 ~/.claude.json，需要 Python 3.11+。"
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
    if ws_command_exists codex; then
        CODEX_BACKEND="cli"
    else
        CODEX_BACKEND="helper"
        HELPER_REQUIRED=true
    fi
fi

if [[ "$TARGET" == "claude" || "$TARGET" == "all" ]]; then
    if ws_command_exists claude; then
        CLAUDE_BACKEND="cli"
    else
        CLAUDE_BACKEND="helper"
        HELPER_REQUIRED=true
    fi
fi

if $HELPER_REQUIRED; then
    ws_select_python_env "$CONDA_ENV"
    ws_require_python_module "tomllib" "请使用 Python 3.11 或更高版本"
    if [[ ! -f "$BARK_MCP_CONFIG_HELPER" ]]; then
        ws_log_error "缺少 Bark MCP 配置 helper: $BARK_MCP_CONFIG_HELPER"
        exit 1
    fi
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

claude_mcp_exists() {
    claude mcp get bark &>/dev/null
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

abort_codex_cli_configuration() {
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

configure_codex_with_cli() {
    ws_log_step "配置 Codex 全局 Bark MCP（CLI）"
    local config_file="${CODEX_HOME:-$HOME/.codex}/config.toml"
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
    if [[ -f "$config_file" ]]; then
        config_existed=true
        if ! output="$(cp -p -- "$config_file" "$backup_file" 2>&1)"; then
            ws_log_error "备份 Codex 配置失败"
            sanitize_output "$output"
            rm -f -- "$backup_file"
            exit 1
        fi
    fi

    if $bark_existed; then
        if ! output="$(codex mcp remove bark 2>&1)"; then
            abort_codex_cli_configuration \
                "移除 Codex bark MCP 失败" "$output" \
                "$config_file" "$backup_file" "$config_existed"
        fi
    fi

    if ! output="$(codex mcp add bark --url "$BARK_MCP_URL" 2>&1)"; then
        abort_codex_cli_configuration \
            "添加 Codex bark MCP 失败" "$output" \
            "$config_file" "$backup_file" "$config_existed"
    fi

    if ! output="$({
        printf '\n'
        printf '# Compatibility workaround for https://github.com/openai/codex/issues/22667\n'
        printf '[mcp_servers.bark.http_headers]\n'
        printf 'Authorization = "%s"\n' "$CODEX_BARK_AUTH_HEADER"
    } 2>&1 >> "$config_file")"; then
        abort_codex_cli_configuration \
            "写入 Codex Bark 兼容 Header 失败" "$output" \
            "$config_file" "$backup_file" "$config_existed"
    fi

    if ! codex_status="$(codex mcp get bark --json 2>&1)"; then
        abort_codex_cli_configuration \
            "解析 Codex Bark MCP 配置失败" "$codex_status" \
            "$config_file" "$backup_file" "$config_existed"
    fi
    if [[ "$codex_status" != *"\"url\": \"$BARK_MCP_URL\""* ||
          "$codex_status" != *"\"Authorization\": \"$CODEX_BARK_AUTH_HEADER\""* ]]; then
        abort_codex_cli_configuration \
            "Codex Bark MCP URL 或兼容 Header 验证失败" "" \
            "$config_file" "$backup_file" "$config_existed"
    fi

    if ! output="$(chmod 600 "$config_file" 2>&1)"; then
        abort_codex_cli_configuration \
            "收紧 Codex 配置文件权限失败" "$output" \
            "$config_file" "$backup_file" "$config_existed"
    fi

    if ! output="$(rm -f -- "$backup_file" 2>&1)"; then
        abort_codex_cli_configuration \
            "清理 Codex 配置备份失败" "$output" \
            "$config_file" "$backup_file" "$config_existed"
    fi

    ws_log_ok "Codex Bark MCP 已通过 CLI 配置"
}

configure_claude_with_cli() {
    ws_log_step "配置 Claude Code user-scope Bark MCP（CLI）"
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

    if [[ -f "$HOME/.claude.json" ]]; then
        run_safely "收紧 Claude Code 配置文件权限" chmod 600 "$HOME/.claude.json"
    fi
    ws_log_ok "Claude Code Bark MCP 已通过 CLI 配置"
}

uninstall_codex_with_cli() {
    ws_log_step "卸载 Codex 全局 Bark MCP（CLI）"

    if codex_mcp_exists; then
        run_safely "移除 Codex bark MCP" codex mcp remove bark
        ws_log_ok "Codex Bark MCP 已通过 CLI 卸载"
    else
        ws_log_skip "Codex bark MCP 不存在"
    fi
}

uninstall_claude_with_cli() {
    ws_log_step "卸载 Claude Code user-scope Bark MCP（CLI）"

    if claude_mcp_exists; then
        run_safely "移除 Claude Code bark MCP" claude mcp remove --scope user bark
        ws_log_ok "Claude Code Bark MCP 已通过 CLI 卸载"
    else
        ws_log_skip "Claude Code bark MCP 不存在"
    fi
}

edit_config_file() {
    local config_kind="$1"
    local config_file="$2"
    local output
    local status
    local helper_args=("$config_kind" "$ACTION" "$config_file")

    if $FORCE; then
        helper_args+=("--force")
    fi

    if output="$(
        BARK_MCP_URL_VALUE="$BARK_MCP_URL" \
        CODEX_BARK_AUTH_HEADER_VALUE="$CODEX_BARK_AUTH_HEADER" \
        "$PYTHON_BIN" "$BARK_MCP_CONFIG_HELPER" "${helper_args[@]}" 2>&1
    )"; then
        echo "$output"
        return 0
    else
        status=$?
        if [[ $status -eq 3 ]]; then
            return 3
        fi
        sanitize_output "$output" >&2
        return "$status"
    fi
}

configure_codex_with_helper() {
    ws_log_step "配置 Codex 全局 Bark MCP（helper）"
    local config_file="${CODEX_HOME:-$HOME/.codex}/config.toml"
    local result

    if result="$(edit_config_file "codex" "$config_file")"; then
        ws_log_ok "Codex Bark MCP 已写入: $config_file"
    elif [[ $? -eq 3 ]]; then
        ws_log_error "Codex 已存在名为 bark 的 MCP；如需覆盖请加 -f | --force"
        exit 1
    else
        ws_log_error "修改 Codex Bark MCP 配置失败"
        exit 1
    fi
}

configure_claude_with_helper() {
    ws_log_step "配置 Claude Code user-scope Bark MCP（helper）"
    local config_file="$HOME/.claude.json"
    local result

    if result="$(edit_config_file "claude" "$config_file")"; then
        ws_log_ok "Claude Code Bark MCP 已写入: $config_file"
    elif [[ $? -eq 3 ]]; then
        ws_log_error "Claude Code 已存在名为 bark 的 MCP；如需覆盖请加 -f | --force"
        exit 1
    else
        ws_log_error "修改 Claude Code Bark MCP 配置失败"
        exit 1
    fi
}

uninstall_codex_with_helper() {
    ws_log_step "卸载 Codex 全局 Bark MCP（helper）"
    local config_file="${CODEX_HOME:-$HOME/.codex}/config.toml"
    local result

    if ! result="$(edit_config_file "codex" "$config_file")"; then
        ws_log_error "修改 Codex Bark MCP 配置失败"
        exit 1
    fi
    if [[ "$result" == "absent" ]]; then
        ws_log_skip "Codex bark MCP 不存在"
    else
        ws_log_ok "Codex Bark MCP 已卸载"
    fi
}

uninstall_claude_with_helper() {
    ws_log_step "卸载 Claude Code user-scope Bark MCP（helper）"
    local config_file="$HOME/.claude.json"
    local result

    if ! result="$(edit_config_file "claude" "$config_file")"; then
        ws_log_error "修改 Claude Code Bark MCP 配置失败"
        exit 1
    fi
    if [[ "$result" == "absent" ]]; then
        ws_log_skip "Claude Code bark MCP 不存在"
    else
        ws_log_ok "Claude Code Bark MCP 已卸载"
    fi
}

configure_codex() {
    if [[ "$CODEX_BACKEND" == "cli" ]]; then
        configure_codex_with_cli
    else
        configure_codex_with_helper
    fi
}

configure_claude() {
    if [[ "$CLAUDE_BACKEND" == "cli" ]]; then
        configure_claude_with_cli
    else
        configure_claude_with_helper
    fi
}

uninstall_codex() {
    if [[ "$CODEX_BACKEND" == "cli" ]]; then
        uninstall_codex_with_cli
    else
        uninstall_codex_with_helper
    fi
}

uninstall_claude() {
    if [[ "$CLAUDE_BACKEND" == "cli" ]]; then
        uninstall_claude_with_cli
    else
        uninstall_claude_with_helper
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
if [[ -n "$CODEX_BACKEND" ]]; then
    ws_log_info "Codex 配置方式: $CODEX_BACKEND"
fi
if [[ -n "$CLAUDE_BACKEND" ]]; then
    ws_log_info "Claude Code 配置方式: $CLAUDE_BACKEND"
fi
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
    ws_log_info "请在 VS Code 中执行 Reload Window 后再创建新会话"
else
    ws_log_ok "Bark MCP 卸载流程完成"
fi

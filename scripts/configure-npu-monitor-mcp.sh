#!/bin/bash
# ============================================================
# 配置/卸载全局 NPU Monitor MCP
#
# 从 .env 读取连接地址和 token，为 Codex 和 Claude Code 配置全局 MCP。
# 优先使用对应 CLI；CLI 不存在时回退配置文件 helper。
#
# 用法:
#   ./scripts/configure-npu-monitor-mcp.sh                  # 配置两个客户端
#   ./scripts/configure-npu-monitor-mcp.sh -t codex         # 仅配置 Codex
#   ./scripts/configure-npu-monitor-mcp.sh -f               # 替换已有配置
#   ./scripts/configure-npu-monitor-mcp.sh -u               # 卸载配置
# ============================================================

set -e
# shellcheck source=./lib/common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/lib" && pwd)/common.sh"
ws_enter_workspace

# ---- 默认配置 ----
ACTION="install"
TARGET="all"
FORCE=false
CONDA_ENV="vllm-ascend-dev"
MCP_CONFIG_HELPER="$SCRIPT_DIR/scripts/lib/npu_monitor_mcp_config.py"

# ---- 参数解析 ----
print_help() {
    echo "用法: $0 [选项]"
    echo ""
    echo "选项:"
    echo "  -t, --target <codex|claude|all>  配置目标（默认: all）"
    echo "  -f, --force                    替换已有 npu-monitor 配置"
    echo "  -u, --uninstall                卸载全局配置，不读取 .env"
    echo "  -h, --help                     显示此帮助信息"
    echo ""
    echo "说明:"
    echo "  从工作区根目录 .env 读取 NPU_MONITOR_MCP_URL 和 NPU_MONITOR_MCP_TOKEN。"
    echo "  .env 覆盖同名环境变量；未定义的变量保留环境值。"
    echo "  配置持久化到用户配置文件，需要 Python 3.11+。"
    echo "  完成后重启客户端；本脚本不验证连接或扫描节点。"
    echo ""
    echo "示例:"
    echo "  $0                     # 配置 Codex + Claude Code"
    echo "  $0 -t codex -f         # 仅替换 Codex 配置"
    echo "  $0 -u                  # 卸载两个客户端的配置"
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -t|--target)
            ws_require_value "$1" "${2:-}"
            TARGET="$2"
            shift 2
            ;;
        -f|--force)
            FORCE=true
            shift
            ;;
        -u|--uninstall)
            ACTION="uninstall"
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

    if [[ -z "${NPU_MONITOR_MCP_URL:-}" || -z "${NPU_MONITOR_MCP_TOKEN:-}" ]]; then
        ws_log_error "请在 .env 中填写 NPU_MONITOR_MCP_URL 和 NPU_MONITOR_MCP_TOKEN"
        exit 1
    fi
    export NPU_MONITOR_MCP_URL NPU_MONITOR_MCP_TOKEN
fi

ws_select_python_env "$CONDA_ENV"
ws_require_python_module "tomllib" "请使用 Python 3.11 或更高版本"

# ---- 主逻辑 ----
helper_args=("$ACTION" --target "$TARGET")
if $FORCE; then
    helper_args+=(--force)
fi

ws_log_info "操作: $ACTION"
ws_log_info "目标: $TARGET"
"$PYTHON_BIN" "$MCP_CONFIG_HELPER" "${helper_args[@]}"

ws_log_ok "配置操作完成；请重启对应客户端。尚未进行连接验证。"

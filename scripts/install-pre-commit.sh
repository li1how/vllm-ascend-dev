#!/bin/bash
# ============================================================
# 安装 vllm-ascend pre-commit 环境并验证格式化
#
# 安装 actionlint 构建所需的系统 Go 和项目 lint 依赖，运行
# format.sh 验证所有 hooks；验证通过后再安装 Git hooks。
#
# 用法:
#   ./scripts/install-pre-commit.sh             # 安装、格式化并启用 Git hooks
#   ./scripts/install-pre-commit.sh -h | --help # 查看帮助
# ============================================================

set -e
# shellcheck source=./lib/common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/lib" && pwd)/common.sh"
ws_enter_workspace

# ---- 默认配置 ----
CONDA_ENV="vllm-ascend-dev"
VLLM_ASCEND_DIR="$SCRIPT_DIR/vllm-ascend"
MIN_GO_MAJOR=1
MIN_GO_MINOR=18
PYTHON_BIN=""

# ---- 参数解析 ----
print_help() {
    echo "用法: $0 [选项]"
    echo ""
    echo "安装 Go 和 vllm-ascend lint 依赖，运行 format.sh，通过后启用 Git hooks。"
    echo ""
    echo "选项:"
    echo "  -h, --help  显示此帮助信息"
    echo ""
    echo "注意:"
    echo "  format.sh 可能直接修改 vllm-ascend 中的文件。"
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

install_go() {
    local -a privilege=()

    if ws_command_exists go; then
        ws_log_skip "Go 已安装: $(go version)"
        return
    fi

    if (( EUID != 0 )); then
        if ws_command_exists sudo; then
            privilege=(sudo)
        else
            ws_log_error "安装 Go 需要 root 权限，请用 root 运行或安装 sudo"
            exit 1
        fi
    fi

    ws_log_step "安装系统 Go..."
    if ws_command_exists apt-get; then
        "${privilege[@]}" apt-get update
        "${privilege[@]}" apt-get install -y golang-go
    elif ws_command_exists dnf; then
        "${privilege[@]}" dnf install -y golang
    elif ws_command_exists yum; then
        "${privilege[@]}" yum install -y golang
    else
        ws_log_error "不支持的包管理器，需要 apt-get、dnf 或 yum"
        exit 1
    fi
}

validate_go_version() {
    local version_output
    local go_major
    local go_minor

    if ! ws_command_exists go; then
        ws_log_error "Go 安装完成后仍无法找到 go 命令"
        exit 1
    fi

    version_output="$(go version)"
    if [[ ! "$version_output" =~ go([0-9]+)\.([0-9]+) ]]; then
        ws_log_error "无法解析 Go 版本: $version_output"
        exit 1
    fi

    go_major="${BASH_REMATCH[1]}"
    go_minor="${BASH_REMATCH[2]}"
    if (( go_major < MIN_GO_MAJOR ||
          (go_major == MIN_GO_MAJOR && go_minor < MIN_GO_MINOR) )); then
        ws_log_error "Go 版本过低: $version_output，需要 Go ${MIN_GO_MAJOR}.${MIN_GO_MINOR} 或更高版本"
        exit 1
    fi
    ws_log_ok "$version_output"
}

# ---- 主逻辑 ----
install_go
validate_go_version

ws_select_python_env "$CONDA_ENV"

cd "$VLLM_ASCEND_DIR"

ws_log_step "安装 vllm-ascend lint 依赖..."
"$PYTHON_BIN" -m pip install -r requirements-lint.txt

ws_log_step "运行 format.sh 验证 pre-commit 环境..."
bash format.sh
ws_log_ok "format.sh 执行通过"

ws_log_step "安装 Git hooks..."
"$PYTHON_BIN" -m pre_commit install
ws_log_ok "vllm-ascend pre-commit 环境已安装并验证"

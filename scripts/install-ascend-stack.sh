#!/bin/bash
# ============================================================
# 安装 Ascend 依赖栈
#
# 从手动指定的包目录按项安装 CANN/NNAL、torch_npu、triton_ascend。
# cann 项会替换 /usr/local/Ascend 下现有 CANN toolkit、ops 和 NNAL。
#
# 用法:
#   ./scripts/install-ascend-stack.sh -p <dir|version> -i cann -y
#   ./scripts/install-ascend-stack.sh -p <dir|version> -i cann,torch_npu,triton_ascend --dry-run
#   ./scripts/install-ascend-stack.sh -h | --help
# ============================================================

set -euo pipefail
# shellcheck source=./lib/common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/lib" && pwd)/common.sh"
ws_enter_workspace

# ---- 默认配置 ----
CONDA_ENV="vllm-ascend-dev"
PKG_DIR=""
ITEMS=""
YES=false
DRY_RUN=false
PYTHON_BIN=""
LOG_DIR=""

DO_CANN=false
DO_TORCH_NPU=false
DO_TRITON_ASCEND=false

CANN_TOOLKIT_RUN=""
CANN_OPS_RUN=""
CANN_NNAL_RUN=""
TORCH_NPU_WHL=""
TRITON_ASCEND_WHL=""

# ---- 参数解析 ----
print_help() {
    echo "用法: $0 [选项]"
    echo ""
    echo "从指定包目录按项安装 Ascend 依赖。"
    echo "执行顺序固定为: cann -> torch_npu -> triton_ascend。"
    echo ""
    echo "选项:"
    echo "  -p, --pkg-dir <dir|version>  包目录或 pkg/ 下版本名，例如 26.1.0.B081、pkg/26.1.0.B081、/abs/pkgdir（必填）"
    echo "  -i, --items <list>   安装项，逗号分隔: cann,torch_npu,triton_ascend,all（必填）"
    echo "  -y, --yes            跳过真实安装前的确认提示"
    echo "  -n, --dry-run        只检查包并打印命令，不执行安装"
    echo "  -h, --help           显示此帮助信息"
    echo ""
    echo "示例:"
    echo "  $0 -p 26.1.0.B081 -i cann -y"
    echo "  $0 -p /data/pkg/26.1.0.B081 -i cann --dry-run"
    echo "  $0 -p pkg/26.1.0.B081 -i cann -y"
    echo "  $0 -p pkg/26.1.0.B081 -i torch_npu -y"
    echo "  $0 -p pkg/26.1.0.B081 -i triton_ascend -y"
    echo "  $0 -p pkg/26.1.0.B081 -i all --dry-run"
    echo "  $0 -p pkg/26.1.0.B081 -i cann,torch_npu,triton_ascend --dry-run"
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -p|--pkg-dir)
            ws_require_value "$1" "${2:-}"
            PKG_DIR="$2"
            shift 2
            ;;
        -i|--items)
            ws_require_value "$1" "${2:-}"
            ITEMS="$2"
            shift 2
            ;;
        -y|--yes)
            YES=true
            shift
            ;;
        -n|--dry-run)
            DRY_RUN=true
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

# ---- 工具函数 ----
format_command() {
    local arg
    local escaped
    local output=""

    for arg in "$@"; do
        printf -v escaped "%q" "$arg"
        output+="${output:+ }$escaped"
    done
    echo "$output"
}

find_one_file() {
    local label="$1"
    local search_dir="$2"
    local pattern="$3"
    local -a matches=()
    local file

    if [[ ! -d "$search_dir" ]]; then
        ws_log_error "$label 目录不存在: $search_dir"
        exit 1
    fi

    mapfile -d '' matches < <(find "$search_dir" -maxdepth 1 -type f -name "$pattern" -print0 | sort -z)
    if (( ${#matches[@]} == 0 )); then
        ws_log_error "未找到 $label: $search_dir/$pattern"
        exit 1
    fi
    if (( ${#matches[@]} > 1 )); then
        ws_log_error "$label 匹配到多个文件，请保留一个明确版本:"
        for file in "${matches[@]}"; do
            echo "  $file" >&2
        done
        exit 1
    fi

    echo "${matches[0]}"
}

resolve_pkg_dir() {
    local input="$1"
    local candidate
    local -a candidates=()

    if [[ "$input" == /* ]]; then
        candidates+=("$input")
    else
        candidates+=("$(ws_resolve_path "$input")")
        if [[ "$input" != pkg/* ]]; then
            candidates+=("$SCRIPT_DIR/pkg/$input")
        fi
    fi

    for candidate in "${candidates[@]}"; do
        if [[ -d "$candidate" ]]; then
            echo "$candidate"
            return
        fi
    done

    ws_log_error "包目录不存在: $input"
    echo "  尝试过:" >&2
    for candidate in "${candidates[@]}"; do
        echo "    $candidate" >&2
    done
    exit 1
}

enable_item() {
    case "$1" in
        cann)
            DO_CANN=true
            ;;
        torch_npu)
            DO_TORCH_NPU=true
            ;;
        triton_ascend)
            DO_TRITON_ASCEND=true
            ;;
        *)
            ws_log_error "非法安装项: $1（仅支持 cann,torch_npu,triton_ascend,all）"
            exit 1
            ;;
    esac
}

selected_items() {
    local -a selected=()

    $DO_CANN && selected+=(cann)
    $DO_TORCH_NPU && selected+=(torch_npu)
    $DO_TRITON_ASCEND && selected+=(triton_ascend)

    echo "${selected[*]}"
}

check_runtime_requirements() {
    ws_require_commands find sort basename date mkdir chmod tail rm unlink

    if $DO_CANN && ! $DRY_RUN && (( EUID != 0 )); then
        ws_log_error "安装 cann 会写入 /usr/local/Ascend，请使用 root 用户执行，或先用 --dry-run 预览"
        exit 1
    fi
}

run_logged() {
    local description="$1"
    local work_dir="$2"
    local log_file="$3"
    shift 3

    ws_log_step "$description"
    if $DRY_RUN; then
        echo "[DRY-RUN] cd $(printf "%q" "$work_dir") && $(format_command "$@") &> $(printf "%q" "$log_file")"
        return
    fi

    mkdir -p "$(dirname "$log_file")"
    if (cd "$work_dir" && "$@" &> "$log_file"); then
        ws_log_ok "$description 完成，日志: $log_file"
    else
        ws_log_error "$description 失败，日志: $log_file"
        tail -80 "$log_file" || true
        exit 1
    fi
}

source_env_file() {
    local env_file="$1"
    if [[ ! -f "$env_file" ]]; then
        ws_log_error "环境文件不存在: $env_file"
        exit 1
    fi

    set +u
    # shellcheck disable=SC1090
    source "$env_file"
    set -u
}

parse_items() {
    local raw="${ITEMS//[[:space:]]/}"
    local -a requested=()
    local item

    if [[ -z "$raw" ]]; then
        ws_log_error "安装项不能为空: $ITEMS"
        exit 1
    fi

    IFS=',' read -r -a requested <<< "$raw"
    for item in "${requested[@]}"; do
        case "$item" in
            all)
                enable_item cann
                enable_item torch_npu
                enable_item triton_ascend
                ;;
            "")
                ws_log_error "安装项不能为空: $ITEMS"
                exit 1
                ;;
            *)
                enable_item "$item"
                ;;
        esac
    done
}

validate_python() {
    ws_select_python_env "$CONDA_ENV"
    if ! "$PYTHON_BIN" -m pip --version &>/dev/null; then
        ws_log_error "当前 Python 无法运行 pip: $PYTHON_BIN -m pip"
        exit 1
    fi
}

discover_packages() {
    if $DO_CANN; then
        CANN_TOOLKIT_RUN="$(find_one_file "CANN toolkit 安装包" "$PKG_DIR" "Ascend-cann-toolkit_*.run")"
        CANN_OPS_RUN="$(find_one_file "CANN ops 安装包" "$PKG_DIR" "Ascend-cann-*-ops_*.run")"
        CANN_NNAL_RUN="$(find_one_file "CANN NNAL 安装包" "$PKG_DIR" "Ascend-cann-nnal_*.run")"
    fi

    if $DO_TORCH_NPU; then
        TORCH_NPU_WHL="$(find_one_file "torch_npu wheel" "$PKG_DIR" "torch_npu-*.whl")"
    fi

    if $DO_TRITON_ASCEND; then
        TRITON_ASCEND_WHL="$(find_one_file "triton_ascend wheel" "$PKG_DIR" "triton_ascend-*.whl")"
    fi
}

print_summary() {
    echo "============================================"
    echo " Ascend 依赖按项安装"
    echo "============================================"
    echo "  包目录: $PKG_DIR"
    echo "  安装项: $(selected_items)"
    echo "  模式:   $($DRY_RUN && echo "dry-run" || echo "真实安装")"
    if [[ -n "${PYTHON_BIN:-}" ]]; then
        echo "  Python: $("$PYTHON_BIN" -c 'import sys; print(sys.executable)')"
    fi
    if ! $DRY_RUN; then
        echo "  日志:   $LOG_DIR"
    else
        echo "  日志:   $LOG_DIR（dry-run 不创建）"
    fi
    echo "============================================"

    if $DO_CANN; then
        echo "  CANN toolkit: $(basename "$CANN_TOOLKIT_RUN")"
        echo "  CANN ops:     $(basename "$CANN_OPS_RUN")"
        echo "  CANN NNAL:    $(basename "$CANN_NNAL_RUN")"
    fi
    $DO_TORCH_NPU && echo "  torch_npu:    $(basename "$TORCH_NPU_WHL")"
    $DO_TRITON_ASCEND && echo "  triton_ascend: $(basename "$TRITON_ASCEND_WHL")"
    echo ""
}

confirm_execution() {
    local answer

    if $DRY_RUN; then
        return
    fi
    if $YES; then
        return
    fi

    ws_log_warn "真实安装会修改当前 Python 环境；安装 cann 会替换 /usr/local/Ascend 下现有 CANN/NNAL。"
    if ! read -r -p "确认继续请输入 yes: " answer; then
        ws_log_error "无法读取确认输入，请传入 -y | --yes 或使用 --dry-run"
        exit 1
    fi
    if [[ "$answer" != "yes" ]]; then
        ws_log_error "用户取消安装"
        exit 1
    fi
}

install_cann() {
    local ops_name
    local -a old_cann_paths=(
        /usr/local/Ascend/ascend-toolkit
        /usr/local/Ascend/nnal
        /usr/local/Ascend/cann-*/
    )

    ws_log_step "清理旧 CANN/NNAL..."
    if $DRY_RUN; then
        echo "[DRY-RUN] $(format_command rm -fr "${old_cann_paths[@]}")"
        echo "[DRY-RUN] $(format_command unlink /usr/local/Ascend/cann) || true"
        echo "[DRY-RUN] $(format_command mkdir -p /usr/local/Ascend)"
        echo "[DRY-RUN] $(format_command chmod 755 /usr /usr/local /usr/local/Ascend)"
    else
        rm -fr "${old_cann_paths[@]}"
        unlink /usr/local/Ascend/cann 2>/dev/null || true
        mkdir -p /usr/local/Ascend
        chmod 755 /usr /usr/local /usr/local/Ascend
    fi

    if $DRY_RUN; then
        echo "[DRY-RUN] $(format_command chmod 700 "$CANN_TOOLKIT_RUN" "$CANN_OPS_RUN" "$CANN_NNAL_RUN")"
    else
        chmod 700 "$CANN_TOOLKIT_RUN" "$CANN_OPS_RUN" "$CANN_NNAL_RUN"
    fi

    run_logged "安装 CANN toolkit" "$PKG_DIR" "$LOG_DIR/cann_toolkit.log" \
        "./$(basename "$CANN_TOOLKIT_RUN")" --full --quiet

    ops_name="$(basename "$CANN_OPS_RUN")"
    run_logged "安装 CANN ops: $ops_name" "$PKG_DIR" "$LOG_DIR/${ops_name%.run}.log" \
        "./$ops_name" --install --quiet

    if $DRY_RUN; then
        echo "[DRY-RUN] source /usr/local/Ascend/ascend-toolkit/set_env.sh"
    else
        source_env_file "/usr/local/Ascend/ascend-toolkit/set_env.sh"
    fi

    run_logged "安装 CANN NNAL" "$PKG_DIR" "$LOG_DIR/cann_nnal.log" \
        "./$(basename "$CANN_NNAL_RUN")" --install --quiet --torch_atb
}

install_torch_npu() {
    run_logged "安装 torch_npu" "$PKG_DIR" "$LOG_DIR/torch_npu.log" \
        "$PYTHON_BIN" -m pip install "$TORCH_NPU_WHL" --force-reinstall
}

install_triton_ascend() {
    run_logged "安装 triton_ascend" "$PKG_DIR" "$LOG_DIR/triton_ascend.log" \
        "$PYTHON_BIN" -m pip install "$TRITON_ASCEND_WHL" --force-reinstall
}

# ---- 环境检查 ----
if [[ -z "$PKG_DIR" ]]; then
    ws_log_error "缺少必填参数: -p | --pkg-dir <dir>"
    exit 1
fi
if [[ -z "$ITEMS" ]]; then
    ws_log_error "缺少必填参数: -i | --items <list>"
    exit 1
fi

PKG_DIR="$(resolve_pkg_dir "$PKG_DIR")"
parse_items
check_runtime_requirements
discover_packages

if $DO_TORCH_NPU || $DO_TRITON_ASCEND; then
    validate_python
fi
LOG_DIR="$SCRIPT_DIR/log/install-ascend-stack/$(date +%Y%m%d_%H%M%S)"
print_summary
confirm_execution

if ! $DRY_RUN; then
    mkdir -p "$LOG_DIR"
fi

# ---- 主逻辑 ----
if $DO_CANN; then
    install_cann
fi
if $DO_TORCH_NPU; then
    install_torch_npu
fi
if $DO_TRITON_ASCEND; then
    install_triton_ascend
fi

echo ""
ws_log_ok "Ascend 依赖安装流程完成"

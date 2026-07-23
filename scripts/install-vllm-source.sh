#!/bin/bash
# ============================================================
# 安装 vLLM 与 vLLM Ascend 源码
#
# 卸载当前 Python 环境中的 vllm / vllm-ascend 包，并从工作区
# vllm/ 与 vllm-ascend/ 目录执行 editable 安装。
#
# 用法:
#   ./scripts/install-vllm-source.sh                         # 完整重装 vllm + vllm-ascend
#   ./scripts/install-vllm-source.sh -s | --skip-uninstall    # 跳过卸载，仅安装源码
#   ./scripts/install-vllm-source.sh -v | --vllm-only         # 仅安装 vllm
#   ./scripts/install-vllm-source.sh -a | --ascend-only       # 仅安装 vllm-ascend
#   ./scripts/install-vllm-source.sh -c | --clean-build-cache # 安装前清理 vllm-ascend 构建缓存
#   ./scripts/install-vllm-source.sh -t /path/to/tmp           # 指定构建临时目录
#   ./scripts/install-vllm-source.sh -h | --help              # 查看帮助
# ============================================================

set -euo pipefail
# shellcheck source=./lib/common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/lib" && pwd)/common.sh"
ws_enter_workspace

# ---- 默认配置 ----
CONDA_ENV="vllm-ascend-dev"
PYTHON_BIN=""
SKIP_UNINSTALL=false
INSTALL_MODE="all"
CLEAN_BUILD_CACHE=false
VLLM_BUILD_REQUIREMENTS=(setuptools-rust)
BUILD_TMP_DIR="$SCRIPT_DIR/tmp"
BUILD_TMP_DIR_EXPLICIT=false
BUILD_TMP_SOURCE="工作区默认"
BUILD_TMP_ENV=()
MIN_BUILD_TMP_KB=$((512 * 1024))
ASCEND_BUILD_DIR="$SCRIPT_DIR/vllm-ascend/csrc/build"
ASCEND_BUILD_CACHE_SUMMARY="保留"
CANN_CACHE_KEYS=(
    CUSTOM_ASCEND_CANN_PACKAGE_PATH
    ASCEND_CMAKE_DIR
    ACL_INC_DIR
    C_SEC_INCLUDE
    METADEF_INC_DIR
    NNOPBASE_ACLNN_INC_DIR
    NNOPBASE_OPDEV_INC_DIR
    OPBASE_INC_DIR
    PLATFORM_INC_DIR
    RUNTIME_INC_DIR
    TILINGAPI_INC_DIR
    dlog_TRANSFORMER_INCLUDE_DIR
)

build_tmp_dir_is_usable() {
    local candidate="$1"
    local available_kb
    local mount_options

    if ! mkdir -p -- "$candidate" 2>/dev/null; then
        return 1
    fi
    if [[ ! -d "$candidate" || ! -w "$candidate" || ! -x "$candidate" ]]; then
        return 1
    fi

    # The generated CANN makeself installer executes install.sh from TMPDIR.
    # A noexec/readonly mount therefore cannot be used even if it has space.
    if ws_command_exists findmnt; then
        mount_options="$(findmnt -T "$candidate" -n -o OPTIONS 2>/dev/null | tail -n 1 || true)"
        case ",$mount_options," in
            *,noexec,*|*,ro,*)
                return 1
                ;;
        esac
    fi

    available_kb="$(df -Pk "$candidate" 2>/dev/null | awk 'NR == 2 { print $4 }')"
    [[ "$available_kb" =~ ^[0-9]+$ ]] || return 1
    (( available_kb >= MIN_BUILD_TMP_KB ))
}

select_build_tmp_dir() {
    local resolved_candidate

    resolved_candidate="$(ws_resolve_path "$BUILD_TMP_DIR")"
    if $BUILD_TMP_DIR_EXPLICIT; then
        BUILD_TMP_SOURCE="显式指定"
    else
        BUILD_TMP_SOURCE="工作区默认"
    fi

    if build_tmp_dir_is_usable "$resolved_candidate"; then
        BUILD_TMP_DIR="$resolved_candidate"
        # Pin pip and the nested CANN makeself installer to the same directory.
        BUILD_TMP_ENV=("TMPDIR=$BUILD_TMP_DIR")
        return 0
    fi

    ws_log_error "${BUILD_TMP_SOURCE}临时目录不可用或可用空间不足 512 MiB: $resolved_candidate"
    echo "  可通过 --tmp-dir <目录> 显式指定。"
    echo "  注意：CANN 自解压安装器不能使用 noexec 文件系统。"
    exit 1
}

normalize_path() {
    local path="$1"

    if ws_command_exists realpath; then
        realpath -m -- "$path"
    else
        readlink -m -- "$path"
    fi
}

resolve_current_cann_root() {
    local candidate=""
    local default_toolkit_dir
    local default_install_dir

    if [[ -n "${ASCEND_HOME_PATH:-}" ]]; then
        candidate="$ASCEND_HOME_PATH"
    elif [[ -n "${ASCEND_OPP_PATH:-}" ]]; then
        candidate="$(dirname -- "$ASCEND_OPP_PATH")"
    else
        if [[ "$(id -u)" == "0" ]]; then
            default_toolkit_dir="/usr/local/Ascend/ascend-toolkit/latest"
            default_install_dir="/usr/local/Ascend/latest"
        else
            default_toolkit_dir="${HOME}/Ascend/ascend-toolkit/latest"
            default_install_dir="${HOME}/Ascend/latest"
        fi

        if [[ -d "$default_toolkit_dir" ]]; then
            candidate="$default_toolkit_dir"
        elif [[ -d "$default_install_dir" ]]; then
            candidate="$default_install_dir"
        fi
    fi

    [[ -n "$candidate" && -d "$candidate" ]] || return 1
    normalize_path "$candidate"
}

read_cmake_cache_value() {
    local cache_file="$1"
    local key="$2"

    awk -v key="$key" '
        index($0, key ":") == 1 {
            sub(/^[^=]*=/, "")
            print
            exit
        }
    ' "$cache_file"
}

path_is_within() {
    local root="$1"
    local path="$2"

    [[ "$path" == "$root" || "$path" == "$root/"* ]]
}

warn_ascend_cache() {
    local current_cann_root="$1"
    shift

    ws_log_warn "vllm-ascend 构建缓存可能与当前 CANN 不兼容"
    if [[ -n "$current_cann_root" ]]; then
        echo "  当前 CANN: $current_cann_root"
    fi
    while [[ $# -gt 0 ]]; do
        echo "  - $1"
        shift
    done
    echo "  建议使用 --clean-build-cache 重新运行；本次不会自动删除缓存。"
}

inspect_ascend_build_cache() {
    local cache_file="$ASCEND_BUILD_DIR/CMakeCache.txt"
    local current_cann_root=""
    local key
    local value
    local cached_path
    local normalized_cached_path
    local found_paths=0
    local -a cached_paths=()
    local -a issues=()

    ASCEND_BUILD_CACHE_SUMMARY="保留"
    if [[ ! -e "$ASCEND_BUILD_DIR" && ! -L "$ASCEND_BUILD_DIR" ]]; then
        ASCEND_BUILD_CACHE_SUMMARY="不存在（无需清理）"
        return 0
    fi

    if [[ ! -r "$cache_file" ]]; then
        ASCEND_BUILD_CACHE_SUMMARY="保留（缓存缺失或不可读）"
        warn_ascend_cache "" "无法读取 $cache_file"
        return 0
    fi

    if ! current_cann_root="$(resolve_current_cann_root)"; then
        ASCEND_BUILD_CACHE_SUMMARY="保留（无法检测兼容性）"
        warn_ascend_cache "" "无法确定当前 CANN 根目录，请检查 ASCEND_HOME_PATH 或 ASCEND_OPP_PATH"
        return 0
    fi

    for key in "${CANN_CACHE_KEYS[@]}"; do
        value="$(read_cmake_cache_value "$cache_file" "$key" 2>/dev/null || true)"
        [[ -n "$value" ]] || continue

        if [[ "$value" == *-NOTFOUND ]]; then
            issues+=("$key=$value")
            continue
        fi

        IFS=';' read -r -a cached_paths <<< "$value"
        for cached_path in "${cached_paths[@]}"; do
            [[ -n "$cached_path" ]] || continue
            if [[ "$cached_path" != /* ]]; then
                issues+=("$key 使用了无法识别的非绝对路径: $cached_path")
                continue
            fi

            found_paths=$((found_paths + 1))
            normalized_cached_path="$(normalize_path "$cached_path")"
            if ! path_is_within "$current_cann_root" "$normalized_cached_path"; then
                issues+=("$key 指向其他 CANN: $cached_path")
            fi
        done
    done

    if (( found_paths == 0 )); then
        ASCEND_BUILD_CACHE_SUMMARY="保留（缓存无法解析）"
        warn_ascend_cache "$current_cann_root" "未在 $cache_file 中找到可识别的 CANN 路径"
    elif (( ${#issues[@]} > 0 )); then
        ASCEND_BUILD_CACHE_SUMMARY="保留（检测到可能失配）"
        warn_ascend_cache "$current_cann_root" "${issues[@]}"
    else
        ws_log_skip "vllm-ascend 构建缓存与当前 CANN 匹配: $current_cann_root"
    fi
}

clean_ascend_build_cache() {
    local expected_parent
    local actual_parent

    expected_parent="$(normalize_path "$SCRIPT_DIR/vllm-ascend/csrc")"
    actual_parent="$(normalize_path "$(dirname -- "$ASCEND_BUILD_DIR")")"
    if [[ "$actual_parent" != "$expected_parent" || "$(basename -- "$ASCEND_BUILD_DIR")" != "build" ]]; then
        ws_log_error "拒绝清理非预期目录: $ASCEND_BUILD_DIR"
        exit 1
    fi

    if [[ -e "$ASCEND_BUILD_DIR" || -L "$ASCEND_BUILD_DIR" ]]; then
        ws_log_step "清理 vllm-ascend 构建缓存: $ASCEND_BUILD_DIR"
        rm -rf -- "$ASCEND_BUILD_DIR"
        ASCEND_BUILD_CACHE_SUMMARY="已清理（显式指定）"
        ws_log_ok "vllm-ascend 构建缓存已清理"
    else
        ASCEND_BUILD_CACHE_SUMMARY="不存在（无需清理）"
        ws_log_skip "vllm-ascend 构建缓存不存在: $ASCEND_BUILD_DIR"
    fi
}

# ---- 参数解析 ----
print_help() {
    echo "用法: $0 [选项]"
    echo ""
    echo "卸载并从工作区源码安装 vllm / vllm-ascend。"
    echo ""
    echo "选项:"
    echo "  -s, --skip-uninstall  跳过卸载步骤，仅执行源码安装"
    echo "  -v, --vllm-only       仅处理 vllm"
    echo "  -a, --ascend-only     仅处理 vllm-ascend"
    echo "  -c, --clean-build-cache"
    echo "                        安装前清理 vllm-ascend 自定义算子构建缓存"
    echo "  -t, --tmp-dir <目录>  指定构建临时目录（至少需要 512 MiB）"
    echo "  -h, --help            显示此帮助信息"
    echo ""
    echo "示例:"
    echo "  $0                    # 卸载并安装 vllm + vllm-ascend"
    echo "  $0 -s                 # 跳过卸载，仅重新安装源码"
    echo "  $0 -v                 # 仅卸载并安装 vllm"
    echo "  $0 -a                 # 仅卸载并安装 vllm-ascend"
    echo "  $0 -a -s -c           # 清理构建缓存后重新安装 vllm-ascend"
    echo "  $0 -a -t /path/tmp    # 使用指定临时目录安装 vllm-ascend"
    echo ""
    echo "默认构建临时目录: $SCRIPT_DIR/tmp"
    echo ""
    echo "以下情况建议使用 --clean-build-cache:"
    echo "  - 切换了 CANN 版本或 CANN 安装路径不同的镜像"
    echo "  - CMake 报错仍引用当前镜像中不存在的旧 CANN 路径"
    echo "  - CMakeCache.txt 同时包含多个 CANN 版本或构建曾异常中断"
    echo "  - 大幅切换了包含 CMake / 自定义算子变更的 vllm-ascend 分支"
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -s|--skip-uninstall)
            SKIP_UNINSTALL=true
            shift
            ;;
        -v|--vllm-only)
            if [[ "$INSTALL_MODE" == "ascend" ]]; then
                ws_log_error "-v | --vllm-only 与 -a | --ascend-only 不能同时使用"
                exit 1
            fi
            INSTALL_MODE="vllm"
            shift
            ;;
        -a|--ascend-only)
            if [[ "$INSTALL_MODE" == "vllm" ]]; then
                ws_log_error "-v | --vllm-only 与 -a | --ascend-only 不能同时使用"
                exit 1
            fi
            INSTALL_MODE="ascend"
            shift
            ;;
        -c|--clean-build-cache)
            CLEAN_BUILD_CACHE=true
            shift
            ;;
        -t|--tmp-dir)
            ws_require_value "$1" "${2:-}"
            BUILD_TMP_DIR="$2"
            BUILD_TMP_DIR_EXPLICIT=true
            shift 2
            ;;
        --tmp-dir=*)
            BUILD_TMP_DIR="${1#*=}"
            if [[ -z "$BUILD_TMP_DIR" ]]; then
                ws_log_error "--tmp-dir 需要参数值，使用 -h 查看帮助"
                exit 1
            fi
            BUILD_TMP_DIR_EXPLICIT=true
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

INSTALL_VLLM=false
INSTALL_ASCEND=false
case "$INSTALL_MODE" in
    all)
        INSTALL_VLLM=true
        INSTALL_ASCEND=true
        ;;
    vllm)
        INSTALL_VLLM=true
        ;;
    ascend)
        INSTALL_ASCEND=true
        ;;
esac

if $CLEAN_BUILD_CACHE && ! $INSTALL_ASCEND; then
    ws_log_error "-c | --clean-build-cache 仅适用于包含 vllm-ascend 的安装"
    exit 1
fi

# ---- 环境检查 ----
if $INSTALL_VLLM && [[ ! -d "$SCRIPT_DIR/vllm" ]]; then
    ws_log_error "vllm 仓库不存在: $SCRIPT_DIR/vllm"
    echo "  请先运行: ./scripts/bootstrap.sh"
    exit 1
fi
if $INSTALL_ASCEND && [[ ! -d "$SCRIPT_DIR/vllm-ascend" ]]; then
    ws_log_error "vllm-ascend 仓库不存在: $SCRIPT_DIR/vllm-ascend"
    echo "  请先运行: ./scripts/bootstrap.sh"
    exit 1
fi

# Use the workspace tmp directory by default, or an explicit override. Check it
# before selecting Python, uninstalling packages, or invoking pip.
select_build_tmp_dir

ws_select_python_env "$CONDA_ENV"
if ! "$PYTHON_BIN" -m pip --version &>/dev/null; then
    ws_log_error "当前 Python 无法运行 pip: $PYTHON_BIN -m pip"
    exit 1
fi

if $INSTALL_ASCEND; then
    if $CLEAN_BUILD_CACHE; then
        clean_ascend_build_cache
    else
        inspect_ascend_build_cache
    fi
fi

# ---- 主逻辑 ----
PACKAGES=()
if $INSTALL_VLLM; then
    PACKAGES+=(vllm)
fi
if $INSTALL_ASCEND; then
    PACKAGES+=(vllm-ascend)
fi

echo "============================================"
echo " vLLM / vLLM Ascend 源码安装"
echo "============================================"
echo "  Python: $("$PYTHON_BIN" -c 'import sys; print(sys.executable)')"
echo "  组件:   ${PACKAGES[*]}"
echo "  卸载:   $([[ "$SKIP_UNINSTALL" == true ]] && echo "跳过" || echo "执行")"
echo "  临时目录: $BUILD_TMP_DIR（$BUILD_TMP_SOURCE，$(($(df -Pk "$BUILD_TMP_DIR" | awk 'NR == 2 { print $4 }') / 1024)) MiB 可用）"
if $INSTALL_ASCEND; then
    echo "  Ascend 构建缓存: $ASCEND_BUILD_CACHE_SUMMARY"
fi
if $INSTALL_VLLM; then
    echo "  vllm 构建依赖: ${VLLM_BUILD_REQUIREMENTS[*]}"
fi
echo "============================================"
echo ""

if $SKIP_UNINSTALL; then
    ws_log_skip "跳过卸载: ${PACKAGES[*]}"
else
    ws_log_step "卸载已安装包: ${PACKAGES[*]}"
    "$PYTHON_BIN" -m pip uninstall -y "${PACKAGES[@]}"
fi

if $INSTALL_VLLM; then
    echo ""
    MISSING_VLLM_BUILD_REQUIREMENTS=()
    for requirement in "${VLLM_BUILD_REQUIREMENTS[@]}"; do
        if "$PYTHON_BIN" -m pip show "$requirement" &>/dev/null; then
            ws_log_skip "vllm 源码构建依赖已安装: $requirement"
        else
            MISSING_VLLM_BUILD_REQUIREMENTS+=("$requirement")
        fi
    done

    if [[ ${#MISSING_VLLM_BUILD_REQUIREMENTS[@]} -gt 0 ]]; then
        ws_log_step "安装缺失的 vllm 源码构建依赖: ${MISSING_VLLM_BUILD_REQUIREMENTS[*]}"
        env "${BUILD_TMP_ENV[@]}" \
            "$PYTHON_BIN" -m pip install "${MISSING_VLLM_BUILD_REQUIREMENTS[@]}"
        ws_log_ok "vllm 源码构建依赖安装完成"
    else
        ws_log_skip "vllm 源码构建依赖均已安装"
    fi

    echo ""
    ws_log_step "从源码安装 vllm..."
    (
        cd "$SCRIPT_DIR/vllm"
        env "${BUILD_TMP_ENV[@]}" VLLM_TARGET_DEVICE=empty \
            "$PYTHON_BIN" -m pip install -e . --no-build-isolation
    )
    ws_log_ok "vllm 源码安装完成"
fi

if $INSTALL_ASCEND; then
    echo ""
    ws_log_step "从源码安装 vllm-ascend..."
    (
        cd "$SCRIPT_DIR/vllm-ascend"
        env "${BUILD_TMP_ENV[@]}" COMPILE_CUSTOM_KERNELS=1 \
            "$PYTHON_BIN" -m pip install -e . --no-build-isolation
    )
    ws_log_ok "vllm-ascend 源码安装完成"
fi

echo ""
ws_log_ok "源码安装流程完成"

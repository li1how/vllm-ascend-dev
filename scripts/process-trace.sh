#!/bin/bash
# ============================================================
# 进程溯源
#
# 根据宿主机进程 PID 查询运行目录，并从 cgroup 信息识别 Docker、
# containerd/CRI 或 Podman 容器。容器运行时 CLI 可用时，同时查询
# 容器名称和状态。
#
# 用法:
#   ./scripts/process-trace.sh <pid>                # 直接传入 PID（推荐）
#   ./scripts/process-trace.sh -p | --pid <pid>     # 通过选项传入 PID
#   ./scripts/process-trace.sh -h | --help          # 查看帮助
# ============================================================

set -euo pipefail
# shellcheck source=./lib/common.sh
# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/lib" && pwd)/common.sh"

TARGET_PID=""
PID_SOURCE=""
CONTAINER_RUNTIME=""
CONTAINER_ID=""
CONTAINER_NAME=""
CONTAINER_STATUS=""
CONTAINER_DETAIL=""

print_help() {
    echo "用法: $0 <pid>"
    echo "      $0 -p | --pid <pid>"
    echo ""
    echo "根据宿主机进程 PID 查询运行目录、容器运行时、容器 ID、名称和状态。"
    echo ""
    echo "参数:"
    echo "  <pid>              目标进程 PID（推荐直接传入）"
    echo ""
    echo "选项:"
    echo "  -p, --pid <pid>     目标进程 PID"
    echo "  -h, --help          显示此帮助信息"
    echo ""
    echo "示例:"
    echo "  $0 2191747"
    echo "  $0 -p 2191747"
    exit 0
}

set_target_pid() {
    local value="$1"
    local source="$2"

    if [[ -n "$TARGET_PID" ]]; then
        if [[ "$PID_SOURCE" != "$source" ]]; then
            ws_log_error "不能同时使用位置参数和 -p | --pid 指定 PID"
        elif [[ "$source" == "positional" ]]; then
            ws_log_error "只能传入一个位置参数 PID"
        else
            ws_log_error "-p | --pid 只能指定一次"
        fi
        exit 1
    fi

    TARGET_PID="$value"
    PID_SOURCE="$source"
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -p|--pid)
                ws_require_value "$1" "${2:-}"
                set_target_pid "$2" "option"
                shift 2
                ;;
            -h|--help)
                print_help
                ;;
            -[0-9]*)
                set_target_pid "$1" "positional"
                shift
                ;;
            -*)
                ws_log_error "未知参数: $1，使用 -h 查看帮助"
                exit 1
                ;;
            *)
                set_target_pid "$1" "positional"
                shift
                ;;
        esac
    done

    if [[ -z "$TARGET_PID" ]]; then
        ws_log_error "缺少 PID，使用 -h 查看帮助"
        exit 1
    fi
    if [[ ! "$TARGET_PID" =~ ^[1-9][0-9]*$ ]]; then
        ws_log_error "PID 必须是正整数: $TARGET_PID"
        exit 1
    fi
}

detect_container() {
    local cgroup_content="$1"
    local line
    local hex64='[[:xdigit:]]{64}'

    CONTAINER_RUNTIME=""
    CONTAINER_ID=""

    while IFS= read -r line; do
        if [[ "$line" =~ docker[-/]($hex64)(\.scope|/|$) ]]; then
            CONTAINER_RUNTIME="Docker"
            CONTAINER_ID="${BASH_REMATCH[1],,}"
            return 0
        fi
        if [[ "$line" =~ cri-containerd[-/]($hex64)(\.scope|/|$) ]]; then
            CONTAINER_RUNTIME="containerd/CRI"
            CONTAINER_ID="${BASH_REMATCH[1],,}"
            return 0
        fi
        if [[ "$line" =~ containerd[-/]($hex64)(\.scope|/|$) ]]; then
            CONTAINER_RUNTIME="containerd"
            CONTAINER_ID="${BASH_REMATCH[1],,}"
            return 0
        fi
        if [[ "$line" =~ libpod[-/]($hex64)(\.scope|/|$) ]]; then
            CONTAINER_RUNTIME="Podman"
            CONTAINER_ID="${BASH_REMATCH[1],,}"
            return 0
        fi
        if [[ "$line" =~ crio[-/]($hex64)(\.scope|/|$) ]]; then
            CONTAINER_RUNTIME="CRI-O"
            CONTAINER_ID="${BASH_REMATCH[1],,}"
            return 0
        fi
        if [[ "$line" =~ kubepods[^[:space:]]*/($hex64)(\.scope|/|$) ]]; then
            CONTAINER_RUNTIME="CRI（运行时未明确）"
            CONTAINER_ID="${BASH_REMATCH[1],,}"
            return 0
        fi
    done <<< "$cgroup_content"

    return 1
}

lookup_ps_metadata() {
    local runtime_cli="$1"
    local output
    local first_line
    local found_id

    if ! output="$("$runtime_cli" ps -a --no-trunc \
            --filter "id=$CONTAINER_ID" \
            --format '{{.ID}}|{{.Names}}|{{.Status}}' 2>/dev/null)"; then
        return 1
    fi
    first_line="${output%%$'\n'*}"
    [[ -n "$first_line" ]] || return 1

    IFS='|' read -r found_id CONTAINER_NAME CONTAINER_STATUS <<< "$first_line"
    [[ "${found_id,,}" == "$CONTAINER_ID" ]]
}

lookup_crictl_metadata() {
    local output
    local line
    local listed_id

    if output="$(crictl inspect --output go-template \
            --template '{{.status.metadata.name}}|{{.status.state}}' \
            "$CONTAINER_ID" 2>/dev/null)" && [[ "$output" == *"|"* ]]; then
        CONTAINER_NAME="${output%%|*}"
        CONTAINER_STATUS="${output#*|}"
        CONTAINER_STATUS="${CONTAINER_STATUS#CONTAINER_}"
        return 0
    fi

    if ! output="$(crictl ps -a --no-trunc --id "$CONTAINER_ID" 2>/dev/null)"; then
        return 1
    fi
    while IFS= read -r line; do
        read -r listed_id _ <<< "$line"
        if [[ "${listed_id,,}" == "$CONTAINER_ID" ]]; then
            CONTAINER_DETAIL="$line"
            return 0
        fi
    done <<< "$output"

    return 1
}

lookup_ctr_metadata() {
    local namespace
    local info_output
    local tasks_output
    local line
    local task_id
    local -a task_fields=()
    local name_pattern='"io\.kubernetes\.container\.name"[[:space:]]*:[[:space:]]*"([^"]+)"'

    for namespace in k8s.io default; do
        if ! info_output="$(ctr -n "$namespace" containers info \
                "$CONTAINER_ID" 2>/dev/null)"; then
            continue
        fi

        while IFS= read -r line; do
            if [[ "$line" =~ $name_pattern ]]; then
                CONTAINER_NAME="${BASH_REMATCH[1]}"
                break
            fi
        done <<< "$info_output"

        CONTAINER_STATUS="无运行中 task"
        if tasks_output="$(ctr -n "$namespace" tasks list 2>/dev/null)"; then
            while IFS= read -r line; do
                task_fields=()
                read -r -a task_fields <<< "$line"
                task_id="${task_fields[0]:-}"
                if [[ "${task_id,,}" == "$CONTAINER_ID" ]]; then
                    CONTAINER_STATUS="${task_fields[2]:-未知}"
                    break
                fi
            done <<< "$tasks_output"
        fi
        return 0
    done

    return 1
}

print_metadata() {
    [[ -n "$CONTAINER_NAME" ]] && echo "  容器名称:     $CONTAINER_NAME"
    [[ -n "$CONTAINER_STATUS" ]] && echo "  容器状态:     $CONTAINER_STATUS"
    [[ -n "$CONTAINER_DETAIL" ]] && echo "  容器信息:     $CONTAINER_DETAIL"
    return 0
}

query_container_metadata() {
    case "$CONTAINER_RUNTIME" in
        Docker)
            if ! ws_command_exists docker; then
                ws_log_warn "未找到 docker，跳过容器名称和状态查询"
            elif lookup_ps_metadata docker; then
                print_metadata
            else
                ws_log_warn "docker 无法查询容器元数据，可能没有访问权限或容器已被删除"
            fi
            ;;
        Podman)
            if ! ws_command_exists podman; then
                ws_log_warn "未找到 podman，跳过容器名称和状态查询"
            elif lookup_ps_metadata podman; then
                print_metadata
            else
                ws_log_warn "podman 无法查询容器元数据，可能没有访问权限或容器已被删除"
            fi
            ;;
        containerd|containerd/CRI|CRI（运行时未明确）)
            if ws_command_exists crictl && lookup_crictl_metadata; then
                print_metadata
            elif ws_command_exists ctr && lookup_ctr_metadata; then
                print_metadata
            else
                ws_log_warn "crictl/ctr 无法查询容器元数据，保留已识别的容器 ID"
            fi
            ;;
        CRI-O)
            if ws_command_exists crictl && lookup_crictl_metadata; then
                print_metadata
            else
                ws_log_warn "crictl 无法查询容器元数据，保留已识别的容器 ID"
            fi
            ;;
    esac
}

main() {
    local proc_dir
    local cgroup_file
    local working_dir
    local cgroup_content

    ws_enter_workspace
    parse_args "$@"
    ws_require_commands readlink

    proc_dir="/proc/$TARGET_PID"
    cgroup_file="$proc_dir/cgroup"

    if [[ ! -d "$proc_dir" ]]; then
        ws_log_error "进程不存在: $TARGET_PID"
        exit 1
    fi
    if ! working_dir="$(readlink "$proc_dir/cwd" 2>/dev/null)"; then
        ws_log_error "无法读取进程运行目录: $proc_dir/cwd（进程可能已退出或权限不足）"
        exit 1
    fi
    if [[ ! -r "$cgroup_file" ]]; then
        ws_log_error "无法读取进程 cgroup: $cgroup_file（进程可能已退出或权限不足）"
        exit 1
    fi
    cgroup_content="$(< "$cgroup_file")"

    echo "============================================"
    echo " 进程溯源"
    echo "============================================"
    echo "  PID:          $TARGET_PID"
    echo "  运行目录:     $working_dir"

    if detect_container "$cgroup_content"; then
        echo "  容器运行时:   $CONTAINER_RUNTIME"
        echo "  容器 ID:      $CONTAINER_ID"
        query_container_metadata
    else
        echo "  容器运行时:   未识别"
        ws_log_info "未从 cgroup 识别到容器 ID，可能是宿主机进程"
    fi
    echo "============================================"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi

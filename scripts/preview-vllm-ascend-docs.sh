#!/bin/bash
# ============================================================
# vllm-ascend 文档构建 & 预览
#
# 适用于修改 vllm-ascend 文档后，本地快速构建并启动 HTTP 服务
# 预览效果，无需推送到远端即可验证。
#
# 用法:
#   ./scripts/preview-vllm-ascend-docs.sh                      # EN→ZH→启动预览（跳过翻译）
#   ./scripts/preview-vllm-ascend-docs.sh -t | --translate     # EN→AI翻译→ZH→启动预览
#   ./scripts/preview-vllm-ascend-docs.sh -s | --skip-server   # 仅构建，不启动 HTTP 服务
#   PORT=9000 ./scripts/preview-vllm-ascend-docs.sh            # 自定义预览端口
# ============================================================

set -e
# shellcheck source=./lib/common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/lib" && pwd)/common.sh"
ws_enter_workspace

# ---- 加载 .env ----
ws_load_env

DOCS_DIR="$SCRIPT_DIR/vllm-ascend/docs"
CONDA_ENV="vllm-ascend-dev"
BUILD_DIR="$DOCS_DIR/_build"
PORT="${PORT:-8723}"
DO_TRANSLATE=false
NO_SERVER=false
PYTHON_BIN=""
# 使用系统 CA 证书（含公司代理 CA），certifi 内置的不含
export SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

# ---- 解析参数 ----
while [[ $# -gt 0 ]]; do
    case "$1" in
        -t|--translate) DO_TRANSLATE=true; shift ;;
        -s|--skip-server) NO_SERVER=true; shift ;;
        -h|--help)
            echo "用法: $0 [选项]"
            echo ""
            echo "  默认：EN构建 → ZH构建 → 启动预览(端口8723)"
            echo "  -t, --translate     启用 AI 翻译（DeepSeek）"
            echo "  -s, --skip-server   不启动 HTTP 服务"
            echo "  -h, --help          显示此帮助信息"
            echo ""
            echo "  PORT=9000 $0     自定义端口"
            exit 0
            ;;
        *) ws_log_error "未知参数: $1，使用 -h 查看帮助"; exit 1 ;;
    esac
done

# ---- 环境检查 ----
ws_select_python_env "$CONDA_ENV"
ws_require_commands sphinx-build
if [[ "$DO_TRANSLATE" == true ]]; then
    ws_require_commands sphinx-intl
    ws_require_python_module "openai" "请先在当前 Python 环境安装 openai"
fi

cd "$DOCS_DIR"
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

# ---- helper: 运行 sphinx-build 并醒目报出所有 WARNING/ERROR ----
run_sphinx() {
    local log
    log="$(mktemp)"
    local rc=0

    # 运行构建，stdout+stderr 全部写入临时日志
    set +e
    sphinx-build "$@" >"$log" 2>&1
    rc=$?
    set -e

    # 显示 sphinx 自身的最后一行摘要
    tail -1 "$log"

    # 提取并高亮报出所有 WARNING / ERROR
    local warn_count err_count
    warn_count=$(grep -c "WARNING:" "$log" 2>/dev/null || true)
    err_count=$(grep -c -E "ERROR:|CRITICAL:" "$log" 2>/dev/null || true)

    if [[ "$warn_count" -gt 0 ]] || [[ "$err_count" -gt 0 ]]; then
        echo "────────────────────────────────────────────"
        grep -E "WARNING:|ERROR:|CRITICAL:" "$log" || true
        echo "────────────────────────────────────────────"
        if [[ "$err_count" -gt 0 ]]; then
            ws_log_warn "$err_count error(s)/critical(s), $warn_count warning(s) — 详见上方 ↑"
        else
            ws_log_warn "$warn_count warning(s) — 详见上方 ↑"
        fi
        echo ""
    fi

    rm -f "$log"
    return $rc
}

# ============================================================
# 1. 构建英文文档
# ============================================================
ws_log_step "构建英文文档..."
run_sphinx -b html source "$BUILD_DIR/html"
if [[ ! -f "$BUILD_DIR/html/index.html" ]]; then
    ws_log_error "英文构建失败"
    exit 1
fi
ws_log_ok "$BUILD_DIR/html/"

# ============================================================
# AI 翻译（可选，-t 启用）
# ============================================================
if [[ "$DO_TRANSLATE" == true ]]; then
    echo ""
    ws_log_step "提取翻译模板..."
    run_sphinx -b gettext source "$BUILD_DIR/gettext"
    sphinx-intl update -p "$BUILD_DIR/gettext" -l zh_CN 2>&1 | grep -v "WARNING" || true

    echo ""
    ws_log_step "AI 翻译 (DeepSeek)..."

    TRANSLATE_SCRIPT="$DOCS_DIR/../.github/workflows/scripts/po_translate.py"
    if [[ ! -f "$TRANSLATE_SCRIPT" ]]; then
        ws_log_warn "翻译脚本不存在: $TRANSLATE_SCRIPT"
    else
        UNTRANSLATED=""
        TOTAL_EMPTY=0
        while IFS= read -r po; do
            empty_count=$(grep -c 'msgstr ""' "$po" 2>/dev/null || true)
            if [[ "$empty_count" -gt 1 ]]; then
                UNTRANSLATED="${UNTRANSLATED}${po},"
                TOTAL_EMPTY=$((TOTAL_EMPTY + empty_count - 1))
            fi
        done < <(find source/locale/zh_CN -name "*.po" | sort)

        if [[ -z "$UNTRANSLATED" ]]; then
            ws_log_ok "所有条目已翻译"
        else
            UNTRANSLATED="${UNTRANSLATED%,}"
            FILE_COUNT=$(echo "$UNTRANSLATED" | tr ',' '\n' | wc -l)
            echo "  $FILE_COUNT 个文件, ~$TOTAL_EMPTY 条待翻译"

            if [[ -z "${DEEPSEEK_API_KEY:-}" ]]; then
                ws_log_warn "未设置 DEEPSEEK_API_KEY 环境变量，跳过翻译"
            else
                set +e
                DEEPSEEK_API_KEY="$DEEPSEEK_API_KEY" "$PYTHON_BIN" "$TRANSLATE_SCRIPT" \
                    --files "$UNTRANSLATED" \
                    --output-json /tmp/translation_results.json 2>&1
                RC=$?
                set -e

                if [[ $RC -eq 0 ]]; then
                    SUCCESS=$("$PYTHON_BIN" -c "import json; d=json.load(open('/tmp/translation_results.json')); print(d.get('success_count',0))" 2>/dev/null || true)
                    ws_log_ok "翻译完成: $SUCCESS/$FILE_COUNT"
                else
                    ws_log_warn "翻译失败, 新增内容将回退英文"
                fi
            fi
        fi
    fi
else
    echo ""
    ws_log_step "跳过翻译 (用 -t 启用)"
fi

# ============================================================
# 构建中文文档
# ============================================================
echo ""
ws_log_step "构建中文文档..."
run_sphinx -b html -D language=zh_CN source "$BUILD_DIR/html/zh-cn"
if [[ ! -f "$BUILD_DIR/html/zh-cn/index.html" ]]; then
    ws_log_error "中文构建失败"
    exit 1
fi
ws_log_ok "$BUILD_DIR/html/zh-cn/"

# ============================================================
# 本地翻译文件屏蔽（仅 -t 翻译后）
# AI 翻译会在 locale/ 目录下产生 .po 修改，
# 用 skip-worktree 让 git 忽略这些本地改动，避免误提交。
# ============================================================
if [[ "$DO_TRANSLATE" == true ]]; then
    LOCALE_PO_DIR="$DOCS_DIR/source/locale/zh_CN/LC_MESSAGES"
    if [[ -d "$LOCALE_PO_DIR" ]]; then
        SKIPPED_COUNT=$(git -C "$SCRIPT_DIR/vllm-ascend" ls-files -- 'docs/source/locale/zh_CN/LC_MESSAGES/*.po' 2>/dev/null | wc -l)
        if [[ "$SKIPPED_COUNT" -gt 0 ]]; then
            git -C "$SCRIPT_DIR/vllm-ascend" ls-files -- 'docs/source/locale/zh_CN/LC_MESSAGES/*.po' \
                | git -C "$SCRIPT_DIR/vllm-ascend" update-index --skip-worktree --stdin 2>/dev/null
            echo ""
            ws_log_ok "已屏蔽 $SKIPPED_COUNT 个本地翻译文件 (skip-worktree)"
        fi
    fi
fi

# ============================================================
# 完成
# ============================================================
echo ""
echo "============================================================"
echo " 构建完成"
echo "  英文: http://localhost:$PORT"
echo "  中文: http://localhost:$PORT/zh-cn"
echo "============================================================"

if [[ "$NO_SERVER" != true ]]; then
    echo ""
    "$PYTHON_BIN" -m http.server "$PORT" -d "$BUILD_DIR/html"
else
    echo "  手动启动: $PYTHON_BIN -m http.server $PORT -d $BUILD_DIR/html"
fi

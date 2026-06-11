#!/bin/bash
# ============================================================
# vllm-ascend 文档构建 & 预览
#
# 适用于修改 vllm-ascend 文档后，本地快速构建并启动 HTTP 服务
# 预览效果，无需推送到远端即可验证。
#
# 用法:
#   ./scripts/build_docs.sh               # EN→ZH→启动预览（跳过翻译）
#   ./scripts/build_docs.sh -t            # EN→AI翻译→ZH→启动预览
#   ./scripts/build_docs.sh -s            # 仅构建，不启动 HTTP 服务
#   PORT=9000 ./scripts/build_docs.sh     # 自定义预览端口
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# ---- 加载 .env ----
if [[ -f "$SCRIPT_DIR/.env" ]]; then
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
fi

DOCS_DIR="$SCRIPT_DIR/vllm-ascend/docs"
CONDA_ENV="vllm-ascend-dev"
BUILD_DIR="$DOCS_DIR/_build"
PORT="${PORT:-8723}"
# 使用系统 CA 证书（含公司代理 CA），certifi 内置的不含
export SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

# ---- 解析参数 ----
while [[ $# -gt 0 ]]; do
    case "$1" in
        -t) DO_TRANSLATE=true; shift ;;
        -s) NO_SERVER=true; shift ;;
        -h|--help)
            echo "Usage: $0 [-t] [-s]"
            echo ""
            echo "  默认：EN构建 → ZH构建 → 启动预览(端口8723)"
            echo "  -t    启用 AI 翻译（DeepSeek）"
            echo "  -s    不启动 HTTP 服务"
            echo ""
            echo "  PORT=9000 $0     自定义端口"
            exit 0
            ;;
        *) echo "未知参数: $1"; exit 1 ;;
    esac
done

# ---- 环境检查 ----
if ! command -v conda &>/dev/null; then
    echo "[ERROR] conda 未找到"
    exit 1
fi
source "$HOME/miniconda3/etc/profile.d/conda.sh"
conda activate "$CONDA_ENV" 2>/dev/null || {
    echo "[ERROR] conda 环境 '$CONDA_ENV' 不存在"
    exit 1
}

# ---- 安装依赖 ----
if ! command -v sphinx-build &>/dev/null; then
    echo "[INFO] 安装文档构建依赖..."
    pip install -r "$DOCS_DIR/requirements-docs.txt" -q
fi

cd "$DOCS_DIR"
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

# ============================================================
# 1. 构建英文文档
# ============================================================
echo ">>> 构建英文文档..."
sphinx-build -b html source "$BUILD_DIR/html" 2>&1 | tail -3
if [[ ! -f "$BUILD_DIR/html/index.html" ]]; then
    echo "[ERROR] 英文构建失败"
    exit 1
fi
echo "[OK] $BUILD_DIR/html/"

# ============================================================
# AI 翻译（可选，-t 启用）
# ============================================================
if [[ "$DO_TRANSLATE" == true ]]; then
    echo ""
    echo ">>> 提取翻译模板..."
    sphinx-build -b gettext source "$BUILD_DIR/gettext" 2>&1 | tail -1
    sphinx-intl update -p "$BUILD_DIR/gettext" -l zh_CN 2>&1 | grep -v "WARNING" || true

    echo ""
    echo ">>> AI 翻译 (DeepSeek)..."

    TRANSLATE_SCRIPT="$DOCS_DIR/../.github/workflows/scripts/po_translate.py"
    if [[ ! -f "$TRANSLATE_SCRIPT" ]]; then
        echo "[WARN] 翻译脚本不存在: $TRANSLATE_SCRIPT"
    else
        UNTRANSLATED=""
        TOTAL_EMPTY=0
        while IFS= read -r po; do
            empty_count=$(grep -c 'msgstr ""' "$po" 2>/dev/null || echo 0)
            if [[ "$empty_count" -gt 1 ]]; then
                UNTRANSLATED="${UNTRANSLATED}${po},"
                TOTAL_EMPTY=$((TOTAL_EMPTY + empty_count - 1))
            fi
        done < <(find source/locale/zh_CN -name "*.po" | sort)

        if [[ -z "$UNTRANSLATED" ]]; then
            echo "[OK] 所有条目已翻译"
        else
            UNTRANSLATED="${UNTRANSLATED%,}"
            FILE_COUNT=$(echo "$UNTRANSLATED" | tr ',' '\n' | wc -l)
            echo "  $FILE_COUNT 个文件, ~$TOTAL_EMPTY 条待翻译"
            pip install openai -q 2>/dev/null

            if [[ -z "${DEEPSEEK_API_KEY:-}" ]]; then
                echo "[WARN] 未设置 DEEPSEEK_API_KEY 环境变量，跳过翻译"
            else
                set +e
                DEEPSEEK_API_KEY="$DEEPSEEK_API_KEY" python "$TRANSLATE_SCRIPT" \
                    --files "$UNTRANSLATED" \
                    --output-json /tmp/translation_results.json 2>&1
                RC=$?
                set -e

                if [[ $RC -eq 0 ]]; then
                    SUCCESS=$(python3 -c "import json; d=json.load(open('/tmp/translation_results.json')); print(d.get('success_count',0))" 2>/dev/null || echo 0)
                    echo "[OK] 翻译完成: $SUCCESS/$FILE_COUNT"
                else
                    echo "[WARN] 翻译失败, 新增内容将回退英文"
                fi
            fi
        fi
    fi
else
    echo ""
    echo ">>> 跳过翻译 (用 -t 启用)"
fi

# ============================================================
# 构建中文文档
# ============================================================
echo ""
echo ">>> 构建中文文档..."
sphinx-build -b html -D language=zh_CN source "$BUILD_DIR/html/zh-cn" 2>&1 | tail -3
if [[ ! -f "$BUILD_DIR/html/zh-cn/index.html" ]]; then
    echo "[ERROR] 中文构建失败"
    exit 1
fi
echo "[OK] $BUILD_DIR/html/zh-cn/"

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
    python3 -m http.server "$PORT" -d "$BUILD_DIR/html"
else
    echo "  手动启动: python3 -m http.server $PORT -d $BUILD_DIR/html"
fi

#!/bin/bash
# ============================================================
# 安装公司代理 MITM 根 CA 到系统信任库
#
# 公司出网代理 (http(s)_proxy) 对 HTTPS 做 SSL 中间人拦截，使用自签
# 根 CA 重新签发证书。未信任该 CA 时，curl / codex / git 等会报
#   "SSL certificate problem: self-signed certificate in certificate chain"
# 本脚本从代理实时抓取该自签根 CA，安装到系统信任库
# (update-ca-certificates / update-ca-trust)，使 TLS 校验恢复正常。
#
# 适用场景: devcontainer 重建后 codex login / curl 证书失败时运行；
#           建议加入 devcontainer postCreateCommand 自动执行。
#
# 用法:
#   ./scripts/install-corp-ca.sh                          # 从 $https_proxy 抓取并安装
#   ./scripts/install-corp-ca.sh -p | --proxy <host:port> # 指定代理
#   ./scripts/install-corp-ca.sh -f | --force             # 强制重装
#   ./scripts/install-corp-ca.sh -h | --help              # 查看帮助
# ============================================================

set -euo pipefail
# shellcheck source=./lib/common.sh
# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/lib" && pwd)/common.sh"
ws_enter_workspace
shopt -s nullglob

# ---- 默认配置 ----
PROXY=""                         # 从 $https_proxy/$HTTPS_PROXY 读取，可用 -p 覆盖
TARGET_HOST="auth.openai.com"    # 抓取 MITM 证书用的目标主机
FORCE=false
CA_NAME="corp-proxy-ca"          # 安装文件名: <name>.crt，目录按系统信任库选择

# ---- 参数解析 ----
print_help() {
    echo "用法: $0 [选项]"
    echo ""
    echo "从公司代理抓取自签根 CA 并安装到系统信任库。"
    echo ""
    echo "选项:"
    echo "  -p, --proxy <host:port>  代理地址（默认读 \$https_proxy / \$HTTPS_PROXY）"
    echo "  -H, --host <host>        抓取证书的目标主机（默认: $TARGET_HOST）"
    echo "  -f, --force              强制重装（默认已信任时跳过）"
    echo "  -h, --help               显示此帮助信息"
    echo ""
    echo "示例:"
    echo "  $0                       # 使用环境变量中的代理"
    echo "  $0 -p 90.254.37.132:3128 # 指定代理"
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -p|--proxy)
            ws_require_value "$1" "${2:-}"
            PROXY="$2"; shift 2 ;;
        -H|--host)
            ws_require_value "$1" "${2:-}"
            TARGET_HOST="$2"; shift 2 ;;
        -f|--force) FORCE=true; shift ;;
        -h|--help)  print_help ;;
        *) ws_log_error "未知参数: $1，使用 -h 查看帮助"; exit 1 ;;
    esac
done

# ---- 环境检查 ----
# root 权限：写入系统 CA anchor 并刷新系统信任库
if [[ $EUID -ne 0 ]]; then
    ws_log_error "需要 root 权限安装系统 CA，请用 root 运行或加 sudo"
    exit 1
fi

# 解析代理地址
if [[ -z "$PROXY" ]]; then
    PROXY="${https_proxy:-${HTTPS_PROXY:-}}"
fi
if [[ -z "$PROXY" ]]; then
    ws_log_error "未获取到代理地址，请设置 \$https_proxy 或用 -p | --proxy <host:port> 指定"
    exit 1
fi
# openssl s_client -proxy 需要 host:port（去 scheme 与路径）
PROXY_HP="${PROXY#http://}"
PROXY_HP="${PROXY_HP#https://}"
PROXY_HP="${PROXY_HP%%/*}"

ws_require_commands openssl curl
ws_select_package_manager
ws_require_commands "${WS_CA_UPDATE_COMMAND[0]}"
CA_FILE="$WS_CA_ANCHOR_DIR/${CA_NAME}.crt"

# ---- 幂等检查 ----
if ! $FORCE; then
    if curl -sS -o /dev/null --max-time 10 --proxy "$PROXY" "https://$TARGET_HOST/" 2>/dev/null; then
        ws_log_skip "系统已信任公司代理 CA，无需重装（用 -f | --force 强制重装）"
        exit 0
    fi
fi

# ---- 主逻辑 ----
TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

ws_log_step "抓取公司代理 MITM 证书链 ($TARGET_HOST via $PROXY_HP)..."
if ! echo | openssl s_client -proxy "$PROXY_HP" -connect "$TARGET_HOST:443" \
        -servername "$TARGET_HOST" -showcerts 2>/dev/null > "$TMPDIR/chain.pem"; then
    ws_log_error "openssl 抓取证书链失败（确认 openssl 支持 -proxy 且代理可达）"
    exit 1
fi
if [[ ! -s "$TMPDIR/chain.pem" ]] || ! grep -q 'BEGIN CERTIFICATE' "$TMPDIR/chain.pem"; then
    ws_log_error "未获取到任何证书"
    exit 1
fi

# 拆分证书链为单张 PEM
awk -v d="$TMPDIR" 'BEGIN{n=0} /-----BEGIN CERTIFICATE-----/{n++} {print > d"/cert_"n".pem"}' "$TMPDIR/chain.pem"

# 找自签根 CA（subject == issuer）
ROOT_CERT=""
for f in "$TMPDIR"/cert_*.pem; do
    [[ -s "$f" ]] || continue
    subj="$(openssl x509 -in "$f" -noout -subject 2>/dev/null | sed 's/^subject=//' || true)"
    iss="$(openssl x509 -in "$f" -noout -issuer 2>/dev/null | sed 's/^issuer=//' || true)"
    if [[ -n "$subj" && "$subj" == "$iss" ]]; then
        ROOT_CERT="$f"
        break
    fi
done
if [[ -z "$ROOT_CERT" ]]; then
    ws_log_error "证书链中未找到自签根 CA（代理可能未下发根证书）"
    exit 1
fi
ws_log_info "找到自签根 CA: $(openssl x509 -in "$ROOT_CERT" -noout -subject 2>/dev/null)"

ws_log_step "安装到 $CA_FILE ..."
mkdir -p "$(dirname "$CA_FILE")"
cp "$ROOT_CERT" "$CA_FILE"
chmod 644 "$CA_FILE"
if ! UPD_OUT="$("${WS_CA_UPDATE_COMMAND[@]}" 2>&1)"; then
    ws_log_error "${WS_CA_UPDATE_COMMAND[*]} 失败"
    [[ -n "$UPD_OUT" ]] && echo "$UPD_OUT"
    exit 1
fi
if [[ "$WS_SYSTEM_FAMILY" == "debian" ]]; then
    UPD_OUT="$(grep -m1 -E '[0-9]+ added, [0-9]+ removed' <<< "$UPD_OUT" || true)"
fi
[[ -z "$UPD_OUT" ]] && UPD_OUT="done"
ws_log_ok "${WS_CA_UPDATE_COMMAND[*]}: $UPD_OUT"

# ---- 验证 ----
ws_log_step "验证 TLS 信任..."
if curl -sS -o /dev/null --max-time 15 --proxy "$PROXY" "https://$TARGET_HOST/" 2>/dev/null; then
    ws_log_ok "公司代理 CA 已安装并信任"
else
    ws_log_error "安装后验证仍失败，请检查代理与证书链"
    exit 1
fi

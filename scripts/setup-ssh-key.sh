#!/bin/bash
# ============================================================
# SSH 密钥初始化与公钥安装
#
# 生成或复用本机 ed25519 SSH 密钥，并将公钥安装到远端服务器的
# ~/.ssh/authorized_keys。远端 IP/主机名必须显式传入，用户名默认 root。
#
# 用法:
#   ./scripts/setup-ssh-key.sh -i | --ip <addr>            # 使用 root 用户安装公钥
#   ./scripts/setup-ssh-key.sh -i <addr> -u | --user <name> # 指定远端用户
#   ./scripts/setup-ssh-key.sh -c | --comment <text>       # 指定密钥注释
#   ./scripts/setup-ssh-key.sh -k | --key-file <path>      # 指定密钥路径
#   ./scripts/setup-ssh-key.sh -h | --help                 # 查看帮助
# ============================================================

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"

# ---- 默认配置 ----
IP=""
SSH_USER="root"
COMMENT="yihao.li@huawei.com"
KEY_FILE="$HOME/.ssh/id_ed25519"

# ---- 参数解析 ----
print_help() {
    echo "用法: $0 [选项]"
    echo ""
    echo "生成或复用本机 ed25519 SSH 密钥，并将公钥安装到远端 authorized_keys。"
    echo ""
    echo "选项:"
    echo "  -i, --ip <addr>        远端服务器 IP 或主机名（必填）"
    echo "  -u, --user <name>      远端用户名（默认: root）"
    echo "  -c, --comment <text>   密钥注释（默认: yihao.li@huawei.com）"
    echo "  -k, --key-file <path>  密钥路径（默认: \$HOME/.ssh/id_ed25519）"
    echo "  -h, --help             显示此帮助信息"
    echo ""
    echo "示例:"
    echo "  $0 -i 90.90.97.48"
    echo "  $0 -i 90.90.97.48 -u root"
    exit 0
}

require_value() {
    local opt="$1"
    local value="${2:-}"
    if [[ -z "$value" || "$value" == -* ]]; then
        echo "[ERROR] $opt 需要参数值，使用 -h 查看帮助"
        exit 1
    fi
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -i|--ip)
            require_value "$1" "${2:-}"
            IP="$2"
            shift 2
            ;;
        -u|--user)
            require_value "$1" "${2:-}"
            SSH_USER="$2"
            shift 2
            ;;
        -c|--comment)
            require_value "$1" "${2:-}"
            COMMENT="$2"
            shift 2
            ;;
        -k|--key-file)
            require_value "$1" "${2:-}"
            KEY_FILE="$2"
            shift 2
            ;;
        -h|--help)
            print_help
            ;;
        *)
            echo "[ERROR] 未知参数: $1，使用 -h 查看帮助"
            exit 1
            ;;
    esac
done

if [[ -z "$IP" ]]; then
    echo "[ERROR] 缺少必填参数: -i | --ip <addr>"
    exit 1
fi

# ---- 环境检查 ----
for cmd in ssh ssh-keygen; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "[ERROR] 缺少依赖: $cmd"
        exit 1
    fi
done

KEY_DIR="$(dirname "$KEY_FILE")"
PUB_KEY_FILE="${KEY_FILE}.pub"
REMOTE_TARGET="${SSH_USER}@${IP}"

mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
if [[ ! -d "$KEY_DIR" ]]; then
    mkdir -p "$KEY_DIR"
    chmod 700 "$KEY_DIR"
fi

# ---- 主逻辑 ----
echo "============================================"
echo " SSH 密钥初始化"
echo "============================================"
echo "  远端:   $REMOTE_TARGET"
echo "  私钥:   $KEY_FILE"
echo "  公钥:   $PUB_KEY_FILE"
echo "  注释:   $COMMENT"
echo "============================================"
echo ""

if [[ -f "$KEY_FILE" && -f "$PUB_KEY_FILE" ]]; then
    echo "[INFO] 已存在密钥对，复用: $KEY_FILE"
elif [[ -f "$KEY_FILE" ]]; then
    echo "[INFO] 已存在私钥，恢复公钥: $PUB_KEY_FILE"
    ssh-keygen -y -f "$KEY_FILE" > "$PUB_KEY_FILE"
elif [[ -f "$PUB_KEY_FILE" ]]; then
    echo "[ERROR] 已存在公钥但私钥缺失: $PUB_KEY_FILE"
    echo "        请恢复私钥、删除孤立公钥，或用 -k | --key-file 指定其他路径"
    exit 1
else
    echo ">>> 生成 ed25519 SSH 密钥..."
    ssh-keygen -t ed25519 -C "$COMMENT" -f "$KEY_FILE"
    echo "[OK] 密钥已生成: $KEY_FILE"
fi

if [[ ! -s "$PUB_KEY_FILE" ]]; then
    echo "[ERROR] 公钥文件不存在或为空: $PUB_KEY_FILE"
    exit 1
fi

echo ""
echo ">>> 安装公钥到远端 authorized_keys..."
ssh "$REMOTE_TARGET" '
set -e
mkdir -p ~/.ssh
chmod 700 ~/.ssh
touch ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
key="$(cat)"
if grep -qxF "$key" ~/.ssh/authorized_keys; then
    echo "[SKIP] 远端 authorized_keys 已包含该公钥"
else
    printf "%s\n" "$key" >> ~/.ssh/authorized_keys
    echo "[OK] 公钥已写入远端 authorized_keys"
fi
' < "$PUB_KEY_FILE"

echo ""
echo "[OK] SSH 密钥初始化完成"

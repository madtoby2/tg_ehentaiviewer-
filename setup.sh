#!/usr/bin/env bash
# ============================================================
# hentaiviewer bot 一键部署脚本（克隆部署用）
#
# 用法:
#   ./setup.sh                    # 交互式向导
#   ./setup.sh --non-interactive  # 全用环境变量，不询问
#   ./setup.sh --install-service  # 额外安装 systemd 服务
#
# 非交互模式可用环境变量:
#   EHBOT_TELEGRAM_TOKEN  EHBOT_GROUP_MODE  EHBOT_GROUP_ALLOWED_CHATS
#   EHBOT_ALLOWED_USERS   EHBOT_OWNER_USERS EHBOT_DAILY_LIMIT
#   EHBOT_TELEGRAPH_TOKEN_FILE
# ============================================================
set -euo pipefail

cd "$(dirname "$0")"

NON_INTERACTIVE=0
INSTALL_SERVICE=0
SKIP_DEPS=0
for arg in "$@"; do
    case "$arg" in
        --non-interactive) NON_INTERACTIVE=1 ;;
        --install-service) INSTALL_SERVICE=1 ;;
        --skip-deps) SKIP_DEPS=1 ;;
        *) echo "未知参数: $arg" >&2; exit 1 ;;
    esac
done

say()  { printf '\033[1;32m%s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m%s\033[0m\n' "$*"; }

ask() {
    # ask "提示" "变量名" "默认值"
    local prompt="$1" var="$2" default="${3:-}"
    if [ -n "${!var:-}" ]; then
        printf '%s [%s] (已设置: %s)\n' "$prompt" "$default" "${!var}"
        return
    fi
    if [ "$NON_INTERACTIVE" = "1" ]; then
        printf '%s [%s]\n' "$prompt" "${default:-未设置}"
        return
    fi
    local val
    read -r -p "$prompt [${default:-}] " val
    eval "$var='${val:-$default}'"
}

# ---------- 0. 检查环境 ----------
say "==> 检查环境..."
command -v python3 >/dev/null || { echo "缺少 python3"; exit 1; }
python3 -m venv --help >/dev/null 2>&1 || { echo "缺少 venv 模块 (apt install python3-venv)"; exit 1; }

# ---------- 1. 安装依赖 ----------
if [ "$SKIP_DEPS" = "1" ]; then
    warn "==> 跳过依赖安装 (--skip-deps)"
elif [ ! -d .venv ]; then
    say "==> 创建虚拟环境 .venv..."
    python3 -m venv .venv
    say "==> 安装依赖..."
    ./.venv/bin/pip install -q --upgrade pip
    ./.venv/bin/pip install -q -r requirements.txt
    say "==> 依赖安装完成"
else
    say "==> .venv 已存在，安装/更新依赖..."
    ./.venv/bin/pip install -q -r requirements.txt
fi

# ---------- 2. 收集配置 ----------
say "==> 收集配置..."

ask "BotFather 创建的 bot token" EHBOT_TELEGRAM_TOKEN
if [ -z "$EHBOT_TELEGRAM_TOKEN" ]; then
    echo "错误: bot token 必填。@BotFather → /newbot 创建" >&2
    exit 1
fi

ask "启用群模式? (1=是 0=仅私聊)" EHBOT_GROUP_MODE "1"
ask "群白名单 chat_id (逗号分隔, 留空=任意群可用)" EHBOT_GROUP_ALLOWED_CHATS
ask "管理员(owner) user_id (免配额)" EHBOT_OWNER_USERS
ask "普通用户每日配额" EHBOT_DAILY_LIMIT "10"
ask "私聊白名单 user_id (逗号分隔, 留空=全开放)" EHBOT_ALLOWED_USERS
ask "Telegraph token 存储路径" EHBOT_TELEGRAPH_TOKEN_FILE ".telegraph_token.json"

# ---------- 3. 生成 .env ----------
say "==> 生成 .env..."
cat > .env <<EOF
EHBOT_TELEGRAM_TOKEN=${EHBOT_TELEGRAM_TOKEN}
EHBOT_GROUP_MODE=${EHBOT_GROUP_MODE:-1}
EOF
[ -n "${EHBOT_GROUP_ALLOWED_CHATS:-}" ] && echo "EHBOT_GROUP_ALLOWED_CHATS=${EHBOT_GROUP_ALLOWED_CHATS}" >> .env
[ -n "${EHBOT_OWNER_USERS:-}" ] && echo "EHBOT_OWNER_USERS=${EHBOT_OWNER_USERS}" >> .env
[ -n "${EHBOT_DAILY_LIMIT:-}" ] && echo "EHBOT_DAILY_LIMIT=${EHBOT_DAILY_LIMIT}" >> .env
[ -n "${EHBOT_ALLOWED_USERS:-}" ] && echo "EHBOT_ALLOWED_USERS=${EHBOT_ALLOWED_USERS}" >> .env
echo "EHBOT_TELEGRAPH_TOKEN_FILE=${EHBOT_TELEGRAPH_TOKEN_FILE:-.telegraph_token.json}" >> .env
echo "EHBOT_USAGE_FILE=$(pwd)/usage_limits.json" >> .env
chmod 600 .env
say "==> .env 已生成 (权限 600)"

# ---------- 4. 可选 systemd 服务 ----------
if [ "$INSTALL_SERVICE" = "1" ]; then
    say "==> 安装 systemd 服务..."
    SERVICE_NAME="eh-reader-bot"
    UNIT="deploy/eh-reader-bot.service"
    if [ ! -f "$UNIT" ]; then
        echo "错误: 缺少 $UNIT" >&2; exit 1
    fi
    # 替换路径占位符
    sed -e "s|__DIR__|$(pwd)|g" "$UNIT" > "/etc/systemd/system/${SERVICE_NAME}.service"
    systemctl daemon-reload
    systemctl enable "${SERVICE_NAME}"
    systemctl start "${SERVICE_NAME}"
    systemctl --no-pager status "${SERVICE_NAME}" | head -5
    say "==> systemd 服务已启动: systemctl status ${SERVICE_NAME}"
else
    warn "提示: 加 --install-service 可安装为 systemd 服务开机自启"
fi

say ""
say "✅ 部署完成！启动方式:"
say "   前台运行:  ./.venv/bin/python bot.py"
say "   服务运行:  ./setup.sh --install-service"
say ""
say "记得 @BotFather → /setprivacy → 选 bot → Disable（群模式必须）"

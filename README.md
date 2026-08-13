# tg_ehentaiviewer

Telegram bot — 发链接直接看图：支持 **EHentai** 与 **18comic**（禁漫天堂）画廊抓取，转存 **Telegraph** 发布，并支持每日排行自动推送到频道。

## 功能

- **链接转阅读**：用户发 EH/18comic 画廊链接，bot 抓取全部图片并发布到 Telegraph，返回阅读页
- **随机推荐**：按 tag 过滤（AI 图 / Guro / 韩漫黑名单可配），每日配额防滥用
- **搜索**：EH / 18comic 按 tag 搜索，交互式结果列表选择
- **每日排行**：EH popular + 18comic popular 排行抓取，一键发布
- **频道推送**：排行/推荐自动发布到指定频道（store channel）
- **图片托管**：catbox 或本地静态目录两种模式

## 命令

| 命令 | 说明 |
|---|---|
| `/start` | 欢迎信息 + 显示固定键盘 |
| `/help` | 帮助 |
| `/daily` | 查看今日剩余次数 |
| `/stats` | 使用统计（管理员） |
| `/cancel` | 取消当前任务 |

**固定键盘（custom keyboard，输入框下方）：** `/start` 后自动出现，点击即用：
- `🎲 随机推荐` — 随机推荐一部
- `🔍 标签搜索` — 输入标签搜索
- `📊 今日额度` — 查看剩余次数
- `🏆 当日排行` — 热门排行（仅管理员可见）

**以图搜图：** 直接发图片给 bot（私聊直接发；群里需 @bot 或回复 bot），返回 Saucenao 匹配结果（pixiv / E-Hentai / Danbooru / Twitter 等来源），匹配到 EH/18comic 画廊可直接点「📖 生成阅读页」。需配置 `SAUCENAO_API_KEY`（saucenao.com 免费注册，约 100 次/天）。

直接发链接或文字消息（URL / tag 搜索词）即可触发处理流程。

## 安装

```bash
pip install -r requirements.txt  # python-telegram-bot cloudscraper requests beautifulsoup4 jmcomic python-dotenv
```

配置环境变量（`.env`）：

```ini
# Telegram
EHBOT_TELEGRAM_TOKEN=        # bot token
EHBOT_ALLOWED_USERS=         # 白名单用户 ID，逗号分隔
STORE_CHANNEL_CHAT_ID=       # 频道 chat_id
STORE_BOT_TOKEN=             # 频道推送用 bot token

# 抓取
EHBOT_MAX_WORKERS=5          # 并发抓图 worker 数
EHBOT_MAX_PAGES=0            # 单画廊最大页数限制（0=不限）
EHBOT_JM_COOKIES=            # 18comic 登录 cookies（F12 → Application → Cookies）

# 图片托管
EHBOT_IMAGE_HOST=catbox      # catbox | static
EHBOT_CATBOX_USERHASH=       # catbox userhash
EHBOT_STATIC_IMAGE_ROOT=     # 本地静态目录
EHBOT_STATIC_IMAGE_BASE_URL= # 静态目录公网 base url
EHBOT_STATIC_IMAGE_TTL_SECONDS=86400

# 群组模式（可选）
EHBOT_GROUP_MODE=0           # 1=允许在群里使用（默认 0=仅私聊）
EHBOT_GROUP_ALLOWED_CHATS=   # 群白名单 chat_id 逗号分隔（空=任意群可用）
                             # 例如: -1001234567890,-1009876543210

# 排行
DAILY_RANKING_COMIC_TELEGRAPH=  # 排行发布目标 telegraph 账号 token
```

运行：

```bash
python bot.py
```

## 目录结构

```
bot.py                        # 主程序（命令处理 / 配额 / 排行 / 搜索）
scrapers/
  ehentai.py                  # EHentai 抓取（metadata / 分页 / 搜索）
  comic18.py                  # 18comic 抓取
publishers/
  telegraph.py                # Telegraph 发布（账号管理 / 图片上传 / 建页）
  jm_telegraph.py             # 画廊发布流水线（并发 / 内存控制 / 静态托管）
  reader.py
tests/                        # 单元测试
trigger_ranking.py            # 手动触发补发当日排行（ops 脚本）
```

## 群组模式

把 bot 拉进群使用。**两种模式的差异：**

| | 私聊（DM） | 群组 |
|---|---|---|
| 发链接 | 直接处理 ✅ | **必须 @bot 或回复 bot 的消息**才处理 |
| 固定键盘 | 可用 | 可用（按钮是明确操作，直接生效） |
| 权限 | 白名单 / 公开+配额 | 全员可用（受每日配额限制） |

群组里别人随便发链接 bot 会**静默不响应**，只有 `@hentaiviewer_bot 链接` 或回复 bot 的消息才会生成阅读页——多 bot 群里不抢消息。

1. **BotFather 关闭隐私模式**（必须，否则群里收不到任何普通消息，@/回复也收不到）：
   ```
   /setprivacy  → 选择 bot  → Disable
   ```
2. 配置 `.env` 开启群组模式：
   ```ini
   EHBOT_GROUP_MODE=1
   EHBOT_GROUP_ALLOWED_CHATS=-1001234567890   # 建议填群白名单，防止被拉进陌生群白嫖
   ```
3. 重启 bot。群内 `/start` 会显示群组模式说明。

行为差异：
- 并发：群里按**用户**加锁（不同成员同时发链接互不等待），私聊按会话加锁
- 群白名单为空 = 任何群可用；非空 = 仅限指定群

## 克隆部署（一键安装到自己的机器）

点 GitHub **Use this template / Fork** 或 `git clone` 后，在服务器上一条命令装完：

```bash
cd tg_ehentaiviewer-
./setup.sh                          # 交互式：输入自己的 bot token → 自动装依赖 → 生成 .env
```

跑完直接启动：

```bash
./.venv/bin/python bot.py           # 前台运行
# 或安装为 systemd 服务（开机自启）：
./setup.sh --install-service
```

**全程只需回答几个问题**（bot token、群模式开关、群白名单、owner ID、每日配额），脚本自动：
1. 创建 Python 虚拟环境并安装全部依赖（`requirements.txt`）
2. 生成 `.env`（权限 600，含你的专属配置）
3. 可选：安装 systemd 服务并启动

非交互模式（脚本化部署/容器用）：

```bash
EHBOT_TELEGRAM_TOKEN="123:abc" \
EHBOT_GROUP_MODE="1" \
EHBOT_GROUP_ALLOWED_CHATS="-1001234567890" \
./setup.sh --non-interactive --install-service
```

**克隆部署必备提醒：**
- @BotFather → `/setprivacy` → **Disable**（群模式必须，否则群里收不到链接消息）
- 群 ID 获取：群里发消息给 @getidsbot / @userinfobot
- 每个克隆实例的 Telegraph 账号自动独立创建（token 存项目目录 `.telegraph_token.json`）

## 注意

- 18comic 登录限制画廊需要 `EHBOT_JM_COOKIES`
- EH 抓取走 cloudscraper 绕过防护，IP 频繁会被限流
- `trigger_ranking.py` 依赖 `/root/eh-reader-bot` 绝对路径，仅限本机 ops 使用

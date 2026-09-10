# tg_ehentaiviewer

Telegram bot — 发链接直接看图：支持 **EHentai** 与 **18comic**（禁漫天堂）画廊抓取，转存 **Telegraph** 发布，并支持每日排行自动推送到频道。

## 功能

- **链接转阅读**：用户发 EH/18comic 画廊链接，bot 抓取全部图片并发布到 Telegraph，返回阅读页
- **随机推荐**：按 tag 过滤（AI 图 / Guro / 韩漫黑名单可配），每日配额防滥用
- **搜索**：EH / 18comic 按 tag 搜索，交互式结果列表选择
- **以图搜图**：直接发图片 → Whos.tv / Yandex / trace.moe / 本地 OCR / IQDB / Saucenao 并行聚合，返回番号、来源、匹配帧
- **订阅提醒**：订阅作者 / 标签 / 女优，排行命中后主动推送（只保存明确订阅词，不记录搜索历史）
- **每日排行**：EH popular + 18comic popular 排行抓取，提前低并发预热缓存，一键发布
- **频道推送**：排行/推荐自动发布到指定频道（store channel）
- **健康检查**：`/health` 一次性自检上游站点、Whos 积分、缓存与临时文件
- **图片托管**：catbox 或本地静态目录两种模式

## 命令

| 命令 | 说明 |
|---|---|
| `/start` | 欢迎信息 + 显示固定键盘 |
| `/help` | 帮助 |
| `/daily` | 查看今日剩余次数 |
| `/stats` | 使用统计（管理员） |
| `/health` | 上游、积分、缓存和临时文件健康检查（管理员） |
| `/subscribe 标签 纯爱` | 订阅作者、标签或女优 |
| `/unsubscribe 标签 纯爱` | 取消订阅 |
| `/subscriptions` | 查看明确订阅项 |
| `/cancel` | 取消当前任务 |

**固定键盘（custom keyboard，输入框下方）：** `/start` 后自动出现，点击即用：
- `🎲 随机推荐` — 随机推荐一部
- `🔍 标签搜索` — 输入标签搜索
- `📊 今日额度` — 查看剩余次数
- `🏆 当日排行` — 热门排行（仅管理员可见）

**任意图片查出处：** 直接发图片给 bot（私聊直接发；群里需 @bot 或回复 bot），也支持以**文件**发送的 JPG/PNG/WebP 原图。并行聚合：
- **Whos.tv**：AV 截图专用识别，直接返回番号、相似度、匹配帧与精准时间点。按账号池顺序逐个检查积分，跳过积分不足的账号（需配置单账号或账号池；低于 `WHOS_TV_MIN_SIMILARITY` 只作为画面候选展示，不宣称命中）
- **Yandex Images**：通用相似图和网页来源，适合真人、AV 截图、商品、表情包等
- **trace.moe**：动画截图识别，返回动画名、集数、时间点和预览（同名歧义结果会被拒绝）
- **本地 OCR**：提取番号（如 `SSIS-123`）、水印、字幕等文字线索
- **IQDB**：无 key 免费启用，覆盖 E-Hentai / Danbooru / 图站；45 秒硬截止
- **Saucenao**：可选增强，覆盖 Pixiv / Twitter / E-Hentai 等。多个 API key 用逗号分隔，自动轮询（N 个 key ≈ 100N 次/天）

只有匹配到 EH/18comic 画廊时才显示「📖 生成阅读页」。配置示例：`SAUCENAO_API_KEY=key1,key2,key3`。

匹配帧会作为图片直接发到会话里方便比对；识别任务结束即清理临时目录，不留图片缓存。

每日排行会提前低并发生成并缓存阅读页；作者/标签/女优订阅只保存用户明确添加的关键词，不记录搜索、阅读或识图历史。

直接发链接或文字消息（URL / tag 搜索词）即可触发处理流程。

## 定时任务

| 时间（UTC） | 任务 | 说明 |
|---|---|---|
| `DAILY_RANKING_HOUR - 2` | `prewarm_rankings` | 低并发抓取当日排行并预生成 Telegraph 阅读页，写入缓存 |
| `DAILY_RANKING_HOUR - 1` | `notify_subscription_matches` | 按订阅关键词匹配排行结果并推送（幂等，重复执行不重发） |
| `DAILY_RANKING_HOUR` | `daily_ranking_to_store` | 推送当日排行到频道 |

## 安装

```bash
pip install -r requirements.txt  # python-telegram-bot cloudscraper requests beautifulsoup4 lxml jmcomic python-dotenv curl_cffi
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

# 以图搜图
WHOS_TV_USERNAME=            # Whos.tv 专用账号（登录与 Session 续期由 bot 自动完成）
WHOS_TV_PASSWORD=
WHOS_TV_ACCOUNTS_FILE=       # 共享 Whos.tv 账号池文件（留空 = 只用上面的单账号）
WHOS_TV_MIN_SIMILARITY=90    # 低于此相似度不宣称 AV 番号命中
SAUCENAO_API_KEY=            # 可选，多个 key 逗号分隔

# 状态文件（默认写在项目目录，克隆部署无需改）
EHBOT_RANKING_CACHE_FILE=/root/eh-reader-bot/ranking_cache.json
EHBOT_SUBSCRIPTIONS_FILE=/root/eh-reader-bot/subscriptions.json
EHBOT_SUBSCRIPTION_NOTIFY_STATE_FILE=/root/eh-reader-bot/subscription_notify_state.json

# 群组模式（可选）
EHBOT_GROUP_MODE=0           # 1=允许在群里使用（默认 0=仅私聊）
EHBOT_GROUP_ALLOWED_CHATS=   # 群白名单 chat_id 逗号分隔（空=任意群可用）
                             # 例如: -1001234567890,-1009876543210

# 排行
DAILY_RANKING_HOUR=12        # UTC 小时；预热 -2h、订阅推送 -1h
DAILY_RANKING_TOP_N=5        # 每次取前 N 部
DAILY_RANKING_COMIC_TELEGRAPH=  # 排行发布目标 telegraph 账号 token
```

> 凭据（`WHOS_TV_*`、`EHBOT_TELEGRAM_TOKEN`、`STORE_BOT_TOKEN`）只放 `.env`。`.env`、运行时状态文件均已在 `.gitignore`，不要提交到 Git。

运行：

```bash
python bot.py
```

## 目录结构

```
bot.py                        # 主程序（命令处理 / 配额 / 排行 / 搜索 / 以图搜图 / 订阅）
scrapers/
  ehentai.py                  # EHentai 抓取（metadata / 分页 / 搜索）
  comic18.py                  # 18comic 抓取
  iqdb.py                     # IQDB 图搜（45s 硬截止）
  saucenao.py                 # Saucenao 图搜（可选）
  trace_moe.py                # 动画截图识别（歧义过滤）
  yandex_images.py            # Yandex 通用图搜 + 相似图下载
  whos_tv.py                  # Whos.tv AV 截图识别（番号 / 时间点 / 匹配帧）
  screenshot_ocr.py           # 本地 OCR（番号 / 水印 / 字幕）
publishers/
  telegraph.py                # Telegraph 发布（账号管理 / 图片上传 / 建页）
  jm_telegraph.py             # 画廊发布流水线（并发 / 内存控制 / 静态托管）
  reader.py
tests/                        # 单元测试（117 项）
trigger_ranking.py            # 手动触发补发当日排行（ops 脚本）
ranking_cache.json            # 当日排行预生成缓存（运行时生成）
subscriptions.json            # 订阅项（运行时生成）
subscription_notify_state.json# 订阅推送幂等状态（运行时生成）
usage_limits.json             # 每日配额计数（运行时生成）
```

## Whos.tv 账号池（与搜索 bot 共享）

Whos.tv 每次图搜扣积分，账号是消耗品。本 bot 不再只依赖一个账号，而是直接复用搜索 bot 的账号池文件：

```ini
WHOS_TV_ACCOUNTS_FILE=/opt/searchbot/whos_accounts.json
```

行为：
- 按顺序逐个账号 `登录 → /api/user/points/can-search` → 只对积分充足的账号上传一次图片
- 积分不足的账号直接跳过；某个账号登录/搜索报错也不会中断，继续试下一个
- 全部不可用时不输出任何 AV 结论（只保留 Yandex 等通用结果），不会假报命中
- 单账号模式（只填 `WHOS_TV_USERNAME/PASSWORD`）行为不变

账号池的上游维护在搜索 bot 侧：每天自动补 5 个新账号（`whos_daily_register.py`）+ 每日任务回补积分（`whos_daily_signin.py`）。`/health` 会显示池内账号数、当前账号积分和单次消耗。

> 传输层：Whos.tv 在 Cloudflare 后面，普道 `requests` 上传会直接 `403`，所以这里用 `curl_cffi` 的 Chrome 指纹 + 原生 multipart（`CurlMime`），依赖已在 `requirements.txt`。

## 群组模式

把 bot 拉进群使用。**两种模式的差异：**

| | 私聊（DM） | 群组 |
|---|---|---|
| 发链接 | 直接处理 ✅ | **必须 @bot 或回复 bot 的消息**才处理 |
| 发图片 | 直接处理 ✅ | **必须 @bot 或回复 bot 的消息**才处理 |
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
- 想用 Whos.tv AV 识别需自备 Whos.tv 账号并填 `WHOS_TV_USERNAME/PASSWORD`；不填则该引擎自动跳过

## 注意

- 18comic 登录限制画廊需要 `EHBOT_JM_COOKIES`
- EH 抓取走 cloudscraper 绕过防护，IP 频繁会被限流
- `trigger_ranking.py` 依赖 `/root/eh-reader-bot` 绝对路径，仅限本机 ops 使用
- 状态文件（`ranking_cache.json` / `subscriptions.json` / `subscription_notify_state.json` / `usage_limits.json`）为运行时数据，已在 `.gitignore`，备份时单独处理

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
| `/start` | 欢迎信息 |
| `/help` | 帮助 |
| `/daily` | 手动触发今日每日排行推送 |
| `/stats` | 使用统计 |
| `/cancel` | 取消当前任务 |

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

把 bot 拉进群，群成员直接发链接即可看图（配额按人独立计算，互不阻塞）。

1. **BotFather 关闭隐私模式**（必须，否则群里收不到普通链接消息）：
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
- 权限：群里所有成员可用（受每日配额限制）；私聊仍走白名单逻辑
- 并发：群里按**用户**加锁（不同成员同时发链接互不等待），私聊按会话加锁
- 群白名单为空 = 任何群可用；非空 = 仅限指定群

## 注意

- 18comic 登录限制画廊需要 `EHBOT_JM_COOKIES`
- EH 抓取走 cloudscraper 绕过防护，IP 频繁会被限流
- `trigger_ranking.py` 依赖 `/root/eh-reader-bot` 绝对路径，仅限本机 ops 使用

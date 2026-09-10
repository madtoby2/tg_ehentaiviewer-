"""
EH/18comic → Reader Bot
Telegram bot that converts EH/18comic links into self-hosted reading pages.
Runs alongside Hermes with its own bot token.
"""
import os
import re
import sys
import json
import html
import asyncio
import functools
import logging
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# Load .env (cloned deployments: ./setup.sh generates it). Existing env vars
# take precedence; .env does not override them.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / '.env')
except ImportError:
    pass

sys.path.insert(0, str(Path(__file__).parent))
from scrapers.ehentai import scrape_gallery as scrape_eh, is_eh_link, scrape_metadata as scrape_eh_meta, search_eh
from scrapers.comic18 import scrape_album as scrape_comic, is_comic_link, scrape_metadata as scrape_comic_meta, search_comic
from scrapers.saucenao import search as saucenao_search
from scrapers.iqdb import search_hard_timeout as iqdb_search
from scrapers.trace_moe import search as trace_moe_search
from scrapers.yandex_images import search as yandex_image_search, download_previews as yandex_download_previews
from scrapers.screenshot_ocr import ocr as screenshot_ocr, extract_av_codes
from scrapers.whos_tv import (search as whos_tv_search,
                              download_match_previews as whos_download_previews,
                              load_accounts as whos_tv_load_accounts, WhosTvClient)

from publishers.jm_telegraph import publish_jm_gallery, publish_eh_gallery

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get('EHBOT_TELEGRAM_TOKEN', '')
# Existing EHBOT_ALLOWED_USERS is treated as owner fallback for compatibility.
ALLOWED_USERS = os.environ.get('EHBOT_ALLOWED_USERS', '')
# Owner user IDs are exempt from daily quota. Defaults to existing allowed user(s), then current owner.
OWNER_USERS = os.environ.get('EHBOT_OWNER_USERS') or ALLOWED_USERS or '1601156128'
# Default is public access with quota for non-owner users. Set EHBOT_PUBLIC_ACCESS=0 to restore whitelist-only mode.
PUBLIC_ACCESS = os.environ.get('EHBOT_PUBLIC_ACCESS', '1').lower() not in ('0', 'false', 'no')
# Group mode: EHBOT_GROUP_MODE=1 enables using the bot inside group chats.
# EHBOT_GROUP_ALLOWED_CHATS (comma separated chat ids, optional): when set,
# only those groups may use the bot (empty = any group).
GROUP_MODE = os.environ.get('EHBOT_GROUP_MODE', '0').lower() in ('1', 'true', 'yes', 'on')
GROUP_ALLOWED_CHATS = {int(x.strip()) for x in os.environ.get('EHBOT_GROUP_ALLOWED_CHATS', '').split(',') if x.strip().lstrip('-').isdigit()}
DAILY_LIMIT = int(os.environ.get('EHBOT_DAILY_LIMIT', '10'))
# Reverse image search via Saucenao (https://saucenao.com, free API key).
# Comma-separated keys are rotated round-robin (N keys = 100N searches/day).
SAUCENAO_API_KEYS = [k.strip() for k in os.environ.get('SAUCENAO_API_KEY', '').split(',') if k.strip()]
WHOS_TV_USERNAME = os.getenv("WHOS_TV_USERNAME", "").strip()
WHOS_TV_PASSWORD = os.getenv("WHOS_TV_PASSWORD", "").strip()
WHOS_TV_MIN_SIMILARITY = float(os.getenv("WHOS_TV_MIN_SIMILARITY", "90"))
# 共享 Whos.tv 账号池（与搜索 bot 共用同一份文件）
WHOS_TV_ACCOUNTS_FILE = os.getenv("WHOS_TV_ACCOUNTS_FILE", "").strip()
WHOS_TV_ENABLED = bool(WHOS_TV_USERNAME and WHOS_TV_PASSWORD) or bool(
    WHOS_TV_ACCOUNTS_FILE and Path(WHOS_TV_ACCOUNTS_FILE).exists())
_sn_key_index = 0


def _next_saucenao_key() -> str | None:
    """Round-robin over configured Saucenao API keys."""
    global _sn_key_index
    if not SAUCENAO_API_KEYS:
        return None
    key = SAUCENAO_API_KEYS[_sn_key_index % len(SAUCENAO_API_KEYS)]
    _sn_key_index += 1
    return key
USAGE_FILE = Path(os.environ.get('EHBOT_USAGE_FILE', '/root/eh-reader-bot/usage_limits.json'))

RANKING_CACHE_FILE = Path(os.environ.get('EHBOT_RANKING_CACHE_FILE', '/root/eh-reader-bot/ranking_cache.json'))
SUBSCRIPTIONS_FILE = Path(os.environ.get('EHBOT_SUBSCRIPTIONS_FILE', '/root/eh-reader-bot/subscriptions.json'))
SUBSCRIPTION_NOTIFY_STATE_FILE = Path(os.environ.get('EHBOT_SUBSCRIPTION_NOTIFY_STATE_FILE', '/root/eh-reader-bot/subscription_notify_state.json'))
TZ_UTC8 = timezone(timedelta(hours=8))
MAX_PAGES = int(os.environ.get('EHBOT_MAX_PAGES', '0'))
MAX_WORKERS = int(os.environ.get('EHBOT_MAX_WORKERS', '5'))
# Page count threshold - don't process if total pages exceed this
MAX_RECOMMEND_PAGES = int(os.environ.get('EHBOT_MAX_RECOMMEND_PAGES', '200'))

# Track processing state per chat to avoid concurrent runs
_processing = set()

# Guro/taguri filter - tags to exclude from random recommendations
GURO_TAGS_EH = {
    'guro', 'gore', 'scat', 'vomit', 'body horror', 'dismemberment',
    'torture', 'snuff', 'fart', 'uro', 'watersports', 'piss',
    'necrophilia', 'insect', 'worm', 'tentacle', 'bestiality',
}
GURO_TAGS_CN = {'獵奇', '血腥', '暴力', '排泄', '屎', '尿', '蟲', '怪物'}
KOREAN_TAGS = {'韓漫', 'korean', '한국', 'manhwa'}
AI_TAGS = {'ai', 'ai:generated', 'ai:art', 'ai:assisted', 'ai:generated', 'generated', 'ai art', '人工智能'}

def _has_ai_tags(tags: list[str]) -> bool:
    """Check if any tag indicates AI-generated content."""
    for tag in tags:
        tag_lower = tag.lower().strip()
        # Check EH-style tags (e.g. "ai:generated", "ai:art")
        for suffix in [tag_lower, tag_lower.split(':')[-1]]:
            if suffix in AI_TAGS:
                return True
        # Check Chinese tags
        if 'ai' in tag_lower or '生成' in tag:
            return True
    return False

def _has_guro_tags(tags: list[str]) -> bool:
    """Check if any tag indicates guro/disturbing content."""
    for tag in tags:
        tag_lower = tag.lower().strip()
        # Check EH-style tags (e.g. "guro", "reclass:guro")
        for suffix in [tag_lower, tag_lower.split(':')[-1]]:
            if suffix in GURO_TAGS_EH:
                return True
        # Check Chinese tags
        for gtag in GURO_TAGS_CN:
            if gtag in tag:
                return True
    return False


def _has_korean_tags(tags: list[str]) -> bool:
    """Check if any tag indicates Korean (manhwa) content."""
    for tag in tags:
        tag_lower = tag.lower().strip()
        for ktag in KOREAN_TAGS:
            if ktag in tag_lower:
                return True
    return False


# ── Random Recommend (1 item, inline link) ──────────────

async def handle_recommend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Random recommend: fetch 1 item → scrape → publish → show inline link."""
    menu = _Menu(update)
    await menu.answer()

    user_id = menu.user.id if menu.user else 0
    if not chat_allowed(update):
        await menu.say("⚠️ 此 bot 未启用群组模式" if _chat_is_group(update.effective_chat) else "⚠️ 你没有权限使用此 bot")
        return

    lock = _lock_key(menu.chat_id, user_id)
    if lock in _processing:
        await menu.answer("⏳ 正在处理中，请稍等", show_alert=True)
        return

    ok, remaining = consume_daily_quota(user_id, 1)
    if not ok:
        await menu.say(
            f"🚫 今日次数已用完。普通用户每天最多 {DAILY_LIMIT} 次，按 UTC+8 零点重置。"
        )
        return

    loop = asyncio.get_event_loop()
    _processing.add(lock)
    try:
        await menu.say("🎲 正在随机推荐中...")

        rec = await loop.run_in_executor(None, fetch_eh_popular)
        if not rec:
            rec = await loop.run_in_executor(None, fetch_comic_popular)
        if not rec:
            await menu.say("❌ 获取推荐失败，请稍后再试")
            _processing.discard(lock)
            return

        url = rec['url']
        title = rec['title']
        source_name = "E-Hentai" if rec['source'] == 'eh' else "18comic"
        source_emoji = "🔞" if rec['source'] == 'eh' else "📖"
        total_pages = rec.get('total_pages', 0)

        await menu.say(
            f"🎲 <b>{source_name}</b>\n{source_emoji} {title}\n📄 {total_pages} 页\n📝 正在生成 Telegraph...",
            parse_mode='HTML'
        )

        if is_comic_link(url):
            result = await loop.run_in_executor(
                None, lambda: scrape_comic(url, max_workers=MAX_WORKERS, max_pages=MAX_PAGES)
            )
        else:
            result = await loop.run_in_executor(
                None, lambda: scrape_eh(url, max_workers=MAX_WORKERS, max_pages=MAX_PAGES)
            )

        if result.get('error'):
            await menu.say(f"❌ {result['error']}")
            return

        image_details = result.get('image_details', [])
        image_urls = result['image_urls']

        if is_comic_link(url) and image_details:
            pub_result = await loop.run_in_executor(
                None, lambda: publish_jm_gallery(title, image_details, max_workers=MAX_WORKERS)
            )
        else:
            pub_result = await loop.run_in_executor(
                None, lambda: publish_eh_gallery(title, image_urls, max_workers=MAX_WORKERS)
            )

        page_url = pub_result.get('url') if pub_result else None
        published = pub_result.get('uploaded', pub_result.get('downloaded', 0)) if pub_result else 0

        link = page_url or url
        msg = (
            f"🎲 <b>随机推荐 - {source_name}</b>\n"
            f"{source_emoji} <a href=\"{link}\">{title}</a>\n"
            f"📄 {published}/{total_pages} 页"
        )

        btn = InlineKeyboardMarkup([[InlineKeyboardButton("🎲 再推荐一个", callback_data="recommend")]])
        await menu.say(msg, parse_mode='HTML', disable_web_page_preview=True, reply_markup=btn)

    except Exception as e:
        logger.exception(f"Recommend failed: {e}")
        await menu.say(f"❌ 处理失败：{str(e)[:200]}")
    finally:
        _processing.discard(lock)


def _parse_user_ids(raw: str) -> set[int]:
    ids = set()
    for item in raw.split(','):
        item = item.strip()
        if not item:
            continue
        try:
            ids.add(int(item))
        except ValueError:
            logger.warning(f"Invalid user id ignored: {item}")
    return ids


def is_allowed(user_id: int) -> bool:
    if PUBLIC_ACCESS:
        return True
    allowed = _parse_user_ids(ALLOWED_USERS) | _parse_user_ids(OWNER_USERS)
    if not allowed:
        return True
    return user_id in allowed


def is_owner(user_id: int) -> bool:
    return user_id in _parse_user_ids(OWNER_USERS)


def _chat_is_group(chat) -> bool:
    return bool(chat) and getattr(chat, 'type', '') in ('group', 'supergroup')


def chat_allowed(update) -> bool:
    """Chat-level access control. Private chats keep the user whitelist logic;
    group chats require group mode and (optionally) an allowed-chat list."""
    chat = update.effective_chat
    if not chat:
        return False
    if not _chat_is_group(chat):
        user_id = update.effective_user.id if update.effective_user else 0
        return is_allowed(user_id)
    if not GROUP_MODE:
        return False
    if GROUP_ALLOWED_CHATS and chat.id not in GROUP_ALLOWED_CHATS:
        return False
    return True


def _lock_key(chat_id: int, user_id: int) -> int:
    """Per-chat lock in DM (sequential per chat), per-user lock in groups so
    different members never block each other."""
    return user_id if GROUP_MODE else chat_id


class _Menu:
    """UI adapter: entry handlers work both as inline buttons (callback_query)
    and as fixed reply-keyboard buttons (plain text message).

    - callback trigger: answer() no-ops, say() edits the original message
    - text trigger:     answer() no-ops, say() replies with a new message
    """

    def __init__(self, update: Update):
        q = update.callback_query
        self.is_callback = q is not None
        self.user = q.from_user if q else update.effective_user
        self.chat_id = update.effective_chat.id if update.effective_chat else 0
        self._q = q
        self._msg = update.message
        self._update = update

    async def answer(self, text: str | None = None, show_alert: bool = False):
        if self.is_callback:
            await self._q.answer(text, show_alert=show_alert)

    async def say(self, text: str, **kw):
        if self.is_callback:
            return await self._q.edit_message_text(text, **kw)
        return await self._msg.reply_text(text, **kw)


def _today_utc8() -> str:
    return datetime.now(TZ_UTC8).strftime('%Y-%m-%d')


def _load_usage() -> dict:
    try:
        if USAGE_FILE.exists():
            with open(USAGE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning(f"Failed to load usage file: {e}")
    return {}


def _save_usage(data: dict):
    try:
        USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = USAGE_FILE.with_suffix('.tmp')
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, USAGE_FILE)
    except Exception as e:
        logger.error(f"Failed to save usage file: {e}")


def consume_daily_quota(user_id: int, amount: int = 1) -> tuple[bool, int]:
    """Consume quota for non-owner users. Returns (allowed, remaining)."""
    if is_owner(user_id) or DAILY_LIMIT <= 0:
        return True, DAILY_LIMIT

    today = _today_utc8()
    usage = _load_usage()
    # Keep only today's bucket so the file does not grow forever.
    day_usage = usage.get(today, {}) if isinstance(usage.get(today, {}), dict) else {}
    used = int(day_usage.get(str(user_id), 0))
    if used + amount > DAILY_LIMIT:
        return False, max(0, DAILY_LIMIT - used)

    day_usage[str(user_id)] = used + amount
    _save_usage({today: day_usage})
    return True, max(0, DAILY_LIMIT - used - amount)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else 0

    # Fixed reply keyboard (custom keyboard) — buttons send plain text,
    # routed by handle_menu_button.
    rows = [
        [KeyboardButton("🎲 随机推荐"), KeyboardButton("🔍 标签搜索")],
        [KeyboardButton("🖼 图片搜索"), KeyboardButton("📊 今日额度")],
    ]
    if is_owner(user_id):
        rows.append([KeyboardButton("🏆 当日排行")])
    keyboard = ReplyKeyboardMarkup(rows, resize_keyboard=True)

    group_hint = (
        "\n\n📢 <b>群组模式已开启：</b>把我拉进群后，<b>@我并发送链接/图片</b>，或<b>回复我的消息</b>发送链接/图片。"
        if GROUP_MODE else ""
    )
    await update.message.reply_text(
        "🎴 <b>hentaiviewer</b>\n\n"
        "EH / 18comic 链接转在线阅读器，自动解析图片生成 Telegraph 页面。\n\n"
        "<b>📖 链接生成阅读页</b>\n"
        "直接发送以下链接给我：\n"
        "• <code>e-hentai.org/g/1234567/abc/</code>\n"
        "• <code>18comic.vip/album/12345/</code>\n\n"
        "<b>🔍 任意图片查出处</b>\n"
        "直接发送漫画、插画、动画截图、AV 截图、真人或商品图片：\n"
        "• 通用相似图与网页来源\n"
        "• Whos.tv AV 番号、片名、相似度、匹配帧与精准时间点\n"
        "• 动画名称、集数和时间点\n"
        "• OCR 提取截图中的番号、水印和文字\n"
        "支持图片消息及 JPG/PNG/WebP 原图文件。\n"
        "匹配到 EH / 18comic 后，还可点击「📖 生成阅读页」。\n\n"
        "也可以点击下方固定按钮：随机推荐、标签搜索、图片搜索、今日额度。"
        f"{group_hint}\n\n"
        "更多精彩尽在黄油频道 🧈 @huangyoustore",
        parse_mode='HTML',
        reply_markup=keyboard
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎴 <b>hentaiviewer 使用说明</b>\n\n"
        "自动抓取 EH / 18comic 画廊，生成在线阅读页面。\n\n"
        "<b>支持格式：</b>\n"
        "• <code>e-hentai.org/g/&lt;id&gt;/&lt;token&gt;/</code>\n"
        "• <code>exhentai.org/g/&lt;id&gt;/&lt;token&gt;/</code>\n"
        "• <code>18comic.vip/album/&lt;id&gt;/</code>\n"
        "• <code>18comic.vip/photo/&lt;id&gt;/</code>\n\n"
        "<b>命令：</b>\n"
        "• <code>/start</code> - 欢迎\n"
        "• <code>/help</code> - 帮助\n"
        "• <code>/daily</code> - 查看今日剩余次数\n"
        "• <code>/subscribe 类型 关键词</code> - 订阅作者/标签/女优\n"
        "• <code>/subscriptions</code> - 查看订阅\n"
        "• <code>/health</code> - 管理员健康检查\n\n"
        "🧈 <b>黄油频道</b> @huangyoustore",
        parse_mode='HTML'
    )


# Fixed reply-keyboard (custom keyboard) button labels → handler routing.
# The buttons send plain text; we match exact labels here.
# Values are function NAMES resolved at call time (avoids definition-order
# issues at module load).
MENU_ROUTES = {
    "🎲 随机推荐": "handle_recommend",
    "🔍 标签搜索": "handle_search_start",
    "🏆 当日排行": "handle_ranking",
    "📊 今日额度": "daily_command",
    "🖼 图片搜索": "image_search_prompt",
}


async def image_search_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Explain image-search input without consuming quota."""
    user_id = update.effective_user.id if update.effective_user else 0
    if is_owner(user_id):
        quota = "♾️ Owner 不限次数"
    else:
        _, remaining = consume_daily_quota(user_id, 0)
        quota = f"今日剩余：{remaining} / {DAILY_LIMIT} 次"
    await update.message.reply_text(
        "🖼 <b>图片搜索</b>\n\n"
        "直接发送图片，或发送 JPG / PNG / WebP 原图文件。\n"
        "支持 Whos.tv AV 匹配帧、动画识别和网页相似图来源。\n\n"
        f"📊 {quota}",
        parse_mode='HTML',
    )


async def handle_menu_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route fixed reply-keyboard button presses to their handlers."""
    if not update.message or not update.message.text:
        return
    handler_name = MENU_ROUTES.get(update.message.text.strip())
    if handler_name:
        handler = globals().get(handler_name)
        if handler:
            await handler(update, context)


def _is_addressed_to_bot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Group-mode trigger: only respond when the message @mentions the bot or
    replies to one of the bot's own messages."""
    msg = update.message
    if not msg:
        return False
    # Reply to one of our own messages
    reply = msg.reply_to_message
    bot_id = getattr(context.bot, "id", None)
    if reply and reply.from_user and bot_id and reply.from_user.id == bot_id:
        return True
    # @mention of the bot
    username = getattr(context.bot, "username", None)
    for ent in (msg.entities or []):
        if ent.type == "text_mention":
            if bot_id and ent.user and ent.user.id == bot_id:
                return True
        elif ent.type == "mention" and username:
            raw = msg.text[ent.offset:ent.offset + ent.length].lstrip("@")
            if raw.lower() == username.lower():
                return True
    return False


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    # Group mode: only respond to @mention or reply-to-bot messages.
    # Private chat behavior stays as-is.
    if _chat_is_group(update.effective_chat) and GROUP_MODE:
        if not _is_addressed_to_bot(update, context):
            return

    user_id = update.effective_user.id if update.effective_user else 0
    if not chat_allowed(update):
        msg = "⚠️ 此 bot 未启用群组模式" if _chat_is_group(update.effective_chat) else "⚠️ 你没有权限使用此 bot"
        await update.message.reply_text(msg)
        return

    chat_id = update.effective_chat.id if update.effective_chat else 0
    lock = _lock_key(chat_id, user_id)
    if lock in _processing:
        await update.message.reply_text("⏳ 正在处理中，请等待完成后再发新链接")
        return

    text = update.message.text.strip()

    # Find all links
    urls = re.findall(r'https?://[^\s]+', text)
    supported = [u for u in urls if is_eh_link(u) or is_comic_link(u)]

    if not supported:
        if any(kw in text.lower() for kw in ['e-hentai', 'exhentai', '18comic', 'jmcomic', 'e站']):
            await update.message.reply_text(
                "❌ 链接格式不对。请发送完整链接：\n"
                "`https://e-hentai.org/g/1234567/abc/`\n"
                "`https://18comic.vip/album/12345/`"
            )
        return

    # Process each link. Non-owner users consume one quota per accepted link.
    for i, url in enumerate(supported):
        ok, remaining = consume_daily_quota(user_id, 1)
        if not ok:
            await update.message.reply_text(
                f"🚫 今日次数已用完。普通用户每天最多 {DAILY_LIMIT} 次，按 UTC+8 零点重置。"
            )
            break

        multi = len(supported) > 1
        status = await update.message.reply_text(
            f"📦 处理中 ({i+1}/{len(supported)})..." if multi else "🔄 正在处理，请稍候..."
        )

        _processing.add(lock)
        try:
            await _process(update, context, url, status)
        except Exception as e:
            logger.exception(f"Failed: {url}")
            await status.edit_text(f"❌ 处理失败：{str(e)[:200]}")
        finally:
            _processing.discard(lock)


async def _process(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str, status_msg):
    """Process link: scrape → download → publish."""
    # Step 1: Scrape
    await status_msg.edit_text("📥 正在获取画廊信息...")

    loop = asyncio.get_event_loop()
    if is_eh_link(url):
        result = await loop.run_in_executor(
            None, lambda: scrape_eh(url, max_workers=MAX_WORKERS, max_pages=MAX_PAGES)
        )
    else:
        result = await loop.run_in_executor(
            None, lambda: scrape_comic(url, max_workers=MAX_WORKERS, max_pages=MAX_PAGES)
        )

    if result.get('error'):
        await status_msg.edit_text(f"❌ {result['error']}")
        return

    title = result['title']
    image_urls = result['image_urls']
    image_details = result.get('image_details', [])
    total = result.get('total_pages', len(image_urls))
    pages = result['pages']

    await status_msg.edit_text(
        f"📥 {title}\n"
        f"共 {pages}/{total} 页\n"
        f"📝 正在生成 Telegraph 页面..."
    )

    # Step 2: Publish Telegraph page. JM/18comic images may be scrambled, so
    # decode them locally first and publish the decoded temporary static URLs.
    if is_comic_link(url) and image_details:
        pub_result = await loop.run_in_executor(
            None, lambda: publish_jm_gallery(title, image_details, max_workers=MAX_WORKERS)
        )
    else:
        pub_result = await loop.run_in_executor(
            None, lambda: publish_eh_gallery(title, image_urls, max_workers=MAX_WORKERS)
        )

    if pub_result.get('error'):
        await status_msg.edit_text(f"❌ {pub_result['error']}")
        return

    page_url = pub_result.get('url')
    published = pub_result.get('uploaded', pub_result.get('downloaded', 0))

    if not page_url:
        await status_msg.edit_text("❌ 创建阅读器失败")
        return

    # Success!
    await status_msg.delete()

    source_emoji = "🔞" if is_eh_link(url) else "📖"
    source_name = "E-Hentai" if is_eh_link(url) else "18comic"

    msg = (
        f"{source_emoji} <a href=\"{page_url}\">{title}</a>\n"
        f"📄 {published}/{pages} 页"
    )
    # effective_message works for both plain messages and callback triggers
    await update.effective_message.reply_text(msg, parse_mode='HTML', disable_web_page_preview=True)


class _CallbackStatus:
    """status_msg adapter for callback-triggered _process runs: edits the
    callback message instead of a sent status message."""

    def __init__(self, query):
        self._q = query

    async def edit_text(self, text: str, **kw):
        await self._q.edit_message_text(text, **kw)

    async def delete(self):
        pass  # keep the message; result is replied below it


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reverse image search: send a photo → Saucenao matches → optional
    'generate reader page' button for e-hentai/18comic matches."""
    message = update.message
    document = message.document if message else None
    image_document = bool(document and (document.mime_type or "").lower() in (
        "image/jpeg", "image/png", "image/webp",
    ))
    if not message or (not message.photo and not image_document):
        return

    # Group mode: same trigger rules as links (@mention or reply-to-bot).
    if _chat_is_group(update.effective_chat) and GROUP_MODE:
        if not _is_addressed_to_bot(update, context):
            return

    user_id = update.effective_user.id if update.effective_user else 0
    if not chat_allowed(update):
        msg = "⚠️ 此 bot 未启用群组模式" if _chat_is_group(update.effective_chat) else "⚠️ 你没有权限使用此 bot"
        await update.message.reply_text(msg)
        return

    chat_id = update.effective_chat.id if update.effective_chat else 0
    lock = _lock_key(chat_id, user_id)
    if lock in _processing:
        await update.message.reply_text("⏳ 正在处理中，请等待完成后再发")
        return

    # IQDB is free/no-key and always available. Saucenao is added when one
    # or more keys are configured.

    ok, remaining = consume_daily_quota(user_id, 1)
    if not ok:
        await update.message.reply_text(
            f"🚫 今日次数已用完。普通用户每天最多 {DAILY_LIMIT} 次，按 UTC+8 零点重置。"
        )
        return

    status = await update.message.reply_text("🔍 正在下载图片...")
    _processing.add(lock)
    tmpdir = tempfile.mkdtemp(prefix="ris_")
    try:
        image_obj = document if image_document else update.message.photo[-1]
        file = await context.bot.get_file(image_obj.file_id)
        suffix = Path(document.file_name or "").suffix.lower() if image_document else ".jpg"
        if suffix not in (".jpg", ".jpeg", ".png", ".webp"):
            suffix = ".jpg"
        img_path = Path(tmpdir) / f"query{suffix}"
        await file.download_to_drive(custom_path=str(img_path))

        loop = asyncio.get_event_loop()
        await status.edit_text("🔍 正在搜图匹配中...")

        # Run general and specialist engines in parallel. IQDB gets its own
        # hard process deadline; every local file is still owned by tmpdir.
        tasks = [
            loop.run_in_executor(None, iqdb_search, str(img_path)),
            loop.run_in_executor(None, trace_moe_search, str(img_path)),
            loop.run_in_executor(None, yandex_image_search, str(img_path)),
            loop.run_in_executor(None, screenshot_ocr, str(img_path)),
        ]
        whos_index = None
        whos_future = None
        if WHOS_TV_ENABLED:
            whos_index = len(tasks)
            whos_future = loop.run_in_executor(
                None, functools.partial(
                    whos_tv_search, WHOS_TV_USERNAME, WHOS_TV_PASSWORD, str(img_path),
                    accounts_file=WHOS_TV_ACCOUNTS_FILE),
            )
            tasks.append(whos_future)
        sn_key = _next_saucenao_key()
        sn_index = None
        if sn_key:
            sn_index = len(tasks)
            tasks.append(loop.run_in_executor(None, saucenao_search, sn_key, str(img_path)))
        if whos_future is not None:
            try:
                early_whos = await whos_future
                trusted_matches = [
                    match for match in (early_whos.get('matches') or [])
                    if float(match.get('similarity') or 0) >= WHOS_TV_MIN_SIMILARITY
                ] if isinstance(early_whos, dict) else []
                if trusted_matches:
                    top = trusted_matches[0]
                    at = f" · {top.get('at')}" if top.get('at') else ""
                    title = f"\n{top.get('title')}" if top.get('title') and top.get('title') != top.get('code') else ""
                    await status.edit_text(
                        f"🎯 Whos.tv 已命中：{top['code']} · {top['similarity']:.1f}%{at}{title}\n"
                        "🔍 正在补充其他来源..."
                    )
            except Exception:
                pass
        done = await asyncio.gather(*tasks, return_exceptions=True)

        iq_results = done[0] if isinstance(done[0], list) else []
        anime_results = done[1] if isinstance(done[1], list) else []
        yandex_result = done[2] if isinstance(done[2], dict) else None
        ocr_text = done[3] if isinstance(done[3], str) else ""
        whos_result = done[whos_index] if whos_index is not None and isinstance(done[whos_index], dict) else None
        whos_candidates = list((whos_result or {}).get('matches') or [])
        if whos_result:
            whos_result = {
                **whos_result,
                'matches': [
                    match for match in (whos_result.get('matches') or [])
                    if float(match.get('similarity') or 0) >= WHOS_TV_MIN_SIMILARITY
                ],
            }
        sauce_results = done[sn_index] if sn_index is not None and isinstance(done[sn_index], list) else []
        source_results = list(iq_results) + list(sauce_results)


        for r in source_results:
            r.setdefault('index_name', 'IQDB')
        source_results.sort(key=lambda r: r['similarity'], reverse=True)
        source_results = source_results[:5]
        av_codes = extract_av_codes(ocr_text)

        if not source_results and not anime_results and not yandex_result and not av_codes and not whos_candidates:
            await status.edit_text("❌ 未找到匹配的图片来源")
            return

        lines = ["🔍 <b>图片出处搜索结果</b>\n"]
        if whos_result and whos_result.get('matches'):
            lines.append("🎯 <b>Whos.tv AV 画面匹配：</b>")
            for match in whos_result['matches'][:3]:
                at = f" · <code>{html.escape(match['at'])}</code>" if match.get('at') else ""
                title = match.get('title') or match['code']
                if title != match['code']:
                    title = f"\n  └ {html.escape(title)}"
                else:
                    title = ""
                lines.append(
                    f"• <a href=\"{html.escape(match['url'])}\"><code>{html.escape(match['code'])}</code></a>"
                    f" · {match['similarity']:.1f}%{at}{title}"
                )
            if whos_result.get('result_url'):
                lines.append(f"🔎 <a href=\"{html.escape(whos_result['result_url'])}\">查看 Whos.tv 完整结果</a>")
        elif whos_candidates:
            lines.append("⚠️ <b>Whos.tv 低置信候选（仅供画面对比）：</b>")
            for match in whos_candidates[:3]:
                at = f" · <code>{html.escape(match['at'])}</code>" if match.get('at') else ""
                lines.append(
                    f"• <a href=\"{html.escape(match['url'])}\"><code>{html.escape(match['code'])}</code></a>"
                    f" · {match['similarity']:.1f}%{at}"
                )

        if av_codes:
            lines.append("🎬 <b>OCR 识别到番号：</b> " + "、".join(f"<code>{c}</code>" for c in av_codes[:5]))

        if anime_results:
            a = anime_results[0]
            episode = f" 第 {a['episode']} 集" if a.get('episode') is not None else ""
            preview = f" · <a href=\"{a['preview']}\">预览</a>" if a.get('preview') else ""
            lines.append(
                f"📺 <b>动画：</b>{a['title']}{episode} {a['at']} "
                f"({a['similarity']:.1f}%){preview}"
            )

        for i, r in enumerate(source_results):
            link = r['urls'][0] if r['urls'] else ""
            title = r['title']
            lines.append(f"{i+1}. [{r['index_name']} {r['similarity']:.1f}%] {title}")
            if link:
                lines.append(f"   <a href=\"{link}\">🔗 原图链接</a>")

        if yandex_result:
            sites = yandex_result.get('sites') or []
            if sites:
                lines.append("🌐 <b>网页来源：</b>")
                for site in sites[:5]:
                    lines.append(
                        f"• <a href=\"{site['url']}\">{site['title']}</a>"
                        f" · {site.get('domain', '')}"
                    )
            lines.append(f"🔎 <a href=\"{yandex_result['search_url']}\">查看更多 Yandex 相似结果</a>")

        # Only gallery matches get the reader-page action.
        eh_url = next(
            (u for r in source_results for u in r['urls'] if is_eh_link(u) or is_comic_link(u)),
            None,
        )
        btns = []
        if eh_url:
            btns.append([InlineKeyboardButton("📖 生成阅读页", callback_data=f"ris_read:{eh_url}")])
        await status.edit_text(
            "\n".join(lines),
            parse_mode='HTML',
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup(btns) if btns else None,
        )

        # Send Whos.tv matched frames first for direct visual confirmation.
        preview_matches = (whos_result or {}).get('matches') or whos_candidates[:3]
        if preview_matches:
            whos_previews = await loop.run_in_executor(
                None, whos_download_previews,
                preview_matches[:3], str(Path(tmpdir) / 'whos_matches'), 3,
            )
            if whos_previews:
                for item in whos_previews:
                    at = f" · {html.escape(item['at'])}" if item.get('at') else ""
                    trusted = item['similarity'] >= WHOS_TV_MIN_SIMILARITY
                    code = html.escape(item['code'])
                    video_url = item.get('url') or ''
                    frame_url = item.get('frame_url') or ''
                    confidence = "高置信匹配" if trusted else "低置信候选 · 仅供对比"
                    caption = (
                        f"{'🎯' if trusted else '⚠️'} <b>{code}</b>\n"
                        f"{confidence}\n"
                        f"相似度：<b>{item['similarity']:.1f}%</b>{at}"
                    )
                    buttons = []
                    row = []
                    if video_url:
                        row.append(InlineKeyboardButton("🎬 查看影片", url=video_url))
                    if frame_url:
                        row.append(InlineKeyboardButton("🖼 查看匹配帧", url=frame_url))
                    if row:
                        buttons.append(row)
                    with open(item['path'], 'rb') as photo:
                        await context.bot.send_photo(
                            chat_id=chat_id, photo=photo, caption=caption,
                            parse_mode='HTML',
                            reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
                        )

        # Send up to four directly matched images so the user can compare
        # visually. Files live under this task's tmpdir and are deleted in the
        # outer finally after Telegram finishes reading them.
        if yandex_result and yandex_result.get('sites'):
            previews = await loop.run_in_executor(
                None, yandex_download_previews,
                yandex_result['sites'], str(Path(tmpdir) / 'matches'), 4,
            )
            if previews:
                media = []
                handles = []
                try:
                    for i, item in enumerate(previews, 1):
                        fh = open(item['path'], 'rb')
                        handles.append(fh)
                        caption = (
                            f"候选 {i}：{html.escape(item['title'])}\n"
                            f"来源：{html.escape(item.get('domain', ''))}\n"
                            f"{html.escape(item['url'])}"
                        )
                        media.append(InputMediaPhoto(media=fh, caption=caption, parse_mode='HTML'))
                    if len(media) == 1:
                        await context.bot.send_photo(
                            chat_id=chat_id,
                            photo=handles[0],
                            caption=media[0].caption,
                            parse_mode='HTML',
                        )
                    else:
                        await context.bot.send_media_group(chat_id=chat_id, media=media)
                finally:
                    for fh in handles:
                        fh.close()
    except Exception as e:
        logger.exception(f"Reverse image search failed: {e}")
        await status.edit_text(f"❌ 搜图失败：{str(e)[:200]}")
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
        _processing.discard(lock)


async def handle_ris_read(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """'生成阅读页' button on a reverse-search result → run gallery pipeline."""
    query = update.callback_query
    await query.answer()
    url = query.data.split(":", 1)[1]

    user_id = query.from_user.id if query.from_user else 0
    if not chat_allowed(update):
        await query.edit_message_text("⚠️ 你没有权限使用此 bot")
        return

    lock = _lock_key(update.effective_chat.id if update.effective_chat else 0, user_id)
    if lock in _processing:
        await query.answer("⏳ 正在处理中，请稍等", show_alert=True)
        return

    ok, remaining = consume_daily_quota(user_id, 1)
    if not ok:
        await query.edit_message_text(
            f"🚫 今日次数已用完。普通用户每天最多 {DAILY_LIMIT} 次，按 UTC+8 零点重置。"
        )
        return

    _processing.add(lock)
    try:
        await _process(update, context, url, _CallbackStatus(query))
    except Exception as e:
        logger.exception(f"RIS read failed: {url}")
        await query.edit_message_text(f"❌ 处理失败：{str(e)[:200]}")
    finally:
        _processing.discard(lock)


def _load_subscriptions():
    try:
        data = json.loads(SUBSCRIPTIONS_FILE.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_subscriptions(data):
    SUBSCRIPTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SUBSCRIPTIONS_FILE.with_name(f".{SUBSCRIPTIONS_FILE.name}.{os.getpid()}.{id(data)}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, SUBSCRIPTIONS_FILE)


def list_subscriptions(user_id):
    return _load_subscriptions().get(str(user_id), [])


def add_subscription(user_id, kind, term):
    data = _load_subscriptions(); key = str(user_id)
    item = {'type': kind, 'term': term.strip()}
    current = data.setdefault(key, [])
    if any(x.get('type') == item['type'] and x.get('term', '').casefold() == item['term'].casefold() for x in current):
        return False
    current.append(item); _save_subscriptions(data); return True


def remove_subscription(user_id, kind, term):
    data = _load_subscriptions(); key = str(user_id); current = data.get(key, [])
    kept = [x for x in current if not (x.get('type') == kind and x.get('term', '').casefold() == term.strip().casefold())]
    if len(kept) == len(current):
        return False
    data[key] = kept; _save_subscriptions(data); return True


def match_subscriptions(subscriptions, items):
    hits = []
    for subscription in subscriptions:
        term = subscription.get('term', '').casefold()
        for item in items:
            haystack = ' '.join([item.get('title', ''), *[str(x) for x in item.get('tags', [])]]).casefold()
            if term and term in haystack:
                hits.append({'subscription': subscription, 'item': item})
                break
    return hits


async def subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not chat_allowed(update):
        await update.message.reply_text("⚠️ 你没有权限使用此 bot")
        return
    if len(context.args) < 2 or context.args[0] not in ('作者', '标签', '女优'):
        await update.message.reply_text("用法：/subscribe 类型 关键词\n类型支持：作者、标签、女优")
        return
    kind, term = context.args[0], ' '.join(context.args[1:]).strip()
    added = await asyncio.get_event_loop().run_in_executor(None, add_subscription, update.effective_user.id, kind, term)
    await update.message.reply_text(("✅ 已订阅：" if added else "ℹ️ 已存在：") + f"{kind} · {term}")


async def unsubscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not chat_allowed(update):
        await update.message.reply_text("⚠️ 你没有权限使用此 bot")
        return
    if len(context.args) < 2 or context.args[0] not in ('作者', '标签', '女优'):
        await update.message.reply_text("用法：/unsubscribe 类型 关键词")
        return
    kind, term = context.args[0], ' '.join(context.args[1:]).strip()
    removed = await asyncio.get_event_loop().run_in_executor(None, remove_subscription, update.effective_user.id, kind, term)
    await update.message.reply_text(("✅ 已取消：" if removed else "ℹ️ 未找到：") + f"{kind} · {term}")


async def subscriptions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not chat_allowed(update):
        await update.message.reply_text("⚠️ 你没有权限使用此 bot")
        return
    items = await asyncio.get_event_loop().run_in_executor(None, list_subscriptions, update.effective_user.id)
    if not items:
        await update.message.reply_text("暂无订阅。\n添加：/subscribe 标签 纯爱")
        return
    await update.message.reply_text("🔔 我的订阅\n" + '\n'.join(f"• {x['type']} · {x['term']}" for x in items))


async def notify_subscription_matches(context: ContextTypes.DEFAULT_TYPE):
    cache = _load_ranking_cache()
    items = (cache.get('eh') or []) + (cache.get('comic') or [])
    try:
        sent_state = json.loads(SUBSCRIPTION_NOTIFY_STATE_FILE.read_text(encoding='utf-8'))
    except Exception:
        sent_state = {}
    def save_state():
        SUBSCRIPTION_NOTIFY_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = SUBSCRIPTION_NOTIFY_STATE_FILE.with_name(
            f".{SUBSCRIPTION_NOTIFY_STATE_FILE.name}.{os.getpid()}.{id(sent_state)}.tmp"
        )
        tmp.write_text(json.dumps(sent_state, ensure_ascii=False), encoding='utf-8')
        os.replace(tmp, SUBSCRIPTION_NOTIFY_STATE_FILE)

    for user_id, subscriptions in _load_subscriptions().items():
        sent = set(sent_state.get(user_id, []))
        for hit in match_subscriptions(subscriptions, items):
            item = hit['item']; sub = hit['subscription']; link = item.get('tg_url') or item.get('url')
            identity = item.get('url') or link
            if not link or identity in sent:
                continue
            try:
                await context.bot.send_message(
                    chat_id=int(user_id),
                    text=f"🔔 订阅命中：{sub['type']} · {sub['term']}\n<a href=\"{html.escape(link)}\">{html.escape(item.get('title', '?'))}</a>",
                    parse_mode='HTML', disable_web_page_preview=True,
                )
            except Exception as exc:
                logger.warning("Subscription notification failed for user %s: %s", user_id, type(exc).__name__)
                continue
            sent.add(identity)
            sent_state[user_id] = list(sent)[-500:]
            save_state()
        sent_state[user_id] = list(sent)[-500:]


async def health_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else 0
    if not is_owner(user_id):
        await update.message.reply_text("⚠️ /health 仅管理员可用")
        return
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, _health_probe)
    labels = {
        'telegraph': 'Telegraph', 'whos': 'Whos.tv', 'catbox': 'Catbox',
        'temp': '临时文件',
    }
    lines = ["🩺 <b>EH Reader 健康状态</b>"]
    for key in ('telegraph', 'whos', 'catbox', 'temp'):
        ok, detail = data.get(key, (False, '未知'))
        lines.append(f"{'✅' if ok else '⚠️'} <b>{labels[key]}</b>：{html.escape(str(detail))}")
    await update.message.reply_text("\n".join(lines), parse_mode='HTML')


def _health_probe():
    import glob

    import requests
    result = {}
    try:
        response = requests.get('https://api.telegra.ph/getPageList', timeout=8)
        result['telegraph'] = (response.status_code < 500, f"HTTP {response.status_code}")
    except Exception as exc:
        result['telegraph'] = (False, type(exc).__name__)
    try:
        response = requests.get('https://catbox.moe/', timeout=8)
        result['catbox'] = (response.status_code < 500, f"HTTP {response.status_code}")
    except Exception as exc:
        result['catbox'] = (False, type(exc).__name__)
    if WHOS_TV_ENABLED:
        try:
            accounts = whos_tv_load_accounts(
                WHOS_TV_ACCOUNTS_FILE, WHOS_TV_USERNAME, WHOS_TV_PASSWORD)
            ok = False
            detail = ''
            for account in accounts:
                client = WhosTvClient(account['username'], account['password'])
                try:
                    client.login()
                    allowed = client.can_search()
                    if not allowed.get('can_search'):
                        continue
                    profile = client.profile()
                    ok = True
                    detail = (f"{len(accounts)}个账号，当前 {profile.get('points_balance')}积分，"
                              f"单次{allowed.get('required_points')}积分")
                    break
                finally:
                    client.close()
            if not ok:
                detail = f'{len(accounts)}个账号，均积分不足' if accounts else '未配置'
            result['whos'] = (ok, detail)
        except Exception as exc:
            result['whos'] = (False, type(exc).__name__)
    else:
        result['whos'] = (False, '未配置')

    tmp_count = len(glob.glob('/tmp/ris_*'))
    result['temp'] = (tmp_count == 0, str(tmp_count))
    return result


async def daily_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else 0
    if is_owner(user_id):
        await update.message.reply_text("♾️ 没有限制。")
        return
    ok, remaining = consume_daily_quota(user_id, 0)
    await update.message.reply_text(
        f"📊 今日剩余次数：**{remaining} / {DAILY_LIMIT}**\n"
        f"（UTC+8 每日自动重置）"
    )


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel pending search or other operations."""
    cleared = False
    if context.user_data.get('awaiting_search_tags'):
        context.user_data['awaiting_search_tags'] = False
        cleared = True
    if cleared:
        await update.message.reply_text("✅ 已取消")
    else:
        await update.message.reply_text("❌ 没有正在进行的操作")


def fetch_eh_popular() -> dict | None:
    """Fetch a random non-guro popular gallery from e-hentai. Returns {url, title, tags, total_pages, error}."""
    import cloudscraper
    from bs4 import BeautifulSoup
    from urllib.parse import urljoin

    scraper = cloudscraper.create_scraper()
    try:
        resp = scraper.get("https://e-hentai.org/popular", timeout=30)
        resp.raise_for_status()
    except Exception as e:
        logger.warning(f"Failed to fetch EH popular: {e}")
        return None

    soup = BeautifulSoup(resp.text, 'lxml')
    links = []
    for a_tag in soup.find_all('a', href=True):
        href = urljoin("https://e-hentai.org", a_tag['href'])
        if is_eh_link(href):
            links.append(href)

    if not links:
        return None

    # Try each link until we find one that has metadata and isn't guro
    import random as _random
    _random.shuffle(links)
    for link in links:
        meta = scrape_eh_meta(link)
        if meta.get('error'):
            continue
        if _has_guro_tags(meta.get('tags', [])):
            continue
        return {
            'url': link,
            'title': meta.get('title', ''),
            'tags': meta.get('tags', []),
            'source': 'eh',
            'total_pages': meta.get('total_pages', 0),
        }
    return None


def fetch_comic_popular() -> dict | None:
    """Fetch a random non-guro popular album from 18comic. Returns {url, title, tags, total_pages, error}."""
    from scrapers.comic18 import _get_client
    try:
        client = _get_client()
        result = client.search_site('', page=1)
        content = getattr(result, 'content', None)
        if not content or not isinstance(content, (list, tuple)):
            logger.warning("No 18comic search content")
            return None
        entries = [c for c in content if isinstance(c, (list, tuple)) and len(c) >= 2]
        # Filter out Korean comics (韩漫) - too many pages
        filtered = []
        for c in entries:
            album_data = c[1] if isinstance(c[1], dict) else {}
            cat = album_data.get('category', {}) or {}
            cat_title = (cat.get('title') or '').strip()
            if cat_title == '韓漫':
                continue
            filtered.append(c)
        if filtered:
            entries = filtered
        if not entries:
            return None

        import random as _random
        _random.shuffle(entries)
        for entry in entries:
            aid = str(entry[0])
            if not (aid and aid.isdigit()):
                continue
            try:
                album = client.get_album_detail(int(aid))
                meta = scrape_comic_meta(f"https://18comic.vip/album/{int(aid)}/")
                if meta.get('error'):
                    continue
                if _has_guro_tags(meta.get('tags', [])):
                    continue
                return {
                    'url': f"https://18comic.vip/album/{int(aid)}/",
                    'title': meta.get('title', ''),
                    'tags': meta.get('tags', []),
                    'source': 'comic',
                    'total_pages': meta.get('total_pages', 0),
                }
            except Exception:
                continue
    except Exception as e:
        logger.warning(f"Failed to fetch 18comic popular: {e}")
    return None


# ── Ranking ──────────────────────────────────────────────

def fetch_eh_ranking() -> list[dict]:
    """Fetch top 5 from EH popular page. Returns [{url, title, tags, total_pages}, ...]."""
    import cloudscraper
    from bs4 import BeautifulSoup
    from urllib.parse import urljoin

    scraper = cloudscraper.create_scraper()
    try:
        resp = scraper.get("https://e-hentai.org/popular", timeout=30)
        resp.raise_for_status()
    except Exception as e:
        logger.warning(f"Failed to fetch EH ranking: {e}")
        return []

    soup = BeautifulSoup(resp.text, 'lxml')
    results = []
    seen = set()
    for a_tag in soup.find_all('a', href=True):
        href = urljoin("https://e-hentai.org", a_tag['href'])
        if is_eh_link(href) and href not in seen:
            seen.add(href)
            meta = scrape_eh_meta(href)
            if meta.get('error') or _has_korean_tags(meta.get('tags', [])) or _has_ai_tags(meta.get('tags', [])):
                continue
            pages = meta.get('total_pages', 0)
            if MAX_RECOMMEND_PAGES > 0 and pages > MAX_RECOMMEND_PAGES:
                continue
            results.append({
                'url': href,
                'title': meta.get('title', ''),
                'tags': meta.get('tags', []),
                'total_pages': meta.get('total_pages', 0),
            })
            if len(results) >= 5:
                break
    return results


def fetch_comic_ranking() -> list[dict]:
    """Fetch top albums from 18comic ranking. Returns [{url, title, tags, total_pages}, ...]."""
    from scrapers.comic18 import _get_client
    try:
        client = _get_client()
        from jmcomic import JmMagicConstants
        # 网页端"每日排行"= 点赞最多(tf_t)，以它为主排序保持与网页一致
        # 保持日维度，不能降级到周/月；点赞榜空时兜底浏览量(day_ranking/mv_t)
        page = client.categories_filter(1, JmMagicConstants.TIME_TODAY, JmMagicConstants.CATEGORY_ALL, 'tf')
        if not page or getattr(page, 'page_count', 0) == 0:
            logger.info('tf_t empty, fallback to day_ranking (mv_t)')
            page = client.day_ranking(1)
        content = getattr(page, 'content', [])
        results = []
        seen = set()
        # 过滤掉韩漫（id=5）
        SKIP_CATEGORIES = {'5', '7'}  # 韩漫、English Manga
        for entry in content[:20]:
            cat = entry[1] if isinstance(entry, (list, tuple)) and len(entry) >= 2 else {}
            cat_id = str(cat.get('category', {}).get('id', ''))
            if cat_id in SKIP_CATEGORIES:
                continue
            aid = str(entry[0]) if isinstance(entry, (list, tuple)) and len(entry) >= 2 else ''
            if not (aid and aid.isdigit()) or aid in seen:
                continue
            seen.add(aid)
            # 过滤韩漫
            if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                entry_meta = entry[1] if isinstance(entry[1], dict) else {}
                entry_cat = entry_meta.get('category', {})
                if isinstance(entry_cat, dict) and entry_cat.get('title', '') in ('韓漫', '韩漫'):
                    continue
            url = f"https://18comic.vip/album/{int(aid)}/"
            meta = scrape_comic_meta(url)
            if meta.get('error') or _has_guro_tags(meta.get('tags', [])) or _has_korean_tags(meta.get('tags', [])) or _has_ai_tags(meta.get('tags', [])):
                continue
            pages = meta.get('total_pages', 0)
            if MAX_RECOMMEND_PAGES > 0 and pages > MAX_RECOMMEND_PAGES:
                continue
            results.append({
                'url': url,
                'title': meta.get('title', ''),
                'tags': meta.get('tags', []),
                'total_pages': meta.get('total_pages', 0),
            })
            if len(results) >= 5:
                break
        return results
    except Exception as e:
        logger.warning(f"Failed to fetch 18comic ranking: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        return []


def _load_ranking_cache():
    try:
        data = json.loads(RANKING_CACHE_FILE.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _ranking_cache_is_fresh(cache_data, source=None):
    try:
        if source:
            updated = (cache_data.get('source_updated_at') or {}).get(source)
        else:
            updated = cache_data.get('updated_at')
        cache_updated = datetime.fromisoformat(updated or '')
        return cache_updated.astimezone(TZ_UTC8).date() == datetime.now(TZ_UTC8).date()
    except (AttributeError, TypeError, ValueError):
        return False


def _save_ranking_cache(data):
    RANKING_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = RANKING_CACHE_FILE.with_name(f".{RANKING_CACHE_FILE.name}.{os.getpid()}.{id(data)}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, RANKING_CACHE_FILE)


async def _prewarm_ranking_source(source: str):
    loop = asyncio.get_event_loop()
    is_eh = source == 'eh'
    fetcher = fetch_eh_ranking if is_eh else fetch_comic_ranking
    items = await loop.run_in_executor(None, fetcher)
    if not items:
        return []

    async def generate(item):
        comic_enabled = os.getenv("DAILY_RANKING_COMIC_TELEGRAPH", "0").strip().lower() in ("1", "true", "yes", "on")
        if not is_eh and not comic_enabled:
            return None
        try:
            return await loop.run_in_executor(None, _gen_tg_telegraph, item, is_eh, 0)
        except Exception:
            return None

    # One gallery at a time: each publisher already has internal workers.
    enriched = []
    for item in items[:5]:
        url = await generate(item)
        enriched.append({**item, 'tg_url': url if isinstance(url, str) and url.startswith('http') else None})
    return enriched


async def prewarm_rankings(context: ContextTypes.DEFAULT_TYPE):
    data = _load_ranking_cache()
    source_updated = data.setdefault('source_updated_at', {})
    for source in ('eh', 'comic'):
        enriched = await _prewarm_ranking_source(source)
        if enriched:
            data[source] = enriched
            source_updated[source] = datetime.now(TZ_UTC8).isoformat()
    data['updated_at'] = datetime.now(TZ_UTC8).isoformat()
    _save_ranking_cache(data)


async def handle_ranking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ranking entry point: show source picker. Owner only."""
    menu = _Menu(update)
    user_id = menu.user.id if menu.user else 0
    owner_set = _parse_user_ids(OWNER_USERS)

    if user_id not in owner_set:
        await menu.answer("仅限管理员使用", show_alert=True)
        return

    await menu.answer()

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔞 EH 排行", callback_data="ranking_eh"),
         InlineKeyboardButton("📖 18comic 排行", callback_data="ranking_comic")],
        [InlineKeyboardButton("🔙 返回", callback_data="back_to_start")],
    ])
    await menu.say(
        "🏆 <b>当日排行</b>\n\n选择一个来源查看热门排行：",
        parse_mode='HTML', reply_markup=keyboard
    )


async def handle_ranking_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fetch and show ranking list with instant Telegraph preview links. Owner only."""
    query = update.callback_query
    await query.answer()
    source = query.data  # "ranking_eh" or "ranking_comic"

    user_id = query.from_user.id if query.from_user else 0
    owner_set = _parse_user_ids(OWNER_USERS)
    if user_id not in owner_set:
        await query.edit_message_text("❌ 仅限管理员使用")
        return

    await query.edit_message_text(f"⏳ 正在获取 {'EH' if source == 'ranking_eh' else '18comic'} 排行...")
    loop = asyncio.get_event_loop()

    cache_key = 'eh' if source == 'ranking_eh' else 'comic'
    cache_data = _load_ranking_cache()
    cache_fresh = _ranking_cache_is_fresh(cache_data, cache_key)
    cached_items = (cache_data.get(cache_key) or []) if cache_fresh else []
    if cached_items:
        items = cached_items
        source_name = "E-Hentai" if cache_key == 'eh' else "18comic"
        emoji = "🔞" if cache_key == 'eh' else "📖"
        fetch_processor = cache_key
    elif source == 'ranking_eh':
        items = await loop.run_in_executor(None, fetch_eh_ranking)
        source_name = "E-Hentai"
        emoji = "🔞"
        fetch_processor = 'eh'
    else:
        items = await loop.run_in_executor(None, fetch_comic_ranking)
        source_name = "18comic"
        emoji = "📖"
        fetch_processor = 'comic'

    if not items:
        await query.edit_message_text(f"❌ 获取排行失败")
        return

    # Cache hits already contain pre-generated Telegraph URLs.
    enriched = []
    if cached_items:
        tg_results = [item.get('tg_url') for item in items[:5]]
    else:
        progress_msg = await query.edit_message_text(f"⏳ 正在生成即时阅览... (0/{min(5, len(items))})")
        loop = asyncio.get_event_loop()
        tasks = []
        for idx, item in enumerate(items[:5]):
            is_eh = fetch_processor != 'comic'
            tasks.append(loop.run_in_executor(None, _gen_tg_telegraph, item, is_eh, idx * 2.0))
        tg_results = await asyncio.gather(*tasks, return_exceptions=True)

    for idx, item in enumerate(items[:5]):
        title = item['title']
        pages = item.get('total_pages', 0)
        tg_url = tg_results[idx] if isinstance(tg_results[idx], str) and str(tg_results[idx]).startswith('http') else None
        enriched.append({
            'item': item,
            'title': title,
            'pages': pages,
            'tg_url': tg_url,
        })

    source_name = "E-Hentai" if fetch_processor != 'comic' else "18comic"
    emoji = "🔞" if fetch_processor != 'comic' else "📖"

    # Build list with Telegraph links - 所有条目都必须有链接
    lines = [
        f"{emoji} <b>{source_name} 当日排行</b>",
        f"📅 {datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')} 更新",
        "",
    ]
    for i, entry in enumerate(enriched):
        link = entry['tg_url'] or entry['item']['url']
        # Handle multi-page Telegraph (e.g. "url1\nurl2\nurl3")
        if link and '\n' in link:
            urls = link.split('\n')
            link = urls[0].strip()
            extra_pages = len(urls) - 1
            suffix = f" +{extra_pages}篇" if extra_pages > 0 else ""
        else:
            suffix = ""
        lines.append(f'<b>{i+1}.</b> <a href="{link}">📖 {entry["title"]}</a>  📄{entry["pages"]}p{suffix}')
    lines.append("")

    await query.edit_message_text(
        '\n'.join(lines),
        parse_mode='HTML',
        disable_web_page_preview=True,
    )

async def handle_ranking_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User picked an item from the ranking list → full process."""
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id if update.effective_chat else 0
    user_id = query.from_user.id if query.from_user else 0

    if not chat_allowed(update):
        await query.edit_message_text("⚠️ 此 bot 未启用群组模式" if _chat_is_group(update.effective_chat) else "⚠️ 你没有权限使用此 bot")
        return
    lock = _lock_key(chat_id, user_id)
    if lock in _processing:
        await query.answer("⏳ 正在处理中，请稍等", show_alert=True)
        return

    # Get the picked item
    idx = int(query.data.replace('rank_pick_', ''))
    items = context.user_data.get('ranking_list', [])
    source_name = context.user_data.get('ranking_source', '')

    if idx < 0 or idx >= len(items):
        await query.edit_message_text("❌ 无效选择")
        return

    item = items[idx]
    url = item['url']

    # Consume quota
    ok, remaining = consume_daily_quota(user_id, 1)
    if not ok:
        await query.edit_message_text(
            f"🚫 今日次数已用完。普通用户每天最多 {DAILY_LIMIT} 次，按 UTC+8 零点重置。"
        )
        return

    emoji = "🔞" if is_eh_link(url) else "📖"
    loop = asyncio.get_event_loop()
    _processing.add(lock)
    try:
        await query.edit_message_text(f"🔄 正在处理：{item['title'][:60]}...")

        if is_comic_link(url):
            result = await loop.run_in_executor(
                None, lambda: scrape_comic(url, max_workers=MAX_WORKERS, max_pages=MAX_PAGES)
            )
        else:
            result = await loop.run_in_executor(
                None, lambda: scrape_eh(url, max_workers=MAX_WORKERS, max_pages=MAX_PAGES)
            )

        if result.get('error'):
            await query.edit_message_text(f"❌ {result['error']}")
            return

        title = result['title']
        image_urls = result['image_urls']
        image_details = result.get('image_details', [])
        pages = result['pages']

        await query.edit_message_text(
            f"🏆 <b>{source_name} 排行</b>\n{emoji} {title}\n📄 {pages} 页\n📝 正在生成 Telegraph...",
            parse_mode='HTML'
        )

        if is_comic_link(url) and image_details:
            pub_result = await loop.run_in_executor(
                None, lambda: publish_jm_gallery(title, image_details, max_workers=MAX_WORKERS)
            )
        else:
            pub_result = await loop.run_in_executor(
                None, lambda: publish_eh_gallery(title, image_urls, max_workers=MAX_WORKERS)
            )

        if pub_result.get('error'):
            await query.edit_message_text(f"❌ {pub_result['error']}")
            return

        page_url = pub_result.get('url')
        published = pub_result.get('uploaded', pub_result.get('downloaded', 0))
        if not page_url:
            await query.edit_message_text(
                f"📖 <b>{title}</b>\n📄 {pages} 页\n<a href=\"{url}\">🔗 打开原文链接</a>",
                parse_mode='HTML', disable_web_page_preview=True
            )
            return

        link = page_url
        msg = (
            f"🏆 <b>{source_name} 排行</b>\n"
            f"{emoji} <a href=\"{link}\">{title}</a>\n"
            f"📄 {published}/{pages} 页"
        )
        btns = [
            [InlineKeyboardButton("🏆 返回排行", callback_data="ranking")],
            [InlineKeyboardButton("🎲 再推荐一个", callback_data="recommend")],
        ]

        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(btns), parse_mode='HTML')

    except Exception as e:
        logger.exception(f"Ranking pick failed: {e}")
        await query.edit_message_text(f"❌ 处理失败：{str(e)[:200]}")
    finally:
        _processing.discard(lock)


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else 0
    if not is_owner(user_id):
        await update.message.reply_text("⛔ 你没有权限查看此信息")
        return

    usage = _load_usage()
    if not usage:
        await update.message.reply_text("📊 暂无使用记录")
        return

    lines = ["📊 **使用统计**\n"]
    for date_str in sorted(usage.keys(), reverse=True):
        day_data = usage[date_str]
        if not isinstance(day_data, dict):
            continue
        lines.append(f"**{date_str}**")
        for uid_str, count in sorted(day_data.items(), key=lambda x: -int(x[1])):
            uid = int(uid_str)
            # Try to get user info
            name = uid_str
            try:
                chat = await context.bot.get_chat(chat_id=uid)
                name = f"@{chat.username}" if chat.username else chat.first_name or uid_str
            except Exception:
                pass
            lines.append(f"  • {name}: {count} 次")
        lines.append("")

    msg = "\n".join(lines)
    # Telegram has 4096 char limit per message
    if len(msg) > 4000:
        msg = msg[:4000] + "\n\n...（超出长度截断）"

    await update.message.reply_text(msg)


async def handle_back_to_start(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    """Return to the start message from a callback."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id if query.from_user else 0
    btns = [
        [InlineKeyboardButton("🎮 黄油下载", url="https://t.me/huangyoustore")],
        [InlineKeyboardButton("🎲 随机推荐", callback_data="recommend")],
        [InlineKeyboardButton("🔍 标签搜索", callback_data="search_start")],
    ]
    if is_owner(user_id):
        btns.append([InlineKeyboardButton("🏆 当日排行", callback_data="ranking")])
    await query.edit_message_text(
        "🎴 <b>hentaiviewer</b>\n\n"
        "EH / 18comic 链接转在线阅读器。\n\n"
        "<b>使用方法：</b>\n"
        "直接发送 EH / 18comic 链接给我即可。\n\n"
        "更多精彩尽在黄油频道 🧈",
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(btns)
    )


async def handle_search_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Search button clicked → ask user to enter tags."""
    menu = _Menu(update)
    await menu.answer()
    user_id = menu.user.id if menu.user else 0
    if not chat_allowed(update):
        await menu.say("⚠️ 此 bot 未启用群组模式" if _chat_is_group(update.effective_chat) else "⚠️ 你没有权限使用此 bot")
        return
    # Set flag so next text message is treated as search query
    context.user_data['awaiting_search_tags'] = True
    await menu.say(
        "🔍 <b>标签搜索</b>\n\n"
        "请输入要搜索的标签，多个标签用空格隔开：\n"
        "例如：<code>female:sole male</code>\n\n"
        "发送 /cancel 取消搜索",
        parse_mode='HTML'
    )


async def handle_search_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text input when awaiting search tags."""
    if not update.message or not update.message.text:
        return
    # Only handle if awaiting search
    if not context.user_data.get('awaiting_search_tags'):
        return
    context.user_data['awaiting_search_tags'] = False

    user_id = update.effective_user.id if update.effective_user else 0
    if not chat_allowed(update):
        msg = "⚠️ 此 bot 未启用群组模式" if _chat_is_group(update.effective_chat) else "⚠️ 你没有权限使用此 bot"
        await update.message.reply_text(msg)
        return

    chat_id = update.effective_chat.id if update.effective_chat else 0
    lock = _lock_key(chat_id, user_id)
    if lock in _processing:
        await update.message.reply_text("⏳ 正在处理中，请等待完成后再搜索")
        return

    tags = update.message.text.strip()
    if not tags:
        await update.message.reply_text("❌ 请输入标签关键词")
        return

    # Consume quota
    ok, remaining = consume_daily_quota(user_id, 1)
    if not ok:
        await update.message.reply_text(
            f"🚫 今日次数已用完。普通用户每天最多 {DAILY_LIMIT} 次，按 UTC+8 零点重置。"
        )
        return

    # Show source picker
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔞 EH 搜索", callback_data=f"search_eh:{tags}"),
         InlineKeyboardButton("📖 18comic 搜索", callback_data=f"search_comic:{tags}")],
        [InlineKeyboardButton("🔙 返回", callback_data="back_to_start")],
    ])
    await update.message.reply_text(
        f"🔍 <b>标签搜索：{tags}</b>\n\n请选择搜索来源：",
        parse_mode='HTML', reply_markup=keyboard
    )


async def handle_search_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Execute search callback. Shows results as selection list (no Telegraph yet)."""
    query = update.callback_query
    await query.answer()

    chat_id = update.effective_chat.id if update.effective_chat else 0
    user_id = query.from_user.id if query.from_user else 0
    if not chat_allowed(update):
        await query.edit_message_text("⚠️ 此 bot 未启用群组模式" if _chat_is_group(update.effective_chat) else "⚠️ 你没有权限使用此 bot")
        return
    lock = _lock_key(chat_id, user_id)
    if lock in _processing:
        await query.answer("⏳ 正在搜索中，请稍等", show_alert=True)
        return

    # Consume quota per search
    ok, remaining = consume_daily_quota(user_id, 1)
    if not ok:
        await query.edit_message_text(
            f"🚫 今日次数已用完。普通用户每天最多 {DAILY_LIMIT} 次，按 UTC+8 零点重置。"
        )
        return

    _processing.add(lock)

    try:
        data = query.data  # "search_eh:tags" or "search_comic:tags"
        source, tags = data.split(':', 1)
        is_eh = source == 'search_eh'
        source_name = "E-Hentai" if is_eh else "18comic"
        emoji = "🔞" if is_eh else "📖"

        await query.edit_message_text(f"⏳ 正在搜索 {source_name}...")

        loop = asyncio.get_event_loop()
        if is_eh:
            items = await loop.run_in_executor(None, search_eh, tags, 5)
        else:
            items = await loop.run_in_executor(None, search_comic, tags, 5)

        if not items:
            await query.edit_message_text(f"❌ 在 {source_name} 未找到「{tags}」相关结果")
            return

        # Store results for the selection handler
        context.user_data['search_results'] = items
        context.user_data['search_source_name'] = source_name
        context.user_data['search_emoji'] = emoji
        context.user_data['search_tags'] = tags
        context.user_data['search_is_eh'] = is_eh

        # Show results as numbered list with selection buttons
        lines = [
            f"{emoji} <b>{source_name} 搜索结果：{tags}</b>",
            f"📅 {datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')}",
            "",
        ]
        for i, item in enumerate(items):
            title = item.get('title', '?')
            pages = item.get('total_pages', 0)
            # compact tag display
            tags_list = item.get('tags', [])
            tag_str = ''
            if tags_list:
                shown = [t.split(':')[-1] for t in tags_list[:3]]
                tag_joined = ', '.join(shown)
                tag_str = f'  🏷️{tag_joined}'
                if len(tags_list) > 3:
                    tag_str += f' +{len(tags_list)-3}'
            lines.append(f"<b>{i+1}.</b> 📖 {title}  📄{pages}p{tag_str}")
        lines.append("")
        lines.append("💬 点击下方数字选择")

        # Build number buttons: max 5 per row
        num_btns = []
        row = []
        for i in range(len(items)):
            row.append(InlineKeyboardButton(str(i+1), callback_data=f"search_pick_{i}"))
            if len(row) >= 5 or i == len(items) - 1:
                num_btns.append(row)
                row = []

        # Source switch + re-search buttons
        nav_btns = [
            InlineKeyboardButton("🔞 EH" if not is_eh else "📖 18comic", callback_data=f"search_{'eh' if not is_eh else 'comic'}:{tags}"),
            InlineKeyboardButton("🔍 重新搜索", callback_data="search_start"),
        ]
        num_btns.append(nav_btns)

        await query.edit_message_text(
            '\n'.join(lines),
            parse_mode='HTML',
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup(num_btns),
        )
    except Exception as e:
        logger.exception(f"Search failed: {e}")
        await query.edit_message_text(f"❌ 搜索失败：{str(e)[:200]}")
    finally:
        _processing.discard(lock)


async def handle_search_results_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Return to previous search results list from a picked item."""
    query = update.callback_query
    await query.answer()

    items = context.user_data.get('search_results', [])
    source_name = context.user_data.get('search_source_name', '')
    emoji = context.user_data.get('search_emoji', '')
    tags = context.user_data.get('search_tags', '')
    is_eh = context.user_data.get('search_is_eh', True)

    if not items:
        # No stored results, go to search start
        await handle_search_start(update, context)
        return

    lines = [
        f"{emoji} <b>{source_name} 搜索结果：{tags}</b>",
        f"📅 {datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')}",
        "",
    ]
    for i, item in enumerate(items):
        title = item.get('title', '?')
        pages = item.get('total_pages', 0)
        tags_list = item.get('tags', [])
        tag_str = ''
        if tags_list:
            shown = [t.split(':')[-1] for t in tags_list[:3]]
            tag_joined = ', '.join(shown)
            tag_str = f'  🏷️{tag_joined}'
            if len(tags_list) > 3:
                tag_str += f' +{len(tags_list)-3}'
        lines.append(f"<b>{i+1}.</b> 📖 {title}  📄{pages}p{tag_str}")
    lines.append("")
    lines.append("💬 点击下方数字选择")

    num_btns = []
    row = []
    for i in range(len(items)):
        row.append(InlineKeyboardButton(str(i+1), callback_data=f"search_pick_{i}"))
        if len(row) >= 5 or i == len(items) - 1:
            num_btns.append(row)
            row = []

    nav_btns = [
        InlineKeyboardButton("🔞 EH" if not is_eh else "📖 18comic", callback_data=f"search_{'eh' if not is_eh else 'comic'}:{tags}"),
        InlineKeyboardButton("🔍 重新搜索", callback_data="search_start"),
    ]
    num_btns.append(nav_btns)

    await query.edit_message_text(
        '\n'.join(lines),
        parse_mode='HTML',
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup(num_btns),
    )


async def handle_search_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User selected an item from search results → full scrape + publish."""
    query = update.callback_query
    await query.answer()

    chat_id = update.effective_chat.id if update.effective_chat else 0
    user_id = query.from_user.id if query.from_user else 0

    if not chat_allowed(update):
        await query.edit_message_text("⚠️ 此 bot 未启用群组模式" if _chat_is_group(update.effective_chat) else "⚠️ 你没有权限使用此 bot")
        return
    lock = _lock_key(chat_id, user_id)
    if lock in _processing:
        await query.answer("⏳ 正在处理中，请稍等", show_alert=True)
        return

    # Get the picked item from stored results
    idx = int(query.data.replace('search_pick_', ''))
    items = context.user_data.get('search_results', [])
    source_name = context.user_data.get('search_source_name', '')
    emoji = context.user_data.get('search_emoji', '')

    if idx < 0 or idx >= len(items):
        await query.edit_message_text("❌ 无效选择")
        return

    item = items[idx]
    url = item['url']
    title = item['title']

    loop = asyncio.get_event_loop()
    _processing.add(lock)
    try:
        await query.edit_message_text(
            f"🔍 <b>搜索 - {source_name}</b>\n{emoji} {title}\n📝 正在抓取并生成 Telegraph...",
            parse_mode='HTML'
        )

        if is_comic_link(url):
            result = await loop.run_in_executor(
                None, lambda: scrape_comic(url, max_workers=MAX_WORKERS, max_pages=MAX_PAGES)
            )
        else:
            result = await loop.run_in_executor(
                None, lambda: scrape_eh(url, max_workers=MAX_WORKERS, max_pages=MAX_PAGES)
            )

        if result.get('error'):
            await query.edit_message_text(f"❌ {result['error']}")
            return

        image_urls = result['image_urls']
        image_details = result.get('image_details', [])
        pages = result['pages']

        if is_comic_link(url) and image_details:
            pub_result = await loop.run_in_executor(
                None, lambda: publish_jm_gallery(title, image_details, max_workers=MAX_WORKERS)
            )
        else:
            pub_result = await loop.run_in_executor(
                None, lambda: publish_eh_gallery(title, image_urls, max_workers=MAX_WORKERS)
            )

        if not pub_result or pub_result.get('error'):
            # Fallback: send original URL
            await query.edit_message_text(
                f"📖 <b>{title}</b>\n📄 {pages} 页\n"
                f"<a href=\"{url}\">🔗 打开原文链接</a>",
                parse_mode='HTML', disable_web_page_preview=True
            )
            return

        page_url = pub_result.get('url')
        published = pub_result.get('uploaded', pub_result.get('downloaded', 0))

        if not page_url:
            await query.edit_message_text(
                f"📖 <b>{title}</b>\n📄 {pages} 页\n"
                f"<a href=\"{url}\">🔗 打开原文链接</a>",
                parse_mode='HTML', disable_web_page_preview=True
            )
            return

        link = page_url
        msg = (
            f"📖 <a href=\"{link}\">{title}</a>\n"
            f"📄 {published}/{pages} 页"
        )
        btns = [[InlineKeyboardButton("🔍 返回搜索列表", callback_data="search_results_back")]]

        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(btns), parse_mode='HTML')

    except Exception as e:
        logger.exception(f"Search pick failed: {e}")
        await query.edit_message_text(f"❌ 处理失败：{str(e)[:200]}")
    finally:
        _processing.discard(lock)


async def error_handler(update, context):
    logger.error(f"Update {update} caused error {context.error}")


def _gen_tg_telegraph(item, is_eh, delay=0):
    """为单条排行条目生成 Telegraph（同步函数，由 run_in_executor 调用）"""
    if delay > 0:
        import time
        time.sleep(delay)
    try:
        url = item.get('url', '')
        title = (item.get('title') or 'Otomi')[:80]
        # 使用完整页数（排行已过滤 >200 页的条目）
        total_pages = item.get('total_pages', 0)
        if total_pages <= 0:
            return None
        effective_max = total_pages
        if is_eh:
            result = scrape_eh(url, max_workers=3, max_pages=effective_max)
            if result and result.get('image_urls'):
                pub = publish_eh_gallery(title, result['image_urls'][:effective_max])
                if pub and pub.get('url'):
                    return pub['url']
        else:
            result = scrape_comic(url, max_workers=3, max_pages=effective_max)
            if result and result.get('image_details'):
                # 重试一次以应对 PAGE_SAVE_FAILED 瞬时错误
                for attempt in range(2):
                    pub = publish_jm_gallery(title, result['image_details'], max_workers=2)
                    if pub and pub.get('url'):
                        return pub['url']
                    if attempt == 0:
                        import time
                        time.sleep(2)
    except Exception as e:
        logger.warning(f"Telegraph 生成失败: {title[:30]} → {e}")
    return None


def _preview_ranking_text(items, source_name, emoji, tg_results):
    """生成排行文本，每条都带链接（优先 Telegraph，失败用原文）"""
    lines = [
        f"{emoji} <b>{source_name} 当日排行</b>",
        f"📅 {datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d')}",
        "",
    ]
    for i, (item, tg_url) in enumerate(zip(items, tg_results)):
        title = (item.get('title') or '?')
        pages = item.get('total_pages', 0)
        link = tg_url or item.get('url', '')
        # Handle multi-page Telegraph (e.g. "url1\nurl2\nurl3")
        if link and '\n' in link:
            urls = link.split('\n')
            # Main link = first page, show page count
            link = urls[0].strip()
            extra_pages = len(urls) - 1
            suffix = f" +{extra_pages}篇" if extra_pages > 0 else ""
        else:
            suffix = ""
        if tg_url:
            lines.append(f"<b>{i+1}.</b> <a href=\"{link}\">📖 {title}</a>  📄{pages}p{suffix}")
        else:
            lines.append(f"<b>{i+1}.</b> ⚠️ {title}  📄{pages}p（生成失败）")
    lines.append("")
    lines.append(f"📱 {datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')} 更新")
    return "\n".join(lines)


# ─── 每日排行推送（到 huangyoustore 频道） ─────────────────

STORE_CHANNEL = os.getenv("STORE_CHANNEL_CHAT_ID", "@huangyoustore")
# 发送排行用的 bot token（默认用 EHBOT，但频道需要另用一个有管理员权限的 bot）
STORE_BOT_TOKEN = os.getenv("STORE_BOT_TOKEN", "") or BOT_TOKEN
DAILY_RANKING_HOUR = int(os.getenv("DAILY_RANKING_HOUR", "12"))  # UTC, 12 = UTC+8 20:00
DAILY_RANKING_TOP_N = int(os.getenv("DAILY_RANKING_TOP_N", "5"))


async def _ranking_send_safe(sender, chat_id, text, parse_mode='HTML'):
    """安全发送，用独立 Bot 实例（不是 context.bot）"""
    import traceback
    try:
        return await sender.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
    except Exception as e:
        logger.error(f"排行推送发送失败: {e}\n{traceback.format_exc()}")
        return None


def _build_ranking_text(title: str, items: list[dict], emoji: str, source: str) -> str:
    if not items:
        return f"{emoji} <b>{title}</b>\n\n暂无排行数据"

    lines = [
        f"{emoji} <b>{title}</b>",
        f"📅 {datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d')}",
        "",
    ]
    for i, item in enumerate(items[:DAILY_RANKING_TOP_N]):
        title_str = (item.get('title') or '?')
        pages = item.get('total_pages', 0)
        lines.append(f"<b>{i+1}.</b> {title_str}  📄{pages}p")
    lines.append("")
    lines.append(f"💬 回复序号查看详情 / 在 @huangyoustore 输入序号自动处理")
    return "\n".join(lines)


async def _run_ranking_generators(items, generator, concurrency: int = 1):
    """Generate ranking pages with bounded gallery-level concurrency.

    Results preserve input order and exceptions stay isolated to their item.
    """
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def run_one(item):
        async with semaphore:
            try:
                return await generator(item)
            except Exception as exc:
                return exc

    return await asyncio.gather(*(run_one(item) for item in items))


async def send_daily_ranking_to_store(context: ContextTypes.DEFAULT_TYPE):
    """每日定时：获取排行→生成 Telegraph→推送到商店频道"""
    logger.info("[每日排行] 开始获取...")
    loop = asyncio.get_event_loop()
    store_chat_id = os.getenv("STORE_CHANNEL_CHAT_ID", "@huangyoustore")

    async def _gen_tg(item, is_eh):
        """为单个排行条目生成 Telegraph，返回 tg_url 或 None"""
        url = item.get('url', '')
        title = (item.get('title') or '?')
        try:
            if is_eh:
                result = await loop.run_in_executor(None, lambda: scrape_eh(url, max_workers=4))
                if result and result.get('image_urls'):
                    pub = await loop.run_in_executor(None, lambda: publish_eh_gallery(title, result['image_urls']))
                    if pub and pub.get('url'):
                        return pub['url']
            else:
                max_pages = int(item.get('total_pages') or 0)
                result = await loop.run_in_executor(None, lambda: scrape_comic(url, max_workers=4, max_pages=max_pages))
                if result and result.get('image_details'):
                    pub = await loop.run_in_executor(None, lambda: publish_jm_gallery(title, result['image_details']))
                    if pub and pub.get('url'):
                        return pub['url']
        except Exception as e:
            logger.error(f"[排行] Telegraph 失败 {title[:30]}: {e}")
        return None

    async def _fetch_and_send(source_name, emoji, fetch_fn, is_eh, generate_telegraph=True):
        """获取排行 + 并行生成 Telegraph + 发送到频道。

        18comic 的 Telegraph 需要先把图片上传到 Catbox/Litterbox；图床故障时会把定时任务卡很久。
        因此支持快速降级为原站链接版，保证每日排行准时发出。
        """
        items = await loop.run_in_executor(None, fetch_fn)
        if not items:
            text = f"{emoji} <b>{source_name} 当日排行</b>\n\n暂无排行数据\n\n📱 更多精彩 @hentaiviewer_bot"
            await _ranking_send_safe(context.bot, store_chat_id, text, 'HTML')
            return

        if generate_telegraph:
            # JM publishing is upload-heavy. Bound gallery-level concurrency so
            # five galleries do not multiply into ~20 simultaneous Catbox posts.
            concurrency = 2 if is_eh else 1
            tg_results = await _run_ranking_generators(
                items[:DAILY_RANKING_TOP_N],
                lambda item: _gen_tg(item, is_eh),
                concurrency=concurrency,
            )
        else:
            logger.warning(f"[每日排行] {source_name} Telegraph 已禁用，使用原站链接快速发送")
            tg_results = [None] * min(len(items), DAILY_RANKING_TOP_N)

        lines = [
            f"{emoji} <b>{source_name} 当日排行</b>",
            f"📅 {datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d')}",
            "",
        ]
        for i, (item, tg_result) in enumerate(zip(items[:DAILY_RANKING_TOP_N], tg_results)):
            title = (item.get('title') or '?')
            pages = item.get('total_pages', 0)
            tg_url = None
            suffix = ""
            if isinstance(tg_result, str) and tg_result.startswith('http'):
                urls = tg_result.split('\n')
                tg_url = urls[0].strip()
                extra = len(urls) - 1
                suffix = f" +{extra}篇" if extra > 0 else ""
            if tg_url:
                lines.append(f"<b>{i+1}.</b> <a href=\"{tg_url}\">📖 {title}</a>  📄{pages}p{suffix}")
            else:
                lines.append(f"<b>{i+1}.</b> ⚠️ {title}  📄{pages}p（生成失败）")

        lines.append("")
        lines.append(f"📱 更多精彩 @hentaiviewer_bot")
        text = "\n".join(lines)
        await _ranking_send_safe(context.bot, store_chat_id, text, 'HTML')

    try:
        await _fetch_and_send("E-Hentai", "🔞", fetch_eh_ranking, is_eh=True)
        logger.info("[每日排行] EH 排行已发送")
        await asyncio.sleep(3)
        comic_tg_enabled = os.getenv("DAILY_RANKING_COMIC_TELEGRAPH", "0").strip().lower() in ("1", "true", "yes", "on")
        await _fetch_and_send("18comic", "📖", fetch_comic_ranking, is_eh=False, generate_telegraph=comic_tg_enabled)
        logger.info("[每日排行] 18comic 排行已发送")
    except Exception as e:
        logger.exception(f"[每日排行] 整体失败: {e}")
        logger.error(f"[每日排行] 整体异常: {e}")


def main():
    token = BOT_TOKEN
    if not token:
        logger.error("EHBOT_TELEGRAM_TOKEN not set")
        sys.exit(1)

    app = Application.builder().token(token).build()

    # APScheduler 默认 misfire_grace_time=1s，延迟几秒就跳过；设为 5 分钟容错
    if app.job_queue:
        app.job_queue.scheduler.configure(misfire_grace_time=300)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("daily", daily_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("health", health_command))
    app.add_handler(CommandHandler("subscribe", subscribe_command))
    app.add_handler(CommandHandler("unsubscribe", unsubscribe_command))
    app.add_handler(CommandHandler("subscriptions", subscriptions_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CallbackQueryHandler(handle_recommend, pattern="^recommend$"))
    app.add_handler(CallbackQueryHandler(handle_ranking, pattern="^ranking$"))
    app.add_handler(CallbackQueryHandler(handle_ranking_list, pattern="^ranking_(eh|comic)$"))
    app.add_handler(CallbackQueryHandler(handle_ranking_pick, pattern="^rank_pick_"))
    app.add_handler(CallbackQueryHandler(handle_search_start, pattern="^search_start$"))
    app.add_handler(CallbackQueryHandler(handle_search_execute, pattern="^search_(eh|comic):"))
    app.add_handler(CallbackQueryHandler(handle_search_results_back, pattern="^search_results_back$"))
    app.add_handler(CallbackQueryHandler(handle_search_pick, pattern="^search_pick_"))
    app.add_handler(CallbackQueryHandler(handle_back_to_start, pattern="^back_to_start$"))
    app.add_handler(CallbackQueryHandler(handle_ris_read, pattern="^ris_read:"))
    # Fixed reply-keyboard buttons (custom keyboard) — must run before the
    # generic search/message handlers.
    menu_pattern = '^(' + '|'.join(re.escape(k) for k in MENU_ROUTES) + ')$'
    app.add_handler(MessageHandler(filters.Regex(menu_pattern), handle_menu_button), group=2)
    # Search text handler must come before the general message handler
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(r'https?://'), handle_search_query), group=1)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    # Reverse image search (photos); group trigger rules apply inside handler
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    image_documents = filters.Document.IMAGE
    app.add_handler(MessageHandler(image_documents, handle_photo))
    app.add_error_handler(error_handler)

    # 每日定时推送排行到 @huangyoustore
    try:
        from datetime import time as dt_time
        job_queue = app.job_queue
        if job_queue:
            job_queue.run_daily(
                prewarm_rankings,
                time=dt_time(hour=(DAILY_RANKING_HOUR - 2) % 24, minute=0, tzinfo=timezone.utc),
                name="prewarm_rankings",
            )
            job_queue.run_daily(
                notify_subscription_matches,
                time=dt_time(hour=(DAILY_RANKING_HOUR - 1) % 24, minute=0, tzinfo=timezone.utc),
                name="notify_subscription_matches",
            )
            job_queue.run_daily(
                send_daily_ranking_to_store,
                time=dt_time(hour=DAILY_RANKING_HOUR, minute=0, tzinfo=timezone.utc),
                name="daily_ranking_to_store"
            )
            logger.info(f"[每日排行] 已设定每日 UTC {DAILY_RANKING_HOUR}:00 推送排行到 {STORE_CHANNEL}")
        else:
            logger.warning("[每日排行] JobQueue 不可用（app.run_polling 未启用 job_queue）")
    except Exception as e:
        logger.warning(f"[每日排行] 初始化 JobQueue 失败: {e}")

    logger.info("EH Reader Bot started, polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

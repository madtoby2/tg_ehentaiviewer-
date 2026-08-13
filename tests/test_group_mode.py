"""Group mode tests: config parsing, chat-level access control, per-user locking.

Run: python3 -m pytest tests/test_group_mode.py -v
"""
import asyncio
import importlib
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot
from telegram import Bot, Chat, Message, MessageEntity, Update, User


def _set_env(**kw):
    for k, v in kw.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _make_update(chat_id, chat_type, user_id, text, entities=None):
    bot_obj = Bot(token="123456:test-token")
    chat = Chat(id=chat_id, type=chat_type)
    user = User(id=user_id, is_bot=False, first_name="tester")
    msg = Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=chat,
        from_user=user,
        text=text,
        entities=entities,
    )
    msg.set_bot(bot_obj)
    return Update(update_id=1, message=msg)


class GroupModeConfigTests(unittest.TestCase):
    """Environment variable parsing."""

    def setUp(self):
        self._saved = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)
        importlib.reload(bot)

    def _reload(self, **env):
        _set_env(**env)
        return importlib.reload(bot)

    def test_group_mode_default_is_off(self):
        b = self._reload()
        self.assertFalse(b.GROUP_MODE)

    def test_group_mode_true_values(self):
        for val in ("1", "true", "True", "yes", "on"):
            b = self._reload(EHBOT_GROUP_MODE=val)
            self.assertTrue(b.GROUP_MODE, val)

    def test_group_mode_false_values(self):
        for val in ("0", "false", "no", "off", ""):
            b = self._reload(EHBOT_GROUP_MODE=val)
            self.assertFalse(b.GROUP_MODE, val)

    def test_group_allowed_chats_parses_negative_ids(self):
        b = self._reload(EHBOT_GROUP_ALLOWED_CHATS="-100123456, -100789")
        self.assertEqual(b.GROUP_ALLOWED_CHATS, {-100123456, -100789})

    def test_group_allowed_chats_ignores_garbage(self):
        b = self._reload(EHBOT_GROUP_ALLOWED_CHATS="-100abc, 12, xyz, -99")
        self.assertEqual(b.GROUP_ALLOWED_CHATS, {12, -99})

    def test_group_allowed_chats_empty_when_unset(self):
        b = self._reload(EHBOT_GROUP_ALLOWED_CHATS=None)
        self.assertEqual(b.GROUP_ALLOWED_CHATS, set())


class ChatIsGroupTests(unittest.TestCase):
    def test_group_types(self):
        for t in ("group", "supergroup"):
            self.assertTrue(bot._chat_is_group(Chat(id=-100, type=t)))

    def test_non_group_types(self):
        for t in ("private", "channel"):
            self.assertFalse(bot._chat_is_group(Chat(id=1, type=t)))

    def test_none_chat(self):
        self.assertFalse(bot._chat_is_group(None))


class ChatAllowedTests(unittest.TestCase):
    """chat_allowed() access control matrix."""

    def setUp(self):
        self._saved = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)
        importlib.reload(bot)

    def _reload(self, **env):
        _set_env(**env)
        return importlib.reload(bot)

    def _update(self, chat_id, chat_type, user_id):
        return _make_update(chat_id, chat_type, user_id, "https://e-hentai.org/g/1/abc/")

    def test_private_chat_allowed_when_public_access(self):
        self._reload(EHBOT_PUBLIC_ACCESS="1")
        self.assertTrue(bot.chat_allowed(self._update(100, "private", 999)))

    def test_private_chat_denied_when_whitelist_misses(self):
        self._reload(EHBOT_PUBLIC_ACCESS="0", EHBOT_ALLOWED_USERS="111")
        self.assertFalse(bot.chat_allowed(self._update(100, "private", 999)))

    def test_private_chat_allowed_when_whitelist_hits(self):
        self._reload(EHBOT_PUBLIC_ACCESS="0", EHBOT_ALLOWED_USERS="111,999")
        self.assertTrue(bot.chat_allowed(self._update(100, "private", 999)))

    def test_group_denied_when_group_mode_off(self):
        self._reload(EHBOT_GROUP_MODE="0")
        self.assertFalse(bot.chat_allowed(self._update(-100123, "supergroup", 999)))

    def test_group_allowed_when_group_mode_on_and_no_whitelist(self):
        self._reload(EHBOT_GROUP_MODE="1", EHBOT_GROUP_ALLOWED_CHATS="")
        self.assertTrue(bot.chat_allowed(self._update(-100123, "supergroup", 999)))

    def test_group_allowed_when_whitelist_hits(self):
        self._reload(EHBOT_GROUP_MODE="1", EHBOT_GROUP_ALLOWED_CHATS="-100123,-100456")
        self.assertTrue(bot.chat_allowed(self._update(-100123, "supergroup", 999)))

    def test_group_denied_when_whitelist_misses(self):
        self._reload(EHBOT_GROUP_MODE="1", EHBOT_GROUP_ALLOWED_CHATS="-100123,-100456")
        self.assertFalse(bot.chat_allowed(self._update(-100999, "supergroup", 999)))

    def test_group_mode_on_does_not_open_private_chat_whitelist(self):
        self._reload(EHBOT_GROUP_MODE="1", EHBOT_PUBLIC_ACCESS="0", EHBOT_ALLOWED_USERS="111")
        self.assertFalse(bot.chat_allowed(self._update(100, "private", 999)))

    def test_no_effective_chat_denied(self):
        self._reload(EHBOT_GROUP_MODE="1")
        update = Update(update_id=1)  # no message/channel_post → effective_chat is None
        self.assertIsNone(update.effective_chat)
        self.assertFalse(bot.chat_allowed(update))


class LockKeyTests(unittest.TestCase):
    def setUp(self):
        self._saved = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)
        importlib.reload(bot)

    def test_lock_is_per_user_in_group_mode(self):
        _set_env(EHBOT_GROUP_MODE="1")
        importlib.reload(bot)
        self.assertEqual(bot._lock_key(111, 222), 222)
        self.assertEqual(bot._lock_key(111, 333), 333)  # different users, different locks

    def test_lock_is_per_chat_in_private_mode(self):
        _set_env(EHBOT_GROUP_MODE="0")
        importlib.reload(bot)
        self.assertEqual(bot._lock_key(111, 222), 111)
        self.assertEqual(bot._lock_key(222, 222), 222)  # different chats, different locks


class HandleMessageGroupTests(unittest.TestCase):
    """Integration-style tests of handle_message in group chats."""

    URL = "https://e-hentai.org/g/1234567/abc/"

    def setUp(self):
        self._saved = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)
        importlib.reload(bot)

    def _reload(self, **env):
        _set_env(**env)
        return importlib.reload(bot)

    async def _run(self, update):
        ctx = mock.Mock()
        ctx.bot.username = "bot"
        ctx.bot.id = 12345
        await bot.handle_message(update, ctx)

    def test_group_denied_when_group_mode_off_replies_notice(self):
        self._reload(EHBOT_GROUP_MODE="0", EHBOT_TELEGRAM_TOKEN="x")
        update = _make_update(-100123, "supergroup", 999, self.URL)
        with mock.patch.object(bot, "_process", new=mock.AsyncMock()) as process, \
             mock.patch.object(Message, "reply_text", new=mock.AsyncMock()) as reply:
            asyncio.run(self._run(update))
        process.assert_not_called()
        reply.assert_called_once_with("⚠️ 此 bot 未启用群组模式")

    def test_group_allowed_when_group_mode_on_processes(self):
        """Group mode on + @mention + link → processed."""
        self._reload(EHBOT_GROUP_MODE="1", EHBOT_TELEGRAM_TOKEN="x")
        ent = MessageEntity(type="mention", offset=0, length=4)
        update = _make_update(-100123, "supergroup", 999, "@bot " + self.URL, entities=[ent])
        with mock.patch.object(bot, "_process", new=mock.AsyncMock()) as process, \
             mock.patch.object(bot, "consume_daily_quota", return_value=(True, 9)), \
             mock.patch.object(Message, "reply_text", new=mock.AsyncMock()):
            asyncio.run(self._run(update))
        process.assert_awaited_once()
        self.assertEqual(process.await_args.args[2], self.URL)

    def test_group_not_in_whitelist_denied(self):
        """Whitelist miss + @mention + link → denied with notice."""
        self._reload(EHBOT_GROUP_MODE="1", EHBOT_GROUP_ALLOWED_CHATS="-100456",
                     EHBOT_TELEGRAM_TOKEN="x")
        ent = MessageEntity(type="mention", offset=0, length=4)
        update = _make_update(-100123, "supergroup", 999, "@bot " + self.URL, entities=[ent])
        with mock.patch.object(bot, "_process", new=mock.AsyncMock()) as process, \
             mock.patch.object(Message, "reply_text", new=mock.AsyncMock()) as reply:
            asyncio.run(self._run(update))
        process.assert_not_called()
        reply.assert_called_once_with("⚠️ 此 bot 未启用群组模式")

    def test_group_not_in_whitelist_unaddressed_stays_silent(self):
        """Whitelist miss + plain link (no @mention) → silent, no notice."""
        self._reload(EHBOT_GROUP_MODE="1", EHBOT_GROUP_ALLOWED_CHATS="-100456",
                     EHBOT_TELEGRAM_TOKEN="x")
        update = _make_update(-100123, "supergroup", 999, self.URL)
        with mock.patch.object(bot, "_process", new=mock.AsyncMock()) as process, \
             mock.patch.object(Message, "reply_text", new=mock.AsyncMock()) as reply:
            asyncio.run(self._run(update))
        process.assert_not_called()
        reply.assert_not_called()

    def test_private_chat_denied_when_whitelist_misses(self):
        self._reload(EHBOT_PUBLIC_ACCESS="0", EHBOT_ALLOWED_USERS="111",
                     EHBOT_TELEGRAM_TOKEN="x")
        update = _make_update(100, "private", 999, self.URL)
        with mock.patch.object(bot, "_process", new=mock.AsyncMock()) as process, \
             mock.patch.object(Message, "reply_text", new=mock.AsyncMock()) as reply:
            asyncio.run(self._run(update))
        process.assert_not_called()
        reply.assert_called_once_with("⚠️ 你没有权限使用此 bot")

    def test_different_group_members_do_not_block_each_other(self):
        self._reload(EHBOT_GROUP_MODE="1", EHBOT_TELEGRAM_TOKEN="x")
        lock_a = bot._lock_key(-100123, 111)
        lock_b = bot._lock_key(-100123, 222)
        self.assertNotEqual(lock_a, lock_b)
        bot._processing.add(lock_a)  # user A busy
        self.assertNotIn(lock_b, bot._processing)  # user B not blocked
        bot._processing.discard(lock_a)


if __name__ == "__main__":
    unittest.main()


class CustomKeyboardTests(unittest.TestCase):
    """Reply-keyboard (custom keyboard) routing and dual-mode _Menu adapter."""

    def setUp(self):
        self._saved = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)
        importlib.reload(bot)

    def _reload(self, **env):
        _set_env(**env)
        return importlib.reload(bot)

    def test_start_intro_mentions_reverse_image_search(self):
        """The /start welcome text must introduce photo reverse-search usage."""
        self._reload(EHBOT_TELEGRAM_TOKEN="x")
        update = _make_update(100, "private", 999, "/start")
        with mock.patch.object(Message, "reply_text", new=mock.AsyncMock()) as reply:
            asyncio.run(bot.start(update, mock.Mock()))
        text = reply.await_args.args[0]
        self.assertIn("以图搜图", text)
        self.assertIn("直接发送图片", text)
        self.assertIn("生成阅读页", text)

    def test_menu_routes_map_to_existing_handlers(self):
        self._reload(EHBOT_TELEGRAM_TOKEN="x")
        for label, fn_name in bot.MENU_ROUTES.items():
            self.assertTrue(callable(getattr(bot, fn_name, None)), f"{label} → {fn_name}")

    def test_menu_button_text_route_dispatch(self):
        """Pressing a fixed keyboard button sends its label as text;
        handle_menu_button must call the mapped handler."""
        self._reload(EHBOT_GROUP_MODE="1", EHBOT_TELEGRAM_TOKEN="x")
        update = _make_update(-100123, "supergroup", 999, "🎲 随机推荐")
        ctx = mock.Mock()
        with mock.patch.object(bot, "handle_recommend", new=mock.AsyncMock()) as rec, \
             mock.patch.object(Message, "reply_text", new=mock.AsyncMock()):
            asyncio.run(bot.handle_menu_button(update, ctx))
        rec.assert_awaited_once_with(update, ctx)

    def test_non_menu_text_not_dispatched(self):
        """Normal text (links, chat) must not be eaten by the menu router."""
        self._reload(EHBOT_TELEGRAM_TOKEN="x")
        update = _make_update(100, "private", 999, "随便聊聊")
        ctx = mock.Mock()
        with mock.patch.object(bot, "handle_recommend", new=mock.AsyncMock()) as rec:
            asyncio.run(bot.handle_menu_button(update, ctx))
        rec.assert_not_called()

    def test_menu_dual_mode_text_trigger_replies(self):
        """Text trigger: _Menu.say() must reply (not edit a callback message)."""
        self._reload(EHBOT_GROUP_MODE="1", EHBOT_TELEGRAM_TOKEN="x")
        update = _make_update(-100123, "supergroup", 999, "🎲 随机推荐")
        menu = bot._Menu(update)
        self.assertFalse(menu.is_callback)
        with mock.patch.object(Message, "reply_text", new=mock.AsyncMock()) as reply:
            asyncio.run(menu.say("hello"))
        reply.assert_awaited_once_with("hello")

    def test_menu_dual_mode_callback_trigger_edits(self):
        """Callback trigger: _Menu.say() must edit the original message."""
        self._reload(EHBOT_TELEGRAM_TOKEN="x")
        msg = _make_update(100, "private", 999, "x").message
        query = mock.Mock()
        query.from_user = mock.Mock(id=999)
        query.answer = mock.AsyncMock()
        query.edit_message_text = mock.AsyncMock()
        update = Update(update_id=2, callback_query=query, message=msg)
        menu = bot._Menu(update)
        self.assertTrue(menu.is_callback)
        asyncio.run(menu.say("edited", parse_mode="HTML"))
        query.edit_message_text.assert_awaited_once_with("edited", parse_mode="HTML")


class GroupTriggerTests(unittest.TestCase):
    """Group mode: only respond to messages that @mention the bot or reply
    to a bot message. Private chat behavior unchanged."""

    URL = "https://e-hentai.org/g/1234567/abc/"
    BOT_USERNAME = "hentaiviewer_bot"
    BOT_ID = 8406100638

    def setUp(self):
        self._saved = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)
        importlib.reload(bot)

    def _reload(self, **env):
        _set_env(**env)
        return importlib.reload(bot)

    def _ctx(self):
        ctx = mock.Mock()
        ctx.bot.id = self.BOT_ID
        ctx.bot.username = self.BOT_USERNAME
        return ctx

    def _group_update(self, text, entities=None, reply_to_bot=False):
        bot_obj = Bot(token="123456:test-token")
        chat = Chat(id=-100123, type="supergroup")
        user = User(id=999, is_bot=False, first_name="tester")
        reply_to = None
        if reply_to_bot:
            reply_to = Message(
                message_id=2, date=datetime.now(timezone.utc), chat=chat,
                from_user=User(id=self.BOT_ID, is_bot=True, first_name="bot"),
                text="old")
            reply_to.set_bot(bot_obj)
        msg = Message(
            message_id=1, date=datetime.now(timezone.utc), chat=chat,
            from_user=user, text=text, entities=entities, reply_to_message=reply_to)
        msg.set_bot(bot_obj)
        return Update(update_id=1, message=msg)

    def test_plain_link_in_group_not_processed(self):
        """No @mention, no reply → bot must stay silent in group mode."""
        self._reload(EHBOT_GROUP_MODE="1", EHBOT_TELEGRAM_TOKEN="x")
        update = self._group_update(self.URL)
        with mock.patch.object(bot, "_process", new=mock.AsyncMock()) as process, \
             mock.patch.object(Message, "reply_text", new=mock.AsyncMock()) as reply:
            asyncio.run(bot.handle_message(update, self._ctx()))
        process.assert_not_called()
        reply.assert_not_called()

    def test_mention_link_in_group_processed(self):
        self._reload(EHBOT_GROUP_MODE="1", EHBOT_TELEGRAM_TOKEN="x")
        ent = MessageEntity(type="mention", offset=0, length=len("@" + self.BOT_USERNAME))
        update = self._group_update("@" + self.BOT_USERNAME + " " + self.URL, entities=[ent])
        with mock.patch.object(bot, "_process", new=mock.AsyncMock()) as process, \
             mock.patch.object(bot, "consume_daily_quota", return_value=(True, 9)), \
             mock.patch.object(Message, "reply_text", new=mock.AsyncMock()):
            asyncio.run(bot.handle_message(update, self._ctx()))
        process.assert_awaited_once()
        self.assertEqual(process.await_args.args[2], self.URL)

    def test_reply_to_bot_link_in_group_processed(self):
        self._reload(EHBOT_GROUP_MODE="1", EHBOT_TELEGRAM_TOKEN="x")
        update = self._group_update(self.URL, reply_to_bot=True)
        with mock.patch.object(bot, "_process", new=mock.AsyncMock()) as process, \
             mock.patch.object(bot, "consume_daily_quota", return_value=(True, 9)), \
             mock.patch.object(Message, "reply_text", new=mock.AsyncMock()):
            asyncio.run(bot.handle_message(update, self._ctx()))
        process.assert_awaited_once()
        self.assertEqual(process.await_args.args[2], self.URL)

    def test_mention_without_link_ignored(self):
        self._reload(EHBOT_GROUP_MODE="1", EHBOT_TELEGRAM_TOKEN="x")
        ent = MessageEntity(type="mention", offset=0, length=len("@" + self.BOT_USERNAME))
        update = self._group_update("@" + self.BOT_USERNAME + " 随便聊聊", entities=[ent])
        with mock.patch.object(bot, "_process", new=mock.AsyncMock()) as process, \
             mock.patch.object(Message, "reply_text", new=mock.AsyncMock()) as reply:
            asyncio.run(bot.handle_message(update, self._ctx()))
        process.assert_not_called()
        reply.assert_not_called()

    def test_private_link_still_processed_without_mention(self):
        """DM behavior must not change: plain links always processed."""
        self._reload(EHBOT_GROUP_MODE="1", EHBOT_TELEGRAM_TOKEN="x")
        update = _make_update(100, "private", 999, self.URL)
        with mock.patch.object(bot, "_process", new=mock.AsyncMock()) as process, \
             mock.patch.object(bot, "consume_daily_quota", return_value=(True, 9)), \
             mock.patch.object(Message, "reply_text", new=mock.AsyncMock()):
            asyncio.run(bot.handle_message(update, self._ctx()))
        process.assert_awaited_once()

    def test_reply_to_other_user_in_group_not_processed(self):
        """Replying to another member (not the bot) must not trigger."""
        self._reload(EHBOT_GROUP_MODE="1", EHBOT_TELEGRAM_TOKEN="x")
        bot_obj = Bot(token="123456:test-token")
        chat = Chat(id=-100123, type="supergroup")
        reply_to = Message(
            message_id=2, date=datetime.now(timezone.utc), chat=chat,
            from_user=User(id=777, is_bot=False, first_name="other"),
            text="hi")
        reply_to.set_bot(bot_obj)
        msg = Message(
            message_id=1, date=datetime.now(timezone.utc), chat=chat,
            from_user=User(id=999, is_bot=False, first_name="tester"),
            text=self.URL, reply_to_message=reply_to)
        msg.set_bot(bot_obj)
        update = Update(update_id=1, message=msg)
        with mock.patch.object(bot, "_process", new=mock.AsyncMock()) as process, \
             mock.patch.object(Message, "reply_text", new=mock.AsyncMock()):
            asyncio.run(bot.handle_message(update, self._ctx()))
        process.assert_not_called()

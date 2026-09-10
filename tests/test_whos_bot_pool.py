"""hentaiviewer bot wiring: use the shared Whos.tv account pool."""
import asyncio
import importlib
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot
from telegram import Bot, Chat, Message, PhotoSize, Update, User


class BotWhosPoolTests(unittest.TestCase):
    def setUp(self):
        self._saved = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)
        importlib.reload(bot)

    def _reload(self, **env):
        env.setdefault('WHOS_TV_USERNAME', '')
        env.setdefault('WHOS_TV_PASSWORD', '')
        for key, value in env.items():
            os.environ[key] = value
        return importlib.reload(bot)

    def _pool_file(self, directory):
        path = os.path.join(directory, 'accounts.json')
        Path(path).write_text(json.dumps([{'username': 'pool1', 'password': 'p1'}]),
                              encoding='utf-8')
        return path

    def _photo_update(self):
        telegram_bot = Bot(token='123456:test-token')
        chat = Chat(id=100, type='private')
        user = User(id=999, is_bot=False, first_name='tester')
        photo = PhotoSize(file_id='FILEID1', file_unique_id='U1', width=100, height=100)
        message = Message(message_id=1, date=datetime.now(timezone.utc), chat=chat,
                          from_user=user, photo=[photo])
        message.set_bot(telegram_bot)
        return Update(update_id=1, message=message)

    def _ctx(self):
        ctx = mock.Mock()
        ctx.bot.username = 'bot'
        ctx.bot.id = 12345
        fake_file = mock.Mock()
        fake_file.download_to_drive = mock.AsyncMock()
        ctx.bot.get_file = mock.AsyncMock(return_value=fake_file)
        ctx.bot.send_media_group = mock.AsyncMock()
        ctx.bot.send_photo = mock.AsyncMock()
        return ctx

    def test_whos_enabled_when_only_pool_file_configured(self):
        with tempfile.TemporaryDirectory() as td:
            module = self._reload(EHBOT_TELEGRAM_TOKEN='x',
                                  WHOS_TV_ACCOUNTS_FILE=self._pool_file(td))
            self.assertTrue(module.WHOS_TV_ENABLED)

    def test_whos_disabled_without_accounts(self):
        module = self._reload(EHBOT_TELEGRAM_TOKEN='x')
        self.assertFalse(module.WHOS_TV_ENABLED)

    def test_photo_search_passes_pool_file_to_whos_search(self):
        with tempfile.TemporaryDirectory() as td:
            pool = self._pool_file(td)
            module = self._reload(EHBOT_TELEGRAM_TOKEN='x', WHOS_TV_ACCOUNTS_FILE=pool,
                                  EHBOT_ALLOWED_USERS='999', EHBOT_OWNER_USERS='999')
            update, ctx = self._photo_update(), self._ctx()
            with mock.patch.object(module, 'whos_tv_search', return_value=None) as search, \
                 mock.patch.object(module, 'iqdb_search', return_value=[]), \
                 mock.patch.object(module, 'trace_moe_search', return_value=[]), \
                 mock.patch.object(module, 'yandex_image_search', return_value=None), \
                 mock.patch.object(module, 'screenshot_ocr', return_value=''), \
                 mock.patch.object(module, 'consume_daily_quota', return_value=(True, 9)), \
                 mock.patch.object(Message, 'reply_text', new=mock.AsyncMock()) as reply:
                reply.return_value.edit_text = mock.AsyncMock()
                asyncio.run(module.handle_photo(update, ctx))

        self.assertTrue(search.called)
        self.assertEqual(search.call_args.kwargs.get('accounts_file'), pool)


if __name__ == '__main__':
    unittest.main()

import asyncio
import importlib
import os
import unittest
from unittest import mock

import bot


class HealthCommandTests(unittest.TestCase):
    def setUp(self):
        self.saved=dict(os.environ)
        os.environ['EHBOT_OWNER_USERS']='123'
        os.environ['EHBOT_TELEGRAM_TOKEN']='x'
        importlib.reload(bot)

    def tearDown(self):
        os.environ.clear(); os.environ.update(self.saved); importlib.reload(bot)

    def _update(self,user_id=123):
        u=mock.Mock(); u.effective_user.id=user_id; u.message.reply_text=mock.AsyncMock(); return u

    def test_owner_health_reports_services_without_spending_search(self):
        update=self._update(); ctx=mock.Mock()
        with mock.patch('bot._health_probe',return_value={
            'telegraph':(True,'可用'),'whos':(True,'40积分，搜索免费'),
            'catbox':(True,'可达'),'cache':(True,'12项'),'temp':(True,'0'),
        }):
            asyncio.run(bot.health_command(update,ctx))
        text=update.message.reply_text.await_args.args[0]
        self.assertIn('Whos.tv',text); self.assertIn('40积分',text); self.assertIn('临时文件',text)

    def test_non_owner_health_is_denied(self):
        update=self._update(999)
        asyncio.run(bot.health_command(update,mock.Mock()))
        self.assertIn('仅管理员',update.message.reply_text.await_args.args[0])

if __name__=='__main__': unittest.main()

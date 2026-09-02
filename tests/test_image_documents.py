import asyncio
import importlib
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
import bot
from telegram import Bot, Chat, Document, Message, Update, User


class ImageDocumentTests(unittest.TestCase):
    def setUp(self):
        self.saved=dict(os.environ)
        os.environ['EHBOT_TELEGRAM_TOKEN']='x'
        os.environ['WHOS_TV_USERNAME']=''
        os.environ['WHOS_TV_PASSWORD']=''
        importlib.reload(bot)

    def tearDown(self):
        os.environ.clear(); os.environ.update(self.saved); importlib.reload(bot)

    def test_image_document_runs_same_search_pipeline(self):
        tg=Bot(token='123456:test-token'); chat=Chat(id=100,type='private'); user=User(id=999,is_bot=False,first_name='t')
        doc=Document(file_id='D1',file_unique_id='DU1',file_name='original.webp',mime_type='image/webp',file_size=100)
        msg=Message(message_id=1,date=datetime.now(timezone.utc),chat=chat,from_user=user,document=doc); msg.set_bot(tg)
        update=Update(update_id=1,message=msg)
        ctx=mock.Mock(); fake=mock.Mock(); fake.download_to_drive=mock.AsyncMock(); ctx.bot.get_file=mock.AsyncMock(return_value=fake)
        ctx.bot.send_media_group=mock.AsyncMock(); ctx.bot.send_photo=mock.AsyncMock(); ctx.bot.username='bot'; ctx.bot.id=1
        with mock.patch('bot.iqdb_search',return_value=[]), mock.patch('bot.trace_moe_search',return_value=[]), \
             mock.patch('bot.yandex_image_search',return_value=None), mock.patch('bot.screenshot_ocr',return_value=''), \
             mock.patch.object(bot,'consume_daily_quota',return_value=(True,9)), \
             mock.patch.object(Message,'reply_text',new=mock.AsyncMock()) as reply:
            reply.return_value.edit_text=mock.AsyncMock(); asyncio.run(bot.handle_photo(update,ctx))
        ctx.bot.get_file.assert_awaited_once_with('D1')
        self.assertIn('未找到匹配',reply.return_value.edit_text.await_args.args[0])

    def test_non_image_document_is_ignored(self):
        tg=Bot(token='123456:test-token'); chat=Chat(id=100,type='private'); user=User(id=999,is_bot=False,first_name='t')
        doc=Document(file_id='D1',file_unique_id='DU1',file_name='x.zip',mime_type='application/zip',file_size=100)
        msg=Message(message_id=1,date=datetime.now(timezone.utc),chat=chat,from_user=user,document=doc); msg.set_bot(tg)
        update=Update(update_id=1,message=msg); ctx=mock.Mock()
        with mock.patch.object(Message,'reply_text',new=mock.AsyncMock()) as reply:
            asyncio.run(bot.handle_photo(update,ctx))
        reply.assert_not_awaited()

if __name__=='__main__': unittest.main()

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import bot


class SubscriptionTests(unittest.TestCase):
    def test_add_list_remove_subscription(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(bot,'SUBSCRIPTIONS_FILE',Path(tmp)/'subs.json'):
            self.assertTrue(bot.add_subscription(123,'标签','纯爱'))
            self.assertFalse(bot.add_subscription(123,'标签','纯爱'))
            self.assertEqual(bot.list_subscriptions(123),[{'type':'标签','term':'纯爱'}])
            self.assertTrue(bot.remove_subscription(123,'标签','纯爱'))
            self.assertEqual(bot.list_subscriptions(123),[])

    def test_match_subscriptions_uses_title_and_tags(self):
        items=[{'title':'Alice新作','tags':['纯爱','彩色'],'url':'https://x/1'}]
        subs=[{'type':'女优','term':'Alice'},{'type':'标签','term':'纯爱'},{'type':'作者','term':'Nobody'}]
        hits=bot.match_subscriptions(subs,items)
        self.assertEqual([h['subscription']['term'] for h in hits],['Alice','纯爱'])

    def test_command_rejects_invalid_type(self):
        update=mock.Mock(); update.effective_user.id=123; update.message.reply_text=mock.AsyncMock()
        ctx=mock.Mock(); ctx.args=['错误','词']
        asyncio.run(bot.subscribe_command(update,ctx))
        self.assertIn('类型',update.message.reply_text.await_args.args[0])

    def test_unauthorized_user_cannot_manage_subscriptions(self):
        update=mock.Mock(); update.effective_user.id=999; update.message.reply_text=mock.AsyncMock()
        ctx=mock.Mock(); ctx.args=['标签','纯爱']
        with mock.patch.object(bot,'chat_allowed',return_value=False), mock.patch.object(bot,'add_subscription') as add:
            asyncio.run(bot.subscribe_command(update,ctx))
        add.assert_not_called()
        self.assertIn('没有权限',update.message.reply_text.await_args.args[0])

    def test_notifications_are_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(bot,'SUBSCRIPTIONS_FILE',Path(tmp)/'subs.json'), \
             mock.patch.object(bot,'RANKING_CACHE_FILE',Path(tmp)/'rank.json'), \
             mock.patch.object(bot,'SUBSCRIPTION_NOTIFY_STATE_FILE',Path(tmp)/'state.json'):
            bot.add_subscription(123,'标签','纯爱')
            bot._save_ranking_cache({'eh':[{'title':'新作','tags':['纯爱'],'url':'https://x/1','tg_url':'https://t/1'}]})
            ctx=mock.Mock(); ctx.bot.send_message=mock.AsyncMock()
            asyncio.run(bot.notify_subscription_matches(ctx))
            asyncio.run(bot.notify_subscription_matches(ctx))
            ctx.bot.send_message.assert_awaited_once()

    def test_notification_failure_does_not_repeat_prior_success_or_block_next_user(self):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(bot,'SUBSCRIPTIONS_FILE',Path(tmp)/'subs.json'), \
             mock.patch.object(bot,'RANKING_CACHE_FILE',Path(tmp)/'rank.json'), \
             mock.patch.object(bot,'SUBSCRIPTION_NOTIFY_STATE_FILE',Path(tmp)/'state.json'):
            bot.add_subscription(1,'标签','纯爱'); bot.add_subscription(2,'标签','纯爱'); bot.add_subscription(3,'标签','纯爱')
            bot._save_ranking_cache({'eh':[{'title':'新作','tags':['纯爱'],'url':'https://x/1'}]})
            async def send(**kw):
                if kw['chat_id']==2: raise RuntimeError('blocked')
            ctx=mock.Mock(); ctx.bot.send_message=mock.AsyncMock(side_effect=send)
            asyncio.run(bot.notify_subscription_matches(ctx)); asyncio.run(bot.notify_subscription_matches(ctx))
            ids=[c.kwargs['chat_id'] for c in ctx.bot.send_message.await_args_list]
            self.assertEqual(ids.count(1),1); self.assertEqual(ids.count(3),1); self.assertEqual(ids.count(2),2)

if __name__=='__main__': unittest.main()

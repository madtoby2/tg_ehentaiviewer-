import tempfile
import unittest
from pathlib import Path

from history_store import ReaderHistoryStore


class ReaderHistoryTests(unittest.TestCase):
    def test_records_listed_only_for_the_same_user_in_newest_first_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ReaderHistoryStore(Path(tmp) / 'history.sqlite3')
            first = store.record(10, 'EH', 'https://e/1', 'First', 'https://t/1', 12)
            second = store.record(10, '18comic', 'https://j/2', 'Second', 'https://t/2', 20)
            store.record(20, 'EH', 'https://e/3', 'Other', 'https://t/3', 4)
            rows = store.list_for(10)
            self.assertEqual([x['history_id'] for x in rows], [second, first])
            self.assertEqual([x['title'] for x in rows], ['Second', 'First'])

    def test_remove_cannot_delete_another_users_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ReaderHistoryStore(Path(tmp) / 'history.sqlite3')
            item = store.record(10, 'EH', 'https://e/1', 'Private', 'https://t/1', 12)
            self.assertFalse(store.remove(20, item))
            self.assertEqual(len(store.list_for(10)), 1)
            self.assertTrue(store.remove(10, item))
            self.assertEqual(store.list_for(10), [])


if __name__ == '__main__':
    unittest.main()

class ReaderHistoryCommandTests(unittest.TestCase):
    def test_history_command_shows_only_users_own_reader_link(self):
        import asyncio
        from unittest import mock
        import bot
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(bot, 'HISTORY_FILE', Path(tmp) / 'history.sqlite3'):
            bot._history().record(10, 'EH', 'https://e/1', 'My title', 'https://t/1', 12)
            bot._history().record(20, 'EH', 'https://e/2', 'Other title', 'https://t/2', 4)
            update = mock.Mock(); update.effective_user.id = 10; update.message.reply_text = mock.AsyncMock(); update.effective_message = update.message
            asyncio.run(bot.history_command(update, mock.Mock()))
            text = update.message.reply_text.await_args.args[0]
            self.assertIn('My title', text)
            self.assertNotIn('Other title', text)

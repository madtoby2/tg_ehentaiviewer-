"""Saucenao reverse image search module tests.

Run: python3 -m pytest tests/test_saucenao.py -v
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
from scrapers import saucenao
from telegram import Bot, Chat, Message, MessageEntity, PhotoSize, Update, User

# Sample Saucenao JSON response (simplified but realistic)
SAMPLE_RESPONSE = {
    "header": {"status": 0, "user_id": 123, "account_type": 0, "limits": {"long": 100, "short": 30}},
    "results": [
        {
            "header": {
                "index_id": 3,
                "similarity": "95.31",
                "thumbnail": "https://img.saucenao.com/.../th.jpg",
            },
            "data": {
                "ext_urls": ["https://www.pixiv.net/artworks/123456"],
                "title": "サンプルタイトル",
                "member_name": "artist-san",
            },
        },
        {
            "header": {"index_id": 18, "similarity": "88.10"},
            "data": {
                "ext_urls": ["https://e-hentai.org/g/1234567/abc/", "https://exhentai.org/g/1234567/abc/"],
                "title": "Sample Gallery",
            },
        },
        {
            "header": {"index_id": 31, "similarity": "41.20"},
            "data": {"title": "weak match"},
        },
    ],
}


class SaucenaoParseTests(unittest.TestCase):
    def test_parses_results_with_similarity_and_urls(self):
        results = saucenao.parse_results(SAMPLE_RESPONSE)
        self.assertEqual(len(results), 3)
        r = results[0]
        self.assertEqual(r["similarity"], 95.31)
        self.assertEqual(r["title"], "サンプルタイトル")
        self.assertEqual(r["urls"], ["https://www.pixiv.net/artworks/123456"])

    def test_results_sorted_by_similarity_desc(self):
        results = saucenao.parse_results(SAMPLE_RESPONSE)
        sims = [r["similarity"] for r in results]
        self.assertEqual(sims, sorted(sims, reverse=True))

    def test_missing_ext_urls_ok(self):
        results = saucenao.parse_results(SAMPLE_RESPONSE)
        self.assertEqual(results[2]["urls"], [])
        self.assertEqual(results[2]["title"], "weak match")

    def test_no_results_returns_empty(self):
        self.assertEqual(saucenao.parse_results({"header": {"status": 0}, "results": []}), [])

    def test_error_status_returns_empty(self):
        self.assertEqual(saucenao.parse_results({"header": {"status": -1, "message": "bad key"}}), [])


class SaucenaoSearchTests(unittest.TestCase):
    def test_search_posts_image_and_returns_parsed_results(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp.write(b"fake-jpeg-bytes")
            img_path = tmp.name
        try:
            fake_resp = mock.Mock()
            fake_resp.json.return_value = SAMPLE_RESPONSE
            fake_resp.raise_for_status = mock.Mock()
            with mock.patch("requests.post", return_value=fake_resp) as post:
                results = saucenao.search("KEY123", img_path)
        finally:
            import os
            os.unlink(img_path)
        self.assertEqual(len(results), 3)
        # multipart file must be included
        self.assertIn("files", post.call_args.kwargs)
        self.assertEqual(post.call_args.kwargs["data"]["api_key"], "KEY123")
        self.assertEqual(post.call_args.kwargs["data"]["output_type"], "2")
        self.assertEqual(post.call_args.kwargs["data"]["numres"], "5")

    def test_search_http_error_returns_empty(self):
        fake_resp = mock.Mock()
        fake_resp.raise_for_status.side_effect = Exception("boom")
        with mock.patch("requests.post", return_value=fake_resp):
            self.assertEqual(saucenao.search("KEY123", "/tmp/test.jpg"), [])


class IndexNameTests(unittest.TestCase):
    def test_known_index_names(self):
        self.assertEqual(saucenao.index_name(3), "Pixiv")
        self.assertIn("e-hentai", saucenao.index_name(18).lower())
        self.assertIn("danbooru", saucenao.index_name(9).lower())

    def test_unknown_index_returns_number(self):
        self.assertEqual(saucenao.index_name(99999), "99999")


if __name__ == "__main__":
    unittest.main()


class PhotoHandlerTests(unittest.TestCase):
    """handle_photo: reverse image search flow in bot."""

    def setUp(self):
        self._saved = dict(os.environ)
        self._saved.pop("SAUCENAO_API_KEY", None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)
        importlib.reload(bot)

    def _reload(self, **env):
        _set_env2(**env)
        return importlib.reload(bot)

    def _photo_update(self, chat_id=100, chat_type="private", user_id=999):
        from telegram import PhotoSize
        bot_obj = Bot(token="123456:test-token")
        chat = Chat(id=chat_id, type=chat_type)
        user = User(id=user_id, is_bot=False, first_name="tester")
        photo = PhotoSize(file_id="FILEID1", file_unique_id="U1", width=100, height=100)
        msg = Message(
            message_id=1, date=datetime.now(timezone.utc), chat=chat,
            from_user=user, photo=[photo])
        msg.set_bot(bot_obj)
        return Update(update_id=1, message=msg)

    def _ctx(self):
        ctx = mock.Mock()
        ctx.bot.username = "bot"
        ctx.bot.id = 12345
        fake_file = mock.Mock()
        fake_file.download_to_drive = mock.AsyncMock()
        ctx.bot.get_file = mock.AsyncMock(return_value=fake_file)
        return ctx

    def test_no_api_key_prompts_config(self):
        self._reload(EHBOT_TELEGRAM_TOKEN="x")  # SAUCENAO_API_KEY unset
        update = self._photo_update()
        with mock.patch.object(Message, "reply_text", new=mock.AsyncMock()) as reply:
            asyncio.run(bot.handle_photo(update, self._ctx()))
        reply.assert_awaited_once()
        self.assertIn("SAUCENAO_API_KEY", reply.await_args.args[0])

    def test_search_result_shows_matches_and_reader_button(self):
        import tests.test_saucenao as ts
        self._reload(EHBOT_TELEGRAM_TOKEN="x", SAUCENAO_API_KEY="KEY")
        update = self._photo_update()
        results = [{
            "similarity": 95.3,
            "title": "Sample Gallery",
            "index_name": "E-Hentai",
            "urls": ["https://e-hentai.org/g/1234567/abc/"],
        }]
        ctx = self._ctx()
        with mock.patch("bot.saucenao_search", return_value=results) as search, \
             mock.patch.object(bot, "consume_daily_quota", return_value=(True, 9)), \
             mock.patch.object(Message, "reply_text", new=mock.AsyncMock()) as reply:
            status_edit = mock.AsyncMock()
            reply.return_value.edit_text = status_edit
            asyncio.run(bot.handle_photo(update, ctx))
        search.assert_called_once()
        self.assertEqual(search.call_args.args[0], "KEY")
        # final edit contains results + reader button
        self.assertTrue(status_edit.await_count >= 1)
        last_text = status_edit.await_args.args[0] if status_edit.await_args.args else ""
        self.assertIn("Sample Gallery", last_text)
        self.assertIn("ris_read:", str(status_edit.await_args.kwargs.get("reply_markup")))

    def test_group_photo_without_mention_silent(self):
        self._reload(EHBOT_GROUP_MODE="1", EHBOT_TELEGRAM_TOKEN="x", SAUCENAO_API_KEY="KEY")
        update = self._photo_update(chat_id=-100123, chat_type="supergroup")
        with mock.patch("bot.saucenao_search") as search, \
             mock.patch.object(Message, "reply_text", new=mock.AsyncMock()) as reply:
            asyncio.run(bot.handle_photo(update, self._ctx()))
        search.assert_not_called()
        reply.assert_not_called()

def _set_env2(**kw):
    import os
    for k, v in kw.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

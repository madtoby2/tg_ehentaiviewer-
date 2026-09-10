import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scrapers import whos_tv


def _client(**kwargs):
    """Client wired to a mocked Chrome-impersonation session."""
    session = mock.Mock()
    session.headers = mock.Mock()
    return whos_tv.WhosTvClient("user", "secret", poll_interval=0, max_wait=5,
                                session_factory=mock.Mock(return_value=session)), session


RESULT_HTML = '''
<html><head><title>图片匹配成功：GANA-2823！点击查看更多场景和影片</title></head><body>
<div class="rounded-xl overflow-hidden bg-white/3">
  <div class="p-3"><div class="flex gap-2.5">
    <a class="shrink-0" href="/videos/gana-2823"><div><div>100.0%</div></div></a>
    <div><a href="/videos/gana-2823"><p><span class="text-primary">GANA-2823</span> GANA-2823 Sample title</p></a></div>
  </div></div>
  <div class="result-image-best-match-frames">
    <a href="/frames/93586533"><img alt="匹配帧 1" src="https://img.test/frame.webp"/><div>100.0%</div><span>01:00:40</span></a>
  </div>
</div>
<div class="rounded-xl overflow-hidden bg-white/3">
  <div><a href="/videos/gana-2825"><span class="text-primary">GANA-2825</span><div>88.7%</div></a></div>
  <div class="result-image-best-match-frames"><a href="/videos/gana-2825?t=3795"><img src="https://img.test/frame2.webp"/><span>01:03:15</span><div>88.7%</div></a></div>
</div>
</body></html>
'''


class WhosTvParseTests(unittest.TestCase):
    def test_parse_result_returns_code_similarity_time_and_preview(self):
        result = whos_tv.parse_result_page(RESULT_HTML, "https://whos.tv/search-img/task?g=1")
        self.assertEqual(result["result_url"], "https://whos.tv/search-img/task?g=1")
        self.assertEqual(result["matches"][0]["code"], "GANA-2823")
        self.assertEqual(result["matches"][0]["similarity"], 100.0)
        self.assertEqual(result["matches"][0]["at"], "01:00:40")
        self.assertEqual(result["matches"][0]["preview"], "https://img.test/frame.webp")
        self.assertEqual(result["matches"][1]["code"], "GANA-2825")

    def test_search_logs_in_uploads_polls_and_parses(self):
        client, session = _client()
        login = mock.Mock(status_code=200)
        login.json.return_value = {"code": 200000, "message": "登录成功"}
        upload = mock.Mock(status_code=200, text="https://whos.tv/search-wait/task123")
        status = mock.Mock(status_code=200, text="https://whos.tv/search-img/task123?g=1")
        page = mock.Mock(status_code=200, text=RESULT_HTML)
        for response in (login, upload, status, page):
            response.raise_for_status = mock.Mock()
        session.post = mock.Mock(side_effect=[login, upload])
        session.get = mock.Mock(side_effect=[status, page])
        with tempfile.NamedTemporaryFile(suffix=".jpg") as image:
            result = client.search(image.name)
        self.assertEqual(result["matches"][0]["code"], "GANA-2823")
        self.assertEqual(session.post.call_args_list[0].args[0], "https://whos.tv/api/login")
        self.assertEqual(session.post.call_args_list[1].args[0], "https://whos.tv/upload-search")

    def test_search_waits_while_status_is_numeric_pending(self):
        client, session = _client()
        login = mock.Mock(status_code=200); login.json.return_value = {"code": 200000}; login.raise_for_status = mock.Mock()
        upload = mock.Mock(status_code=200, text="https://whos.tv/search-wait/task123"); upload.raise_for_status = mock.Mock()
        pending = mock.Mock(status_code=200, text="1"); pending.raise_for_status = mock.Mock()
        ready = mock.Mock(status_code=200, text="https://whos.tv/search-img/task123?g=1"); ready.raise_for_status = mock.Mock()
        page = mock.Mock(status_code=200, text=RESULT_HTML); page.raise_for_status = mock.Mock()
        session.post = mock.Mock(side_effect=[login, upload])
        session.get = mock.Mock(side_effect=[pending, ready, page])
        with tempfile.NamedTemporaryFile(suffix=".jpg") as image:
            result = client.search(image.name)
        self.assertEqual(result["matches"][0]["code"], "GANA-2823")

    def test_search_reauthenticates_once_when_upload_redirects_to_login(self):
        client, session = _client()
        ok = mock.Mock(status_code=200); ok.json.return_value = {"code": 200000}; ok.raise_for_status = mock.Mock()
        denied = mock.Mock(status_code=200, text="https://whos.tv/?login=1"); denied.raise_for_status = mock.Mock()
        upload = mock.Mock(status_code=200, text="https://whos.tv/search-wait/task123"); upload.raise_for_status = mock.Mock()
        status = mock.Mock(status_code=200, text="https://whos.tv/search-img/task123?g=1"); status.raise_for_status = mock.Mock()
        page = mock.Mock(status_code=200, text=RESULT_HTML); page.raise_for_status = mock.Mock()
        session.post = mock.Mock(side_effect=[ok, denied, ok, upload])
        session.get = mock.Mock(side_effect=[status, page])
        with tempfile.NamedTemporaryFile(suffix=".jpg") as image:
            result = client.search(image.name)
        self.assertEqual(result["matches"][0]["code"], "GANA-2823")
        self.assertEqual(sum(c.args[0].endswith('/api/login') for c in session.post.call_args_list), 2)

    def test_missing_credentials_returns_none(self):
        self.assertIsNone(whos_tv.search("", "", "/tmp/no.jpg"))


if __name__ == "__main__":
    unittest.main()

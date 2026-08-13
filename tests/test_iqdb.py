"""IQDB reverse image search module tests.

Run: python3 -m pytest tests/test_iqdb.py -v
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scrapers import iqdb

MATCH_HTML = """<html><head><title>Multi-service image search - Search results</title></head>
<body>
<h2>Your image</h2>
<table class="pages">
<tr>
<td class="image"><a href="https://e-hentai.org/g/1234567/abc/"><img src="/thumbs/1.jpg" alt="Sample Gallery"></a></td>
<td class="info"><span class="similarity">93%</span> similar</td>
</tr>
<tr>
<td class="image"><a href="https://danbooru.donmai.us/posts/555"><img src="/thumbs/2.jpg" alt=""></a></td>
<td class="info"><span class="similarity">85%</span> similar</td>
</tr>
</table>
</body></html>
"""

NO_MATCH_HTML = """<html><head><title>Multi-service image search - Search results</title></head>
<body><h2>Your image</h2><p>No relevant matches</p></body></html>
"""

QUEUED_HTML = """<html><head><title>Multi-service image search - Search results</title></head>
<body>iqdb is currently under high load, your query has been queued. Place in queue: 3</body></html>
"""


class IqdbParseTests(unittest.TestCase):
    def test_parses_matches_with_similarity_and_urls(self):
        results = iqdb.parse_results(MATCH_HTML)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["similarity"], 93.0)
        self.assertEqual(results[0]["title"], "Sample Gallery")
        self.assertEqual(results[0]["urls"], ["https://e-hentai.org/g/1234567/abc/"])
        self.assertEqual(results[1]["similarity"], 85.0)

    def test_sorted_by_similarity_desc(self):
        results = iqdb.parse_results(MATCH_HTML)
        sims = [r["similarity"] for r in results]
        self.assertEqual(sims, sorted(sims, reverse=True))

    def test_no_match_returns_empty(self):
        self.assertEqual(iqdb.parse_results(NO_MATCH_HTML), [])

    def test_queued_page_returns_empty(self):
        self.assertEqual(iqdb.parse_results(QUEUED_HTML), [])

    def test_empty_html_returns_empty(self):
        self.assertEqual(iqdb.parse_results(""), [])


class IqdbSearchTests(unittest.TestCase):
    def test_search_uploads_file_and_parses(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp.write(b"fake-jpeg-bytes")
            path = tmp.name
        try:
            fake_resp = mock.Mock()
            fake_resp.text = MATCH_HTML
            fake_resp.raise_for_status = mock.Mock()
            with mock.patch("requests.post", return_value=fake_resp) as post:
                results = iqdb.search(path)
        finally:
            import os
            os.unlink(path)
        self.assertEqual(len(results), 2)
        self.assertIn("files", post.call_args.kwargs)
        self.assertEqual(post.call_args.args[0], "https://iqdb.org/")

    def test_search_http_error_returns_empty(self):
        fake_resp = mock.Mock()
        fake_resp.raise_for_status.side_effect = Exception("boom")
        with mock.patch("requests.post", return_value=fake_resp):
            self.assertEqual(iqdb.search("/tmp/x.jpg"), [])

    def test_search_network_error_returns_empty(self):
        with mock.patch("requests.post", side_effect=Exception("timeout")):
            self.assertEqual(iqdb.search("/tmp/x.jpg"), [])

    def test_hard_timeout_terminates_worker_and_returns_empty(self):
        """IQDB must enforce a wall-clock deadline, not requests' idle timeout."""
        with mock.patch("scrapers.iqdb.subprocess.run", side_effect=__import__('subprocess').TimeoutExpired('curl', 45)) as run:
            self.assertEqual(iqdb.search_hard_timeout("/tmp/x.jpg", timeout=45), [])
        self.assertEqual(run.call_args.kwargs["timeout"], 50)


if __name__ == "__main__":
    unittest.main()

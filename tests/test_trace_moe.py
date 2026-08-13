"""Tests for trace.moe anime screenshot recognition."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scrapers import trace_moe

SAMPLE = {
    "error": "",
    "result": [{
        "anilist": {"id": 1, "title": {"native": "葬送のフリーレン", "romaji": "Sousou no Frieren", "english": "Frieren"}},
        "filename": "Frieren - 01.mkv",
        "episode": 1,
        "from": 125.5,
        "to": 127.0,
        "similarity": 0.927,
        "video": "https://api.trace.moe/video/x",
        "image": "https://api.trace.moe/image/x"
    }]
}

class TraceMoeTests(unittest.TestCase):
    def test_parse_result(self):
        r = trace_moe.parse_results(SAMPLE)
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["title"], "葬送のフリーレン")
        self.assertEqual(r[0]["episode"], 1)
        self.assertEqual(r[0]["at"], "02:05")
        self.assertAlmostEqual(r[0]["similarity"], 92.7)

    def test_ambiguous_top_matches_are_rejected(self):
        """Non-anime images often produce several near-identical false positives."""
        first = {**SAMPLE["result"][0], "similarity": .9999}
        second = {**SAMPLE["result"][0], "similarity": .9998, "filename": "other.mkv",
                  "anilist": {"id": 2, "title": {"native": "另一部动画"}}}
        self.assertEqual(trace_moe.parse_results({"result": [first, second]}), [])

    def test_low_similarity_filtered(self):
        d = {"result": [{**SAMPLE["result"][0], "similarity": .2}]}
        self.assertEqual(trace_moe.parse_results(d), [])

    def test_weak_77_percent_false_positive_filtered(self):
        """Real AV/general screenshots can receive bogus ~78% anime candidates."""
        d = {"result": [{**SAMPLE["result"][0], "similarity": .778}]}
        self.assertEqual(trace_moe.parse_results(d), [])

    def test_search_uploads_file(self):
        with tempfile.NamedTemporaryFile(suffix='.jpg') as f:
            f.write(b'x'); f.flush()
            resp = mock.Mock(); resp.json.return_value = SAMPLE; resp.raise_for_status = mock.Mock()
            with mock.patch('requests.post', return_value=resp) as post:
                out = trace_moe.search(f.name)
        self.assertEqual(len(out), 1)
        self.assertIn('files', post.call_args.kwargs)

if __name__ == '__main__': unittest.main()

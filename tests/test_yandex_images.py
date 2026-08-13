"""Tests for Yandex Images reverse-search upload."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from scrapers import yandex_images

SAMPLE = {"blocks": [{"params": {"originalImageUrl": "https://avatars.mds.yandex.net/get-images-cbir/1/abc/orig", "cbirId": "1/abc"}}]}

class YandexImagesTests(unittest.TestCase):
    def test_parse_upload_response(self):
        r = yandex_images.parse_upload(SAMPLE)
        self.assertEqual(r['cbir_id'], '1/abc')
        self.assertIn('cbir_id=1%2Fabc', r['search_url'])
        self.assertEqual(r['original_image_url'], SAMPLE['blocks'][0]['params']['originalImageUrl'])

    def test_invalid_response_returns_none(self):
        self.assertIsNone(yandex_images.parse_upload({}))

    def test_upload_posts_image(self):
        with tempfile.NamedTemporaryFile(suffix='.jpg') as f:
            f.write(b'x'); f.flush()
            resp=mock.Mock(); resp.json.return_value=SAMPLE; resp.raise_for_status=mock.Mock()
            with mock.patch('requests.post', return_value=resp) as post:
                out=yandex_images.search(f.name)
        self.assertEqual(out['cbir_id'], '1/abc')
        self.assertIn('files', post.call_args.kwargs)

if __name__ == '__main__': unittest.main()

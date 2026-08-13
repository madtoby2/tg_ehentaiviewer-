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

SAMPLE_HTML = '''
<div class="CbirSites-Item">
  <a class="Link Thumb" href="https://pbs.twimg.com/media/AAA.jpg">849×1200</a>
  <a class="Link Link_view_default" href="https://example.com/post?utm_medium=organic&amp;utm_source=yandexsmartcamera">赤 野 と び ら @tobiraakano</a>
  <a class="Link Link_view_outer CbirSites-ItemDomain" href="https://example.com/post">example.com</a>
</div>
<div class="CbirSites-Item">
  <a class="Link Link_view_default" href="https://site.test/work">Sample Work</a>
  <a class="Link Link_view_outer CbirSites-ItemDomain" href="https://site.test/work">site.test</a>
</div>
'''

class YandexImagesTests(unittest.TestCase):
    def test_parse_result_sites(self):
        sites = yandex_images.parse_sites(SAMPLE_HTML)
        self.assertEqual(len(sites), 2)
        self.assertEqual(sites[0]['title'], '赤 野 と び ら @tobiraakano')
        self.assertEqual(sites[0]['domain'], 'example.com')
        self.assertEqual(sites[0]['url'], 'https://example.com/post')
        self.assertEqual(sites[0]['image_url'], 'https://pbs.twimg.com/media/AAA.jpg')

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
            upload_resp=mock.Mock(); upload_resp.json.return_value=SAMPLE; upload_resp.raise_for_status=mock.Mock()
            sites_resp=mock.Mock(); sites_resp.text=SAMPLE_HTML; sites_resp.raise_for_status=mock.Mock()
            with mock.patch('requests.post', return_value=upload_resp) as post, \
                 mock.patch('requests.get', return_value=sites_resp) as get:
                out=yandex_images.search(f.name)
        self.assertEqual(out['cbir_id'], '1/abc')
        self.assertEqual(len(out['sites']), 2)
        self.assertIn('files', post.call_args.kwargs)
        get.assert_called_once()

if __name__ == '__main__': unittest.main()

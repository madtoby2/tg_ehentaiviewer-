import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scrapers.whos_tv import download_match_previews


class WhosPreviewTests(unittest.TestCase):
    def test_downloads_only_images_with_limits(self):
        matches = [
            {'code': 'A-001', 'preview': 'https://img.test/a.webp'},
            {'code': 'A-002', 'preview': 'ftp://bad.test/a.jpg'},
            {'code': 'A-003', 'preview': 'https://img.test/not-image'},
        ]
        import io
        from PIL import Image
        buf = io.BytesIO(); Image.new('RGB', (2, 2), 'red').save(buf, 'WEBP')
        good = mock.Mock(status_code=200, headers={'Content-Type': 'image/webp'})
        good.iter_content.return_value=[buf.getvalue()]
        good.raise_for_status = mock.Mock()
        bad = mock.Mock(status_code=200, content=b'html', headers={'Content-Type': 'text/html'})
        bad.raise_for_status = mock.Mock()
        with tempfile.TemporaryDirectory() as tmp, mock.patch('scrapers.whos_tv.requests.get', side_effect=[good, bad]):
            out = download_match_previews(matches, tmp, limit=3, max_bytes=100)
            self.assertEqual(len(out), 1)
            self.assertEqual(out[0]['code'], 'A-001')
            self.assertTrue(Path(out[0]['path']).exists())

    def test_stream_aborts_when_preview_exceeds_limit(self):
        match=[{'code':'A-001','preview':'https://img.test/a.webp'}]
        response=mock.Mock(status_code=200,headers={'Content-Type':'image/webp'})
        response.raise_for_status=mock.Mock(); response.iter_content.return_value=[b'a'*60,b'b'*60]
        with tempfile.TemporaryDirectory() as tmp, mock.patch('scrapers.whos_tv.requests.get',return_value=response):
            out=download_match_previews(match,tmp,max_bytes=100)
            self.assertEqual(out,[])
            self.assertEqual(list(Path(tmp).iterdir()),[])
            response.close.assert_called_once()

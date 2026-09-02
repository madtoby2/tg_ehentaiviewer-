"""Tests for local screenshot OCR and AV code extraction."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from PIL import Image
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from scrapers import screenshot_ocr

class ScreenshotOcrTests(unittest.TestCase):
    def test_extracts_common_av_codes(self):
        text = 'sample SSIS-123 and ipx 456 watermark'
        self.assertEqual(screenshot_ocr.extract_av_codes(text), ['SSIS-123', 'IPX-456'])

    def test_ocr_confused_prefix_is_normalized(self):
        """Tesseract commonly reads SSIS as SS15; normalize prefix leetspeak."""
        self.assertEqual(screenshot_ocr.extract_av_codes('SS15-123 sample screenshot'), ['SSIS-123'])

    def test_deduplicates_codes(self):
        self.assertEqual(screenshot_ocr.extract_av_codes('ssis123 SSIS-123'), ['SSIS-123'])

    def test_ignores_random_words(self):
        self.assertEqual(screenshot_ocr.extract_av_codes('HELLO WORLD 1080P'), [])

    def test_ocr_calls_tesseract_with_hard_timeout(self):
        done = mock.Mock(stdout='SSIS-123\nwatermark', returncode=0)
        with tempfile.TemporaryDirectory() as tmp:
            image=Path(tmp)/'x.jpg'; Image.new('RGB',(100,200),'white').save(image)
            with mock.patch('subprocess.run', return_value=done) as run:
                self.assertIn('SSIS-123', screenshot_ocr.ocr(str(image)))
        self.assertLessEqual(run.call_args_list[0].kwargs['timeout'], 10)

    def test_ocr_checks_lower_screen_in_english_before_full_image(self):
        fast = mock.Mock(stdout='MIBB-081', returncode=0)
        with tempfile.TemporaryDirectory() as tmp:
            image=Path(tmp)/'phone.jpg'; Image.new('RGB',(576,1280),'white').save(image)
            with mock.patch('subprocess.run', return_value=fast) as run:
                text=screenshot_ocr.ocr(str(image))
        self.assertIn('MIBB-081', text)
        args=run.call_args_list[0].args[0]
        self.assertEqual(args[args.index('-l')+1], 'eng')
        self.assertNotEqual(args[1], str(image))

    def test_ocr_timeout_returns_empty(self):
        import subprocess
        with mock.patch('subprocess.run', side_effect=subprocess.TimeoutExpired('tesseract', 20)):
            self.assertEqual(screenshot_ocr.ocr('/tmp/x.jpg'), '')

if __name__ == '__main__': unittest.main()

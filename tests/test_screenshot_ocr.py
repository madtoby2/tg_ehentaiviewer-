"""Tests for local screenshot OCR and AV code extraction."""
import sys
import unittest
from pathlib import Path
from unittest import mock
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
        with mock.patch('subprocess.run', return_value=done) as run:
            self.assertIn('SSIS-123', screenshot_ocr.ocr('/tmp/x.jpg'))
        self.assertEqual(run.call_args.kwargs['timeout'], 20)

    def test_ocr_timeout_returns_empty(self):
        import subprocess
        with mock.patch('subprocess.run', side_effect=subprocess.TimeoutExpired('tesseract', 20)):
            self.assertEqual(screenshot_ocr.ocr('/tmp/x.jpg'), '')

if __name__ == '__main__': unittest.main()

import sys
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from publishers.jm_telegraph import _collect_completed_results


class CollectCompletedResultsTests(unittest.TestCase):
    def test_timeout_waits_for_running_worker_before_returning(self):
        worker_finished = False

        def slow_worker():
            nonlocal worker_finished
            time.sleep(0.08)
            worker_finished = True
            return "late"

        started = time.monotonic()
        results, timed_out = _collect_completed_results(
            [(0, slow_worker)], max_workers=1, timeout=0.01
        )
        elapsed = time.monotonic() - started

        self.assertTrue(timed_out)
        self.assertTrue(worker_finished, "helper returned while a worker still used temp/session resources")
        self.assertGreaterEqual(elapsed, 0.07)
        self.assertEqual(results, {})

    def test_collects_successful_results_in_input_order_keys(self):
        results, timed_out = _collect_completed_results(
            [(2, lambda: "c"), (0, lambda: "a"), (1, lambda: None)],
            max_workers=2,
            timeout=1,
        )

        self.assertFalse(timed_out)
        self.assertEqual(results, {0: "a", 2: "c"})

    def test_release_process_memory_collects_and_trims_allocator(self):
        import publishers.jm_telegraph as publisher

        fake_libc = mock.Mock()
        with mock.patch('gc.collect') as collect, mock.patch('ctypes.CDLL', return_value=fake_libc):
            publisher._release_process_memory()

        collect.assert_called_once_with()
        fake_libc.malloc_trim.assert_called_once_with(0)

    def test_gallery_publish_releases_process_memory_after_completion(self):
        import tempfile
        import publishers.jm_telegraph as publisher

        session = mock.Mock()
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(publisher, 'TMP_ROOT', Path(tmp)), \
             mock.patch.object(publisher, 'cleanup_tmp'), \
             mock.patch.object(publisher, '_build_retry_session', return_value=session), \
             mock.patch.object(publisher, '_collect_completed_results', return_value=({}, False)), \
             mock.patch.object(publisher, '_release_process_memory') as release:
            result = publisher.publish_jm_gallery('test', [object()], max_workers=1)

        self.assertEqual(result['error'], 'Failed to upload any decoded JM images')
        release.assert_called_once_with()
        session.close.assert_called_once_with()

    def test_decode_worker_closes_response_and_pillow_images(self):
        import tempfile
        import publishers.jm_telegraph as publisher

        response = mock.Mock()
        response.content = b'x' * 1001
        raw_image = mock.Mock()
        decoded_image = mock.Mock()
        raw_image.convert.return_value = decoded_image
        session = mock.Mock()
        session.get.return_value = response
        detail = mock.Mock(download_url='https://example.com/image.jpg')

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(publisher.JmImageTool, 'open_image', return_value=raw_image), \
             mock.patch.object(publisher.JmImageTool, 'get_num_by_detail', return_value=0), \
             mock.patch.object(publisher.JmImageTool, 'decode_and_save'), \
             mock.patch.object(publisher, '_upload_image_host', return_value='https://catbox.test/image.jpg'):
            result = publisher._download_decode_upload_one(detail, Path(tmp), 0, session)

        self.assertEqual(result, 'https://catbox.test/image.jpg')
        response.close.assert_called_once_with()
        raw_image.close.assert_called_once_with()
        decoded_image.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()

import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot


class DailyRankingConcurrencyTests(unittest.TestCase):
    def test_comic_generators_are_bounded_to_one_gallery_at_a_time(self):
        active = 0
        peak = 0

        async def generate(item):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return item['url']

        items = [{'url': f'https://example/{i}'} for i in range(5)]
        results = asyncio.run(bot._run_ranking_generators(items, generate, concurrency=1))

        self.assertEqual(peak, 1)
        self.assertEqual(results, [item['url'] for item in items])

    def test_generator_exceptions_are_isolated_per_gallery(self):
        async def generate(item):
            if item['url'].endswith('/2'):
                raise RuntimeError('broken')
            return item['url']

        items = [{'url': f'https://example/{i}'} for i in range(4)]
        results = asyncio.run(bot._run_ranking_generators(items, generate, concurrency=2))

        self.assertEqual(results[0], 'https://example/0')
        self.assertEqual(results[1], 'https://example/1')
        self.assertIsInstance(results[2], RuntimeError)
        self.assertEqual(results[3], 'https://example/3')


if __name__ == '__main__':
    unittest.main()

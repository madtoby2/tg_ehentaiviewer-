import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import bot


class RankingPrewarmTests(unittest.TestCase):
    def test_prewarm_preserves_items_and_isolates_failure(self):
        items=[{'url':'u1','title':'a','total_pages':1},{'url':'u2','title':'b','total_pages':2}]
        async def run():
            with mock.patch.dict('os.environ',{'DAILY_RANKING_COMIC_TELEGRAPH':'1'}), \
                 mock.patch.object(bot,'fetch_eh_ranking',return_value=items), \
                 mock.patch.object(bot,'_gen_tg_telegraph',side_effect=['https://t/1',RuntimeError('x')]):
                return await bot._prewarm_ranking_source('eh')
        result=asyncio.run(run())
        self.assertEqual(result[0]['tg_url'],'https://t/1')
        self.assertIsNone(result[1]['tg_url'])
        self.assertEqual([x['title'] for x in result],['a','b'])

    def test_ranking_cache_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(bot,'RANKING_CACHE_FILE',Path(tmp)/'rank.json'):
            bot._save_ranking_cache({'eh':[{'title':'x'}]})
            self.assertEqual(bot._load_ranking_cache()['eh'][0]['title'],'x')

    def test_stale_ranking_cache_is_rejected(self):
        stale=(datetime.now(bot.TZ_UTC8)-timedelta(days=1)).isoformat()
        self.assertFalse(bot._ranking_cache_is_fresh({'updated_at':stale,'eh':[{'title':'old'}]}))
        self.assertTrue(bot._ranking_cache_is_fresh({'updated_at':datetime.now(bot.TZ_UTC8).isoformat()}))

    def test_one_source_failure_does_not_refresh_its_stale_cache(self):
        stale=(datetime.now(bot.TZ_UTC8)-timedelta(days=2)).isoformat()
        async def run(path):
            with mock.patch.object(bot,'RANKING_CACHE_FILE',path), \
                 mock.patch.object(bot,'_prewarm_ranking_source',side_effect=[[],[{'title':'new comic'}]]):
                bot._save_ranking_cache({
                    'eh':[{'title':'old eh'}], 'comic':[{'title':'old comic'}],
                    'source_updated_at':{'eh':stale,'comic':stale},
                })
                await bot.prewarm_rankings(mock.Mock())
                return bot._load_ranking_cache()
        with tempfile.TemporaryDirectory() as tmp:
            data=asyncio.run(run(Path(tmp)/'rank.json'))
        self.assertFalse(bot._ranking_cache_is_fresh(data,'eh'))
        self.assertTrue(bot._ranking_cache_is_fresh(data,'comic'))
        self.assertEqual(data['eh'][0]['title'],'old eh')
        self.assertEqual(data['comic'][0]['title'],'new comic')

if __name__=='__main__': unittest.main()

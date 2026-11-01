"""Source coverage and shortfall regressions; fake public feeds, real temporary SQLite."""
import sys,tempfile,unittest,urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from datetime import datetime
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'backend'))
import newsroom
from store import Store
from planning import Planning

NOW=datetime(2026,11,1,10,tzinfo=newsroom.TZ)
SOURCES=[{'category':'ai','name':'AI','url':'https://example.test/ai','home':'https://example.test/ai'}, {'category':'finance','name':'Failing','url':'https://example.test/fail','home':'https://example.test/fail'}, {'category':'finance','name':'Finance','url':'https://example.test/finance','home':'https://example.test/finance'}]
def article(n):return {'id':str(n),'title':'Public headline '+str(n),'url':'https://example.test/'+str(n),'published':NOW.isoformat(),'source':'Synthetic source','excerpt':'Synthetic test excerpt'}
class Coverage(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.ledger=Store(Path(self.tmp.name)/'db.sqlite3');self.room=newsroom.NewsRoom(self.ledger,Planning(self.ledger),SimpleNamespace(key='',model='mock'))
  self.clock=patch.object(newsroom,'now_local',return_value=NOW);self.sources=patch.object(newsroom,'SOURCES',SOURCES);self.clock.start();self.sources.start()
 def tearDown(self):self.sources.stop();self.clock.stop();self.tmp.cleanup()
 def refresh(self,n,failed=True):
  def collect(source,now):
   if source['name']=='Failing':
    if failed:raise urllib.error.HTTPError(source['url'],301,'moved',{},None)
    return []
   return [article(i) for i in range(n)]
  with patch.object(newsroom,'collect',side_effect=collect):self.room.refresh(asynchronous=False)
  return self.room.overview()['channels']['finance']
 def test_fallback_feed_supplies_five(self):
  c=self.refresh(7);self.assertEqual(len(c['items']),5);self.assertEqual(c['candidate_count'],7);self.assertEqual(c['shortfall_reason'],'');self.assertEqual(c['source_results'][0]['error_code'],'http_301');self.assertEqual(c['source_results'][1]['article_count'],7)
 def test_one_article_is_explicit_source_shortfall(self):
  c=self.refresh(1);self.assertEqual(len(c['items']),1);self.assertEqual(c['shortfall_reason'],'source_unavailable');self.assertFalse(c['ai_generated'])
 def test_no_recent_articles_differ_from_error(self):
  c=self.refresh(1,failed=False);self.assertEqual(c['source_results'][0]['status'],'no_recent_content');self.assertEqual(c['shortfall_reason'],'insufficient_recent_sources')
 def test_ai_underselection_not_source_failure(self):
  self.room.provider.key='dummy-not-real';self.room._summarize=lambda cat,rows:[dict(rows[0],summary='test',why='test')]
  c=self.refresh(6,failed=False);self.assertEqual(c['candidate_count'],6);self.assertEqual(c['shortfall_reason'],'editorial_selection');self.assertTrue(c['ai_generated'])
 def test_failed_refresh_keeps_old_date_and_marks_attempt(self):
  original=self.refresh(6);later=datetime(2026,11,2,10,tzinfo=newsroom.TZ)
  with patch.object(newsroom,'now_local',return_value=later),patch.object(newsroom,'collect',side_effect=ValueError('broken feed')):self.room.refresh(asynchronous=False)
  c=self.room.overview()['channels']['finance'];self.assertTrue(c['stale']);self.assertEqual(c['updated_at'],original['updated_at']);self.assertEqual(c['last_attempt'],later.isoformat());self.assertEqual(c['candidate_count'],0);self.assertEqual(c['shortfall_reason'],'source_unavailable')
 def test_unique_candidates_count_after_dedup(self):
  def collect(s,n):return [article(1),article(1)]
  with patch.object(newsroom,'collect',side_effect=collect):self.room.refresh(asynchronous=False)
  c=self.room.overview()['channels']['finance'];self.assertEqual(c['candidate_count'],1);self.assertEqual(len(c['items']),1);self.assertEqual(c['source_results'][0]['article_count'],2)
if __name__=='__main__':unittest.main()

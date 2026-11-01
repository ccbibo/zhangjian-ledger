"""P09 scheduler tests: real temporary SQLite, mock clock/collector/model, no network."""
import hashlib
import json
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
import newsroom
from store import Store


class Planning:
    def __init__(self):
        self.settings = {}
    def state(self):
        return {'settings': self.settings}


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='p09-news-sqlite-')
        self.ledger = Store(Path(self.folder.name) / 'news.sqlite3')
        self.planning = Planning()
        self.provider = SimpleNamespace(key='synthetic-test-key-not-real', model='mock-model')
        self.clock = datetime(2026, 11, 1, 7, 59, tzinfo=newsroom.TZ)
        self.collected = []
        self.clock_patch = patch.object(newsroom, 'now_local', lambda: self.clock)
        self.collect_patch = patch.object(newsroom, 'collect', self.fake_collect)
        self.network_patch = patch.object(newsroom, 'safe_open', side_effect=AssertionError('No external network allowed in P09 test'))
        self.clock_patch.start(); self.collect_patch.start(); self.network_patch.start()
        self.room = self.new_room()
        self.observation = {}

    def tearDown(self):
        OBSERVATIONS[self._testMethodName] = self.observation
        self.clock_patch.stop(); self.collect_patch.stop(); self.network_patch.stop()
        self.folder.cleanup()

    def new_room(self):
        room = newsroom.NewsRoom(self.ledger, self.planning, self.provider)
        room._summarize = lambda category, candidates: [dict(a, summary='合成摘要', why='合成重要性') for a in candidates[:5]]
        return room

    def fake_collect(self, source, now):
        self.collected.append((source['name'], now.isoformat()))
        return [{'id': source['name'] + '-' + str(i), 'title': source['name'] + ' synthetic ' + str(i),
                 'url': 'https://example.test/' + source['category'] + '/' + str(i),
                 'source': source['name'], 'published': now.isoformat(), 'excerpt': '合成來源'} for i in range(4)]

    def tick_sync(self, room=None):
        room = room or self.room
        refresh = room.refresh
        with patch.object(room, 'refresh', lambda: refresh(asynchronous=False)):
            return room.tick()

    def test_01_default_schedule_before_after_0800(self):
        self.assertEqual(self.room.schedule(), {'enabled': True, 'hour': 8, 'minute': 0})
        self.assertFalse(self.tick_sync())
        self.assertEqual(len(self.collected), 0)
        self.clock = self.clock.replace(hour=8, minute=0)
        self.assertTrue(self.tick_sync())
        overview = self.room.overview()
        self.assertEqual(overview['timezone'], 'Asia/Taipei')
        self.assertEqual(len(self.collected), len(newsroom.SOURCES))
        for category in ('ai', 'finance'):
            channel = overview['channels'][category]
            self.assertEqual(len(channel['items']), 5)
            self.assertEqual(channel['date'], '2026-11-01')
            self.assertTrue(channel['ai_generated'])
        self.observation = {'calls':len(self.collected),'job':overview['job'],'channel_counts':{k:len(v['items']) for k,v in overview['channels'].items()}}

    def test_02_same_day_tick_does_not_repeat(self):
        self.clock = self.clock.replace(hour=8, minute=1)
        self.assertTrue(self.tick_sync())
        calls = len(self.collected)
        self.clock = self.clock.replace(hour=20)
        self.assertFalse(self.tick_sync())
        self.assertEqual(len(self.collected), calls)
        self.observation = {'calls_after_second_tick':len(self.collected),'expected_once':calls}

    def test_03_disabled_schedule_does_not_fetch(self):
        self.planning.settings['news_schedule'] = {'enabled':False,'hour':8,'minute':0}
        self.clock = self.clock.replace(hour=9)
        self.assertFalse(self.tick_sync())
        self.assertIsNone(self.room.overview()['next_run'])
        self.assertEqual(self.collected, [])
        self.observation = {'next_run':None,'calls':0}

    def test_04_two_instances_same_db_lease_excludes_second(self):
        self.clock = self.clock.replace(hour=8)
        second = self.new_room()
        entered, release = threading.Event(), threading.Event()
        original = self.fake_collect
        calls = []
        def block_collect(source, now):
            calls.append(source['name'])
            if len(calls) == 1:
                entered.set()
                if not release.wait(4):
                    raise AssertionError('synthetic block timeout')
            return original(source, now)
        with patch.object(newsroom, 'collect', block_collect):
            thread = threading.Thread(target=lambda:self.room.refresh(asynchronous=False))
            thread.start()
            self.assertTrue(entered.wait(2))
            competing = second.refresh(asynchronous=False)
            self.assertFalse(competing['started'])
            self.assertEqual(competing['status'], 'running')
            release.set(); thread.join(4)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(calls), len(newsroom.SOURCES))
        self.observation = {'second_result':competing,'total_source_calls':len(calls)}

    def test_05_failed_day_retains_old_date_and_items_stale(self):
        self.clock = self.clock.replace(hour=8)
        self.tick_sync()
        old = self.room.overview()['channels']
        self.clock += timedelta(days=1)
        with patch.object(newsroom, 'collect', side_effect=OSError('synthetic feed failure')):
            self.assertTrue(self.tick_sync())
        new = self.room.overview()
        for category in ('ai','finance'):
            self.assertTrue(new['channels'][category]['stale'])
            self.assertEqual(new['channels'][category]['date'], '2026-11-01')
            self.assertEqual(new['channels'][category]['items'], old[category]['items'])
            self.assertTrue(new['channels'][category]['last_attempt'].startswith('2026-11-02'))
        self.assertEqual(new['job']['status'], 'failed')
        self.assertFalse(self.tick_sync())
        self.observation = {'channel_dates':{k:v['date'] for k,v in new['channels'].items()},'last_attempt':{k:v['last_attempt'] for k,v in new['channels'].items()},'job':new['job']}

    def test_06_restart_persists_digest_and_daily_attempt(self):
        self.clock = self.clock.replace(hour=8)
        self.tick_sync()
        old = self.room.overview()
        restarted = self.new_room()
        restored = restarted.overview()
        self.assertEqual(old['channels'], restored['channels'])
        self.assertEqual(old['job'], restored['job'])
        self.assertFalse(self.tick_sync(restarted))
        self.observation = {'restored_channels':list(restored['channels']),'job_status':restored['job']['status'],'repeat_tick_started':False}

    def test_07_zero_sources_never_pads_to_five(self):
        self.clock = self.clock.replace(hour=8)
        with patch.object(newsroom, 'collect', return_value=[]):
            self.assertTrue(self.tick_sync())
        current = self.room.overview()
        for category in ('ai','finance'):
            channel = current['channels'][category]
            self.assertEqual(channel['items'], [])
            self.assertEqual(channel['candidate_count'], 0)
            self.assertFalse(channel['ai_generated'])
            self.assertFalse(channel['stale'])
        self.assertEqual(current['job']['status'], 'failed')
        self.assertFalse(self.tick_sync())
        self.observation = {'job':current['job'],'channels':current['channels']}

    def test_08_model_failure_marks_sources_only_not_ai_generated(self):
        self.clock = self.clock.replace(hour=8)
        self.room._summarize = lambda *args: (_ for _ in ()).throw(ValueError('synthetic LLM error'))
        self.tick_sync()
        overview = self.room.overview()
        for category in ('ai','finance'):
            channel = overview['channels'][category]
            self.assertFalse(channel['ai_generated'])
            self.assertEqual(len(channel['items']), 5)
            self.assertTrue(all(a['summary'] == '' and a['why'] == '' for a in channel['items']))
            self.assertTrue(any(e['source']=='DeepSeek' for e in channel['errors']))
        self.observation = {'ai_generated':{k:v['ai_generated'] for k,v in overview['channels'].items()},'job':overview['job']}

    def test_09_next_day_fetches_again_only_after_scheduled_time(self):
        self.clock = self.clock.replace(hour=8)
        self.tick_sync()
        first_calls = len(self.collected)
        self.clock = (self.clock + timedelta(days=1)).replace(hour=7, minute=59)
        self.assertFalse(self.tick_sync())
        self.clock = self.clock.replace(hour=8,minute=0)
        self.assertTrue(self.tick_sync())
        self.assertEqual(len(self.collected), first_calls * 2)
        self.observation = {'source_calls':len(self.collected),'daily_source_count':first_calls}

    def test_10_expired_worker_cannot_overwrite_newer_refresh(self):
        self.clock = self.clock.replace(hour=8)
        second = self.new_room()
        entered, release = threading.Event(), threading.Event()
        first_thread = [None]
        def slow_collect(source, now):
            if threading.current_thread() is first_thread[0] and not entered.is_set():
                entered.set()
                if not release.wait(4):
                    raise AssertionError('synthetic block timeout')
            return self.fake_collect(source, now)
        with patch.object(newsroom, 'collect', slow_collect):
            first_thread[0] = threading.Thread(target=lambda:self.room.refresh(asynchronous=False))
            first_thread[0].start(); self.assertTrue(entered.wait(2))
            self.clock += timedelta(seconds=181)
            replacement = second.refresh(asynchronous=False)
            before_old_resumes = second.overview()['channels']
            release.set(); first_thread[0].join(4)
            after = second.overview()['channels']
        self.observation = {'replacement_started':replacement['started'],'newer_updated_at':{k:v['updated_at'] for k,v in before_old_resumes.items()},'final_updated_at':{k:v['updated_at'] for k,v in after.items()}}
        self.assertTrue(replacement['started'])
        self.assertEqual({k:v['updated_at'] for k,v in after.items()}, {k:v['updated_at'] for k,v in before_old_resumes.items()}, 'expired old worker overwrote newer cache after lease takeover')


    def test_11_expired_without_takeover_shows_failure_and_keeps_cache(self):
        self.clock = self.clock.replace(hour=8,minute=0)
        self.tick_sync()
        previous = self.room.overview()['channels']
        self.clock += timedelta(days=1)
        entered, release = threading.Event(), threading.Event()
        def blocked(source, now):
            entered.set()
            if not release.wait(4):
                raise AssertionError('synthetic wait timeout')
            return self.fake_collect(source, now)
        with patch.object(newsroom, 'collect', blocked):
            worker=threading.Thread(target=lambda:self.room.refresh(asynchronous=False))
            worker.start();self.assertTrue(entered.wait(2))
            self.clock += timedelta(seconds=181)
            displayed=self.room.overview()
            self.assertEqual(displayed['job']['status'],'failed')
            release.set();worker.join(4);self.assertFalse(worker.is_alive())
        final=self.room.overview()
        self.assertEqual(final['channels'],previous)
        self.assertEqual(final['job']['status'],'failed')
        self.observation={'displayed_while_expired':displayed['job'],'final_job':final['job'],'old_channels_unchanged':final['channels']==previous}

    def test_12_old_worker_finally_does_not_clobber_active_takeover_lease(self):
        self.clock = self.clock.replace(hour=8,minute=0)
        second=self.new_room()
        entered_a,entered_b=threading.Event(),threading.Event()
        release_a,release_b=threading.Event(),threading.Event()
        workers={}
        def controlled(source,now):
            current=threading.current_thread()
            if current is workers.get('a') and not entered_a.is_set():
                entered_a.set()
                if not release_a.wait(4):raise AssertionError('wait A timeout')
            elif current is workers.get('b') and not entered_b.is_set():
                entered_b.set()
                if not release_b.wait(4):raise AssertionError('wait B timeout')
            return self.fake_collect(source,now)
        with patch.object(newsroom,'collect',controlled):
            workers['a']=threading.Thread(target=lambda:self.room.refresh(asynchronous=False))
            workers['a'].start();self.assertTrue(entered_a.wait(2))
            self.clock+=timedelta(seconds=181)
            workers['b']=threading.Thread(target=lambda:second.refresh(asynchronous=False))
            workers['b'].start();self.assertTrue(entered_b.wait(2))
            takeover_job=second.overview()['job']
            release_a.set();workers['a'].join(4);self.assertFalse(workers['a'].is_alive())
            active=second.overview()['job']
            self.assertEqual(active['run_id'],takeover_job['run_id'])
            self.assertEqual(active['status'],'running')
            self.assertFalse(self.new_room().refresh(asynchronous=False)['started'])
            release_b.set();workers['b'].join(4);self.assertFalse(workers['b'].is_alive())
        done=second.overview()['job']
        self.assertEqual(done['run_id'],takeover_job['run_id'])
        self.assertEqual(done['status'],'complete')
        self.observation={'takeover_run_id':takeover_job['run_id'],'after_old_finally_status':active['status'],'final_status':done['status'],'old_worker_cannot_release_new_lease':True}


OBSERVATIONS = {}
if __name__ == '__main__':
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(ScheduleTests)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    report={'reviewer':'P09','module':'backend/newsroom.py','sha256':hashlib.sha256((ROOT/'backend/newsroom.py').read_bytes()).hexdigest(),
            'evidence_type':'Real temporary SQLite + fake Asia/Taipei clock + mocked source collection and model calls; no external network, no real key, no HTTP server/browser.',
            'tests':result.testsRun,'passed':result.testsRun-len(result.failures)-len(result.errors),'failures':[{'test':str(t),'trace':trace} for t,trace in result.failures],
            'errors':[{'test':str(t),'trace':trace} for t,trace in result.errors],'observations':OBSERVATIONS,'ui_score':None}
    (ROOT/('qa/reports/P09_news_schedule_'+(sys.argv[1] if len(sys.argv)>1 else 'r1')+'.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2))
    sys.exit(not result.wasSuccessful())

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from collector import CollectionError
from sync_state import CheckpointStore, RetryableError, retry_due, usable, retry_delay
from us_collector import collect_market, PublicClient, main


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 1, 4, tzinfo=timezone.utc)
        self.market = {'id': 50, 'name': 'Missoula', 'timezone': 'America/Denver'}

    def snapshot(self, **changes):
        data = {'version': 1, 'market_id': 50, 'status': 'failed', 'retryable': True,
                'window_start': self.now.isoformat()}
        data.update(changes)
        return data

    def test_only_recent_unfinished_retryable_work_runs(self):
        self.assertTrue(retry_due(self.snapshot(), 50, self.now))
        for changes in ({'status': 'complete'}, {'retryable': False},
                        {'market_id': 45}, {'version': 0},
                        {'retry_not_before': (self.now + timedelta(hours=2)).isoformat()},
                        {'window_start': (self.now - timedelta(hours=12)).isoformat()}):
            self.assertFalse(retry_due(self.snapshot(**changes), 50, self.now))
        self.assertFalse(usable(None, 50, self.now))

    def test_cooldown_accepts_seconds_and_http_date(self):
        self.assertEqual(retry_delay('7200',self.now),7200)
        self.assertEqual(retry_delay('Sun, 01 Nov 2026 06:00:00 GMT',self.now),7201)
        self.assertIsNone(retry_delay('not a date',self.now))

    def test_partial_collection_exits_nonzero(self):
        with patch('sys.argv',['us_collector.py','--market','50']), \
             patch('us_collector.collect_market',return_value=({'status':'partial'},[],{})), \
             patch('builtins.print'):
            with self.assertRaises(SystemExit) as result:main()
            self.assertEqual(result.exception.code,2)

    def test_manual_refresh_discards_nonretryable_partial_snapshot(self):
        class Client:
            count=0
            venue_cache={}
        saved=self.snapshot(status='partial',retryable=False)
        with tempfile.TemporaryDirectory() as directory, \
             patch('us_collector.utcnow',return_value=self.now), \
             patch('us_collector.PublicClient',return_value=Client()), \
             patch('us_collector.discover',return_value=([],{'directory_complete':True,'excluded_non_us_or_unknown':0})) as discover:
            summary,_,_=collect_market(self.market,directory,snapshot=saved)
            self.assertFalse(summary['resumed'])
            self.assertEqual(discover.call_count,1)

    def test_interrupted_task_resumes_without_refetching_completed_tasks(self):
        class Client:
            count=0
            venue_cache={}
        coverage={'directory_complete': True, 'excluded_non_us_or_unknown': 0}
        calls=[]
        def first_run(client, ids, day):
            calls.append(day)
            if len(calls)==2:
                raise RetryableError('temporary failure')
            yield []
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            store=CheckpointStore(50,root)
            with patch('us_collector.utcnow',return_value=self.now), \
                 patch('us_collector.PublicClient',return_value=Client()), \
                 patch('us_collector.discover',return_value=([{'id':1}],coverage)), \
                 patch('us_collector.schedule_pages',side_effect=first_run):
                with self.assertRaises(RetryableError):
                    collect_market(self.market,root,store=store)
            saved=store.load()
            self.assertEqual(list(saved['tasks']),['0'])
            resumed_calls=[]
            def resumed(client,ids,day):
                resumed_calls.append(day)
                yield []
            with patch('us_collector.utcnow',return_value=self.now), \
                 patch('us_collector.PublicClient',return_value=Client()), \
                 patch('us_collector.discover',side_effect=AssertionError('must reuse discovery')), \
                 patch('us_collector.schedule_pages',side_effect=resumed):
                summary,rows,snapshot=collect_market(self.market,root,store=store,snapshot=saved)
            self.assertEqual(summary['status'],'complete')
            self.assertTrue(summary['resumed'])
            self.assertNotIn(calls[0],resumed_calls)
            self.assertEqual(summary['completed_tasks'],summary['expected_tasks'])

    def test_failed_cursor_page_does_not_commit_a_partial_task(self):
        class Client:
            count=0
            venue_cache={}
        def pages(*args):
            yield []
            raise RetryableError('page two failed')
        with tempfile.TemporaryDirectory() as directory, \
             patch('us_collector.utcnow',return_value=self.now), \
             patch('us_collector.PublicClient',return_value=Client()), \
             patch('us_collector.discover',return_value=([{'id':1}],{'directory_complete':True,'excluded_non_us_or_unknown':0})), \
             patch('us_collector.schedule_pages',side_effect=pages):
            store=CheckpointStore(50,directory)
            with self.assertRaises(RetryableError):
                collect_market(self.market,directory,store=store)
            self.assertEqual(store.load()['tasks'],{})

    def test_access_denial_is_not_retried(self):
        class Response:status_code=403
        client=PublicClient(delay=0)
        with patch.object(client.session,'request',return_value=Response()) as request:
            with self.assertRaises(CollectionError):client.request('GET','/search/test')
            self.assertEqual(request.call_count,1)

    def test_partial_source_is_not_marked_complete(self):
        class Client:
            count=0
            venue_cache={}
        with tempfile.TemporaryDirectory() as directory, \
             patch('us_collector.utcnow',return_value=self.now), \
             patch('us_collector.PublicClient',return_value=Client()), \
             patch('us_collector.discover',return_value=([],{'directory_complete':False,'excluded_non_us_or_unknown':0})):
            summary,_,snapshot=collect_market(self.market,directory)
            self.assertEqual(summary['status'],'partial')
            self.assertFalse(snapshot['retryable'])

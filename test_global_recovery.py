import base64
import gzip
import json
import unittest
from unittest.mock import patch
from collector import CollectionError
from run_global import decode_checkpoint, encode_checkpoint, pack_profile, unpack_profile, PROFILE_CACHE_HOURS
from datetime import datetime, timedelta, timezone
from plan_classpass import retry_candidates


class RecoveryTests(unittest.TestCase):
    def test_profile_cache_losslessly_preserves_all_fields_and_observation(self):
        studio = {'studio_id': 123, 'profile_checked_at': '2026-11-10T10:00:00+00:00',
                  'raw_profile': {'id': 123, 'description': 'Detailed description ' * 2000,
                                  'new_source_field': {'false': False, 'zero': 0, 'empty': []}},
                  'raw_directory': {'id': 123}, 'raw_schedule': {'timezone': 'America/New_York'},
                  'details': {'name': 'Studio', 'address': {'city': 'New York'}}}
        packed = pack_profile(studio)
        self.assertLess(len(json.dumps(packed)), len(json.dumps(studio)) / 2)
        self.assertEqual(unpack_profile(packed), studio)
        self.assertEqual(packed['profile_checked_at'], studio['profile_checked_at'])
        self.assertEqual(packed['details']['address']['city'], 'New York')
        self.assertEqual(PROFILE_CACHE_HOURS, 72)

    def test_profile_cache_supports_legacy_json_and_rejects_corruption(self):
        legacy = {'studio_id': 1, 'raw_profile': {'id': 1}}
        self.assertEqual(unpack_profile(legacy), legacy)
        self.assertEqual(unpack_profile(pack_profile(legacy)), legacy)
        packed = pack_profile({**legacy, 'raw_profile': {'id': 1, 'description': 'abc' * 10000}})
        for change in ({'sha256': 'bad'}, {'studio_id': 2}, {'compressed_payload': 'bad!'},
                       {'profile_checked_at': 'invented'}, {'cache_format': 'unknown'}):
            with self.assertRaises(CollectionError):
                unpack_profile({**packed, **change})
        with patch('run_global.CHECKPOINT_RAW_LIMIT', 100):
            with self.assertRaises(CollectionError):
                unpack_profile(packed)

    def test_lossless_large_manifest_survives_the_old_eight_mib_boundary(self):
        state = {'market_id': 1, 'venues': {'1': {'description': 'a' * (9 * 1024 * 1024)}}}
        self.assertEqual(decode_checkpoint(encode_checkpoint(state)), state)

    def test_bounded_decompression_rejects_oversized_payload(self):
        value = base64.b64encode(gzip.compress(json.dumps({'data': 'x' * 4000}).encode())).decode()
        with patch('run_global.CHECKPOINT_RAW_LIMIT', 1024):
            with self.assertRaises(CollectionError):
                decode_checkpoint(value)

    def test_corrupt_checkpoint_is_never_treated_as_empty_success(self):
        with self.assertRaises(CollectionError):
            decode_checkpoint('corrupt!')

    def test_retry_excludes_complete_old_blocked_and_cooldown_work(self):
        now=datetime.now(timezone.utc)
        markets=[{'id':i} for i in range(1,7)]
        def job(i,**changes):
            return {'market_id':i,'stats':dict(status='partial',retryable=True,
                window_start=(now-timedelta(hours=2)).isoformat(),**changes)}
        jobs=[job(1),job(2),job(3),job(4),job(5)]
        jobs[1]['stats']['status']='complete'
        jobs[2]['stats']['window_start']=(now-timedelta(hours=13)).isoformat()
        jobs[3]['stats']['retryable']=False
        jobs[4]['stats']['retry_not_before']=(now+timedelta(hours=1)).isoformat()
        self.assertEqual(retry_candidates(markets,jobs,now),[{'id':1}])

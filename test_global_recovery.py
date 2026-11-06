import base64
import gzip
import json
import unittest
from unittest.mock import patch
from collector import CollectionError
from run_global import decode_checkpoint, encode_checkpoint
from datetime import datetime, timedelta, timezone
from plan_classpass import retry_candidates


class RecoveryTests(unittest.TestCase):
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

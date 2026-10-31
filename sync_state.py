"""Durable, bounded collection snapshots. Backend credentials never enter logs."""
import json
import os
import time
import base64
import gzip
import io
from datetime import datetime, timedelta, timezone
from pathlib import Path
from email.utils import parsedate_to_datetime

from curl_cffi import requests
from collector import CollectionError
from enrichment import compact_checkpoint

VERSION = 2
CHECKPOINT_LIMIT = 8*1024*1024


def pack_checkpoint(snapshot):
    data=json.dumps(compact_checkpoint(snapshot),ensure_ascii=False).encode('utf8')
    if len(data)>CHECKPOINT_LIMIT:raise CollectionError('Checkpoint exceeds stage budget; reduce batch scope')
    if len(data)<65536:return json.loads(data)
    header={k:snapshot[k] for k in ['version','market_id','window_start','window_end','status',
            'retryable','retry_not_before','saved_at','summary'] if k in snapshot}
    return {**header,'compression':'gzip+base64','uncompressed_bytes':len(data),
            'compressed_payload':base64.b64encode(gzip.compress(data,mtime=0)).decode('ascii')}


def unpack_checkpoint(value):
    if not value or 'compression' not in value:return value
    try:
        if value['compression']!='gzip+base64':raise ValueError()
        compressed=base64.b64decode(value['compressed_payload'],validate=True)
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
            raw=stream.read(CHECKPOINT_LIMIT+1)
        if len(raw)>CHECKPOINT_LIMIT or len(raw)!=value['uncompressed_bytes']:raise ValueError()
        decoded=json.loads(raw)
        for key in ['version','market_id','window_start','status']:
            if decoded.get(key)!=value.get(key):raise ValueError()
        return decoded
    except (ValueError,KeyError,TypeError,OSError,EOFError):
        raise CollectionError('Saved checkpoint failed integrity validation') from None


def retry_delay(value, now):
    if value.isdigit():
        return int(value)
    try:
        return max(0, int((parsedate_to_datetime(value)-now).total_seconds())+1)
    except (ValueError, TypeError, OverflowError):
        return None


class RetryableError(CollectionError):
    def __init__(self, message, cooldown=0):
        super().__init__(message)
        self.cooldown = cooldown


def rpc(name, payload):
    config = json.loads(Path(__file__).with_name('config.json').read_text())
    secret = os.environ.get('SUPABASE_SECRET_KEY')
    if not secret:
        raise CollectionError('SUPABASE_SECRET_KEY missing')
    headers = {'apikey': secret, 'Content-Type': 'application/json'}
    if secret.startswith('eyJ'):
        headers['Authorization'] = 'Bearer ' + secret
    for attempt in range(3):
        try:
            response = requests.post(config['supabase_url'].rstrip('/') + '/rest/v1/rpc/' + name,
                                     json=payload, headers=headers, timeout=20)
        except requests.RequestsError:
            if attempt == 2:
                raise RetryableError('Database transport failed; saved progress may be incomplete') from None
            time.sleep(2 * (attempt + 1))
            continue
        if response.status_code >= 500 and attempt < 2:
            time.sleep(2 * (attempt + 1))
            continue
        if response.status_code != 200:
            error=RetryableError if response.status_code>=500 or response.status_code==429 else CollectionError
            raise error(f'Database {name} HTTP {response.status_code}')
        return response.json()


def usable(snapshot, market_id, now):
    if not snapshot or snapshot.get('version') != VERSION or snapshot.get('market_id') != market_id:
        return False
    try:
        age = now - datetime.fromisoformat(snapshot['window_start'])
        return timedelta(0) <= age < timedelta(hours=12)
    except (KeyError, TypeError, ValueError):
        return False


def retry_due(snapshot, market_id, now):
    if not usable(snapshot, market_id, now) or snapshot.get('status') == 'complete':
        return False
    if snapshot.get('retryable') is False:
        return False
    not_before = snapshot.get('retry_not_before')
    return not not_before or now >= datetime.fromisoformat(not_before)


class CheckpointStore:
    def __init__(self, market_id, root, remote=False):
        self.market_id, self.root, self.remote = market_id, Path(root), remote

    def load(self):
        if self.remote:
            return unpack_checkpoint(rpc('classpass_checkpoint_read', {'p_market_id': self.market_id}))
        path = self.root / 'checkpoint.json'
        return unpack_checkpoint(json.loads(path.read_text(encoding='utf8'))) if path.exists() else None

    def save(self, snapshot):
        # One bounded snapshot per currently enabled market, overwritten in place.
        snapshot=pack_checkpoint(snapshot)
        encoded = json.dumps(snapshot)
        if len(encoded.encode('utf8')) > 8 * 1024 * 1024:
            raise CollectionError('Checkpoint exceeds stage budget; reduce batch scope')
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / 'checkpoint.json'
        temp = path.with_suffix('.tmp')
        temp.write_text(encoded, encoding='utf8')
        temp.replace(path)
        if self.remote:
            rpc('classpass_checkpoint_save', {'p_market_id': self.market_id, 'p_snapshot': snapshot})

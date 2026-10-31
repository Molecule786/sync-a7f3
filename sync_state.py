"""Durable, bounded collection snapshots. Backend credentials never enter logs."""
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from email.utils import parsedate_to_datetime

from curl_cffi import requests
from collector import CollectionError

VERSION = 2


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
            return rpc('classpass_checkpoint_read', {'p_market_id': self.market_id})
        path = self.root / 'checkpoint.json'
        return json.loads(path.read_text()) if path.exists() else None

    def save(self, snapshot):
        # One bounded snapshot per currently enabled market, overwritten in place.
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

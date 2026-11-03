"""GitHub end-to-end proof. Writes only through the existing isolated test RPC."""
import copy
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from curl_cffi import requests
from collector import CollectionError, collect, timestamp, write_batch

TARGET = 'classpass_events_test'

def read_saved(config, rows):
    secret = os.environ['SUPABASE_SECRET_KEY']
    headers = {'apikey': secret}
    if secret.startswith('eyJ'):
        headers['Authorization'] = 'Bearer ' + secret
    response = requests.get(
        config['supabase_url'].rstrip('/') + '/rest/v1/' + TARGET,
        headers=headers,
        params={'select': '*', 'event_id': 'in.(' + ','.join(r['event_id'] for r in rows) + ')'},
        timeout=30,
    )
    if response.status_code != 200:
        raise CollectionError(f'Test read-back failed (HTTP {response.status_code})')
    saved = response.json()
    if not isinstance(saved, list) or len(saved) != len(rows):
        raise CollectionError('Test read-back count mismatch')
    by_id = {r['event_id']: r for r in saved}
    if len(by_id) != len(rows):
        raise CollectionError('Duplicate test event IDs')
    for expected in rows:
        actual = by_id.get(expected['event_id'])
        if actual is None:
            raise CollectionError('Missing test event')
        for key, value in expected.items():
            equal = timestamp(actual[key]) == timestamp(value) if key in ('starts_at', 'ends_at') else actual.get(key) == value
            if not equal:
                raise CollectionError(f'Test read-back field mismatch: {key}')
    return by_id

def prove(config):
    rows = collect(config)
    if len(rows) != 10:
        raise CollectionError('This demonstration requires exactly ten upcoming source classes')
    Path('output').mkdir(exist_ok=True)
    Path('output/pipeline-test-source.json').write_text(json.dumps(rows, indent=2), encoding='utf-8')
    first = write_batch(config, rows, production=False)
    saved = read_saved(config, rows)
    second = write_batch(config, rows, production=False)
    repeated = read_saved(config, rows)
    if {k:v['id'] for k,v in saved.items()} != {k:v['id'] for k,v in repeated.items()}:
        raise CollectionError('Repeat merge changed stable database row IDs')
    # Deliberate test-only mutation proves update behavior even when the source is unchanged.
    probe = copy.deepcopy(rows[:1])
    probe[0]['title'] += ' [ISOLATED UPDATE TEST]'
    try:
        update = write_batch(config, probe, production=False)
        read_saved(config, probe)
    finally:
        write_batch(config, rows[:1], production=False)
    restored = read_saved(config, rows)
    if {k:v['id'] for k,v in saved.items()} != {k:v['id'] for k,v in restored.items()}:
        raise CollectionError('Update/restore changed database row IDs')
    return {'status': 'success', 'target': TARGET, 'verified_rows': len(rows),
            'first_merge': first, 'repeat_merge': second,
            'read_back_fields_verified': True, 'duplicate_free_repeat': True,
            'controlled_test_update_verified': True, 'source_values_restored': True,
            'controlled_update_merge': update, 'production_write_calls': 0,
            'event_ids': [r['event_id'] for r in rows],
            'github_run_id': os.environ.get('GITHUB_RUN_ID'),
            'scope': 'Two NYC studios, current public schedule, at most seven days; not US-wide.'}

def main():
    try:
        result = prove(json.loads(Path('config.json').read_text()))
    except Exception as exc:
        result = {'status': 'failed', 'target': TARGET,
                  'error': str(exc) if isinstance(exc, CollectionError) else type(exc).__name__}
    result['finished_at'] = datetime.now(timezone.utc).isoformat()
    Path('output').mkdir(exist_ok=True)
    Path('output/pipeline-test-summary.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as handle:
            handle.write('## Isolated ClassPass pipeline proof\n\n```json\n'+json.dumps(result, indent=2)+'\n```\n')
    if result['status'] != 'success':
        raise SystemExit(1)

if __name__ == '__main__':
    main()

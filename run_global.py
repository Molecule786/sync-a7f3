"""Worldwide runner with an explicit field-preserving production mode."""
import argparse
import base64
import gzip
import io
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from curl_cffi import requests
from collector import CollectionError
from global_collector import ISO2, collect, country_of
from us_collector import save
from precise_refresh import proposal

ROOT = Path(__file__).resolve().parent
CONFIG = json.loads((ROOT/'config.json').read_text())
CHECKPOINT_RAW_LIMIT = 64 * 1024 * 1024
CHECKPOINT_WIRE_LIMIT = 8 * 1024 * 1024


def decode_checkpoint(value):
    """The wire and decoded limits differ; old large manifests stay lossless."""
    if len(value) > CHECKPOINT_WIRE_LIMIT:
        raise CollectionError('Saved compressed checkpoint exceeds transfer budget')
    try:
        packed = base64.b64decode(value, validate=True)
        with gzip.GzipFile(fileobj=io.BytesIO(packed)) as stream:
            raw = stream.read(CHECKPOINT_RAW_LIMIT + 1)
        if len(raw) > CHECKPOINT_RAW_LIMIT:
            raise ValueError('Decoded checkpoint too large')
        state = json.loads(raw)
        if not isinstance(state, dict):
            raise ValueError('Invalid checkpoint')
        return state
    except (ValueError, OSError, EOFError, TypeError) as exc:
        raise CollectionError('Saved global checkpoint failed bounded validation') from exc


def encode_checkpoint(state):
    raw = json.dumps(state, ensure_ascii=False).encode('utf-8')
    if len(raw) > CHECKPOINT_RAW_LIMIT:
        raise CollectionError('Global checkpoint exceeds decoded storage budget')
    value = base64.b64encode(gzip.compress(raw, mtime=0)).decode('ascii')
    if len(value) > CHECKPOINT_WIRE_LIMIT:
        raise CollectionError('Global checkpoint exceeds compressed transfer budget')
    return value

def database(method, resource, payload=None, params=None):
    secret = os.environ.get('SUPABASE_SECRET_KEY')
    if not secret:
        raise CollectionError('Missing backend database secret')
    headers = {'apikey':secret, 'Content-Type':'application/json', 'Prefer':'resolution=merge-duplicates,return=representation'}
    if secret.startswith('eyJ'):
        headers['Authorization'] = 'Bearer '+secret
    # Fixed allowlist; no production resource or production RPC can be selected.
    if resource not in ('venues_global_test','events_global_test','directory_jobs_global_test',
                        'rpc/sync_directory_global_test','rpc/directory_global_test_health',
                        'rpc/refresh_classpass_precise','events','classpass_event_freshness'):
        raise CollectionError('Forbidden database target')
    if resource=='events' and method!='GET':
        raise CollectionError('Direct event writes are forbidden; use the compare-and-swap merge')
    if resource=='classpass_event_freshness' and method!='GET':
        raise CollectionError('Freshness writes require a verified source merge')
    response = requests.request(method,CONFIG['supabase_url']+'/rest/v1/'+resource,headers=headers,
                                json=payload,params=params,timeout=30)
    if not 200 <= response.status_code < 300:
        raise CollectionError(f'Isolated database request failed (HTTP {response.status_code}); inspect capacity/grants')
    return response.json() if response.content else None

def merge_precise(records):
    ids=[r['event']['event_id'] for r in records]
    current=database('GET','events',params={'source':'eq.classpass','event_id':'in.('+','.join(ids)+')','select':'*'}) if ids else []
    before={r['event_id']:r for r in current}
    items=[proposal(r['event'],before.get(r['event']['event_id']),r['observed_at']) for r in records]
    return database('POST','rpc/refresh_classpass_precise',{'p_items':items})

def run(market, markets, maximum, production=False):
    root = ROOT/'output/global'/str(market['id'])
    root.mkdir(parents=True,exist_ok=True)
    health = database('POST','rpc/directory_global_test_health',{})
    if not health.get('can_collect'):
        raise CollectionError('400 MiB database guard: collection stopped before source requests')
    source_key='classpass-production' if production else 'classpass'
    previous = database('GET','directory_jobs_global_test',params={'source':'eq.'+source_key,'market_id':'eq.'+str(market['id']),'select':'compressed_state,stats'})
    if previous and previous[0].get('compressed_state'):
        state = decode_checkpoint(previous[0]['compressed_state'])
        # Completed/exported tasks are durable in the isolated tables; unfinished writes refetch.
        state['tasks'] = {k:v for k,v in state.get('tasks',{}).items() if v.get('exported')}
        save(root/'checkpoint.json',state)
    def cached_profiles(ids):
        cutoff = (datetime.now(timezone.utc)-timedelta(hours=20)).isoformat()
        rows = database('GET','venues_global_test',params={'source':'eq.classpass',
                        'source_id':'in.('+','.join(str(i) for i in ids)+')',
                        'observed_at':'gte.'+cutoff,'select':'details'})
        return [r['details'] for r in rows]
    summary = collect(market,markets,root,maximum,profile_loader=cached_profiles)
    state = json.loads((root/'checkpoint.json').read_text(encoding='utf-8'))
    generation = root/state['generation']
    country = ISO2[market['country_code']]
    venues = []
    for path in sorted((generation/'venues').glob('*.json.gz')):
        studio = json.loads(gzip.decompress(path.read_bytes()))
        if country_of(studio.get('raw_profile') or {},markets)==country:
            venues.append(studio)
    inserted = updated = processed = unchanged = conflicts = 0
    audits=[]
    for i in range(0,len(venues),50):
        database('POST','rpc/sync_directory_global_test',{'p_market':market['id'],'p_country':country,
                 'p_venues':venues[i:i+50],'p_events':[]})
    for key,task in state['tasks'].items():
        if task.get('exported'):
            continue
        path = generation/'tasks'/f'{key}.json.gz'
        if not path.exists():
            continue
        rows = json.loads(gzip.decompress(path.read_bytes()))
        for i in range(0,len(rows),250):
            chunk=rows[i:i+250]
            result = merge_precise(chunk) if production else database('POST','rpc/sync_directory_global_test',{'p_market':market['id'],
                    'p_country':country,'p_venues':[],'p_events':chunk})
            if result.get('target')!=('events' if production else 'events_global_test'):
                raise CollectionError('Wrong database target')
            inserted += result['inserted']; updated += result['updated']; processed += len(chunk)
            unchanged+=result.get('unchanged',0);conflicts+=result.get('conflicts_skipped',0)
            audits.extend(result.get('audit',[]))
        task['exported'] = True
        save(root/'checkpoint.json',state)
    summary.pop('database_writes',None)
    summary.update(target='events' if production else 'events_global_test',processed_this_attempt=processed,inserted=inserted,updated=updated,
                   unchanged=unchanged,conflicts_skipped=conflicts,
                   venues_saved_this_attempt=len(venues),github_run_id=os.environ.get('GITHUB_RUN_ID'),
                   production_write_enabled=production)
    audit_path=root/'change-audit.json'
    previous_audit=json.loads(audit_path.read_text(encoding='utf-8')) if audit_path.exists() else []
    save(audit_path,previous_audit+audits)
    # Future run restores the compact job manifest; source task payloads are in database/artifacts.
    packed = encode_checkpoint(state)
    database('POST','directory_jobs_global_test',{'source':source_key,'market_id':market['id'],
             'country':country,'compressed_state':packed,'stats':summary,
             'updated_at':datetime.now(timezone.utc).isoformat()},params={'on_conflict':'source,market_id'})
    save(root/'summary.json',summary)
    return summary

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--market',type=int,required=True)
    parser.add_argument('--max-requests',type=int,default=150)
    parser.add_argument('--write-production',action='store_true')
    args = parser.parse_args()
    if not 1<=args.max_requests<=150:
        parser.error('Request budget must be 1 to 150')
    all_markets = json.loads((ROOT/'global_markets.json').read_text(encoding='utf-8'))
    market = next(m for m in all_markets if m['id']==args.market)
    try:
        result = run(market,{m['id']:m['country_code'] for m in all_markets},args.max_requests,args.write_production)
    except Exception as exc:
        result = {'status':'failed','market_id':args.market,'production_write_enabled':args.write_production,
                  'error':str(exc) if isinstance(exc,CollectionError) else type(exc).__name__}
        save(ROOT/'output/global'/str(args.market)/'summary.json',result)
    print(json.dumps(result))
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a',encoding='utf-8') as handle:
            handle.write('## ClassPass worldwide / '+market['name']+'\n\n```json\n'+json.dumps(result,indent=2)+'\n```\n')
    if result['status']!='complete':
        raise SystemExit(2)

if __name__=='__main__':
    main()


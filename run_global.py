"""Isolated worldwide runner; production events is never an allowed target."""
import argparse
import base64
import gzip
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from curl_cffi import requests
from collector import CollectionError
from global_collector import ISO2, collect, country_of
from us_collector import save

ROOT = Path(__file__).resolve().parent
CONFIG = json.loads((ROOT/'config.json').read_text())

def database(method, resource, payload=None, params=None):
    secret = os.environ.get('SUPABASE_SECRET_KEY')
    if not secret:
        raise CollectionError('Missing backend database secret')
    headers = {'apikey':secret, 'Content-Type':'application/json', 'Prefer':'resolution=merge-duplicates,return=representation'}
    if secret.startswith('eyJ'):
        headers['Authorization'] = 'Bearer '+secret
    # Fixed allowlist; no production resource or production RPC can be selected.
    if resource not in ('venues_global_test','events_global_test','directory_jobs_global_test',
                        'rpc/sync_directory_global_test','rpc/directory_global_test_health'):
        raise CollectionError('Forbidden database target')
    response = requests.request(method,CONFIG['supabase_url']+'/rest/v1/'+resource,headers=headers,
                                json=payload,params=params,timeout=30)
    if not 200 <= response.status_code < 300:
        raise CollectionError(f'Isolated database request failed (HTTP {response.status_code}); inspect capacity/grants')
    return response.json() if response.content else None

def run(market, markets, maximum):
    root = ROOT/'output/global'/str(market['id'])
    root.mkdir(parents=True,exist_ok=True)
    health = database('POST','rpc/directory_global_test_health',{})
    if not health.get('can_collect'):
        raise CollectionError('350 MiB database guard: collection stopped before source requests')
    previous = database('GET','directory_jobs_global_test',params={'source':'eq.classpass','market_id':'eq.'+str(market['id']),'select':'compressed_state'})
    if previous and previous[0].get('compressed_state'):
        raw = gzip.decompress(base64.b64decode(previous[0]['compressed_state']))
        if len(raw)>8*1024*1024:
            raise CollectionError('Saved global checkpoint exceeds budget')
        state = json.loads(raw)
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
    inserted = updated = processed = 0
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
            result = database('POST','rpc/sync_directory_global_test',{'p_market':market['id'],
                    'p_country':country,'p_venues':[],'p_events':rows[i:i+250]})
            if result.get('target')!='events_global_test':
                raise CollectionError('Wrong isolated database target')
            inserted += result['inserted']; updated += result['updated']; processed += result['processed']
        task['exported'] = True
        save(root/'checkpoint.json',state)
    summary.update(target='events_global_test',processed_this_attempt=processed,inserted=inserted,updated=updated,
                   venues_saved_this_attempt=len(venues),github_run_id=os.environ.get('GITHUB_RUN_ID'),
                   production_write_calls=0)
    # Future run restores the compact job manifest; source task payloads are in database/artifacts.
    packed = base64.b64encode(gzip.compress(json.dumps(state,ensure_ascii=False).encode('utf-8'),mtime=0)).decode('ascii')
    if len(packed)>8*1024*1024:
        raise CollectionError('Global checkpoint exceeds isolated storage budget')
    database('POST','directory_jobs_global_test',{'source':'classpass','market_id':market['id'],
             'country':country,'compressed_state':packed,'stats':summary,
             'updated_at':datetime.now(timezone.utc).isoformat()},params={'on_conflict':'source,market_id'})
    save(root/'summary.json',summary)
    return summary

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--market',type=int,required=True)
    parser.add_argument('--max-requests',type=int,default=150)
    args = parser.parse_args()
    if not 1<=args.max_requests<=150:
        parser.error('Request budget must be 1 to 150')
    all_markets = json.loads((ROOT/'global_markets.json').read_text(encoding='utf-8'))
    market = next(m for m in all_markets if m['id']==args.market)
    try:
        result = run(market,{m['id']:m['country_code'] for m in all_markets},args.max_requests)
    except Exception as exc:
        result = {'status':'failed','market_id':args.market,'production_write_calls':0,
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

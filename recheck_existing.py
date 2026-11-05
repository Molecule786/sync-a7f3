"""Frozen-ID manifest and source-by-source verification of existing live rows."""
import argparse,json,os,gzip
from datetime import datetime,timezone,timedelta
from pathlib import Path
from collector import CollectionError
from precise_refresh import proposal,session_row
from run_global import database,ROOT
from us_collector import PublicClient,save
from sync_state import RetryableError

def plan():
    rows=[];offset=0
    while True:
        page=database('GET','events',params={'select':'event_id,country','source':'eq.classpass',
                      'order':'event_id','limit':1000,'offset':offset})
        rows.extend(page)
        if len(page)<1000:break
        offset+=len(page)
    allowed={r['iso3'] for r in json.loads((ROOT/'global_scope.json').read_text())['countries']}
    from global_collector import ISO2
    recent=set();offset=0
    cutoff=(datetime.now(timezone.utc)-timedelta(hours=6)).isoformat()
    while True:
        page=database('GET','classpass_event_freshness',params={'select':'event_id','last_seen_at':'gte.'+cutoff,
                      'order':'event_id','limit':1000,'offset':offset})
        recent.update(r['event_id'] for r in page)
        if len(page)<1000:break
        offset+=len(page)
    scoped=[r['event_id'] for r in rows if r['country'] in {ISO2[c] for c in allowed}]
    ids=[identity for identity in scoped if identity not in recent]
    save(Path('output/recheck-manifest.json'),{'created_at':datetime.now(timezone.utc).isoformat(),'event_ids':ids,'size':100,
         'all_existing_records':len(scoped),'already_verified_within_six_hours':len(scoped)-len(ids)})
    shards=list(range((len(ids)+99)//100)) or [0]
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'],'a') as f:f.write('shards='+json.dumps(shards)+'\n')
    print(json.dumps({'existing_records':len(scoped),'remaining_to_verify':len(ids),
                      'already_verified_within_six_hours':len(scoped)-len(ids),'shards':len(shards)}))

def recheck(shard):
    manifest=json.loads(Path('output/recheck-manifest.json').read_text())
    ids=manifest['event_ids'][shard*100:(shard+1)*100]
    rows=database('GET','events',params={'select':'*','source':'eq.classpass','event_id':'in.('+','.join(ids)+')'}) if ids else []
    markets=json.loads((ROOT/'global_markets.json').read_text(encoding='utf-8'))
    country_map={m['id']:m['country_code'] for m in markets}
    client=PublicClient(max_requests=150)
    items=[];checks=[];snapshots=[]
    for before in rows:
        identity=before['event_id'].split(':')[1]
        try:
            source=client.json('GET','/_api/v1/schedules/'+identity)
            snapshots.append({'event_id':before['event_id'],'before':before,'source':source,
                              'verified_at':datetime.now(timezone.utc).isoformat()})
            row,status=session_row(source,country_map,existing_record=True)
            change=proposal(row,before,datetime.now(timezone.utc).isoformat(),source.get('id'))
            items.append(change)
            checks.append({'event_id':before['event_id'],'source_state':status,'proposed_fields':list(change['patch']),
                           'availability':source.get('availability'),'source_starts_at':row['starts_at'],
                           'source_verticals':source.get('verticals'),'source_activities':(source.get('class') or {}).get('activities'),
                           'source_ends_at':row['ends_at'],'verified_at':change['evidence']['verified_at']})
        except CollectionError as exc:
            checks.append({'event_id':before['event_id'],'source_state':'unverified_retained','error':str(exc)})
            if isinstance(exc,RetryableError) or '403' in str(exc) or '401' in str(exc):break
    root=Path('output')/('recheck-'+str(shard));root.mkdir(parents=True,exist_ok=True)
    (root/'source-sessions.json.gz').write_bytes(gzip.compress(json.dumps(snapshots,ensure_ascii=False).encode('utf-8')))
    save(root/'source-checks.json',checks)
    result=database('POST','rpc/refresh_classpass_precise',{'p_items':items})
    result.update(verified=len(items),selected=len(ids),confirmed_expired=sum(c['source_state']=='expired' for c in checks),
                  confirmed_upcoming=sum(c['source_state']=='upcoming' for c in checks),
                  unverified=len(ids)-len(items),shard=shard)
    save(root/'summary.json',result)
    print(json.dumps({k:v for k,v in result.items() if k!='audit'}))
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:
            f.write('## Existing ClassPass records / shard '+str(shard)+'\n\n```json\n'+json.dumps({k:v for k,v in result.items() if k!='audit'},indent=2)+'\n```\n')
    if result['unverified'] or result.get('conflicts_skipped'):raise SystemExit(2)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--plan',action='store_true');p.add_argument('--shard',type=int)
    a=p.parse_args();plan() if a.plan else recheck(a.shard)

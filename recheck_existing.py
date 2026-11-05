"""Frozen-ID manifest and source-by-source verification of existing live rows."""
import argparse,json,os,gzip
from datetime import datetime,timezone
from pathlib import Path
from collector import CollectionError
from precise_refresh import proposal,session_row
from run_global import database,ROOT
from us_collector import PublicClient,save

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
    ids=[r['event_id'] for r in rows if r['country'] in {ISO2[c] for c in allowed}]
    save(Path('output/recheck-manifest.json'),{'created_at':datetime.now(timezone.utc).isoformat(),'event_ids':ids,'size':100})
    shards=list(range((len(ids)+99)//100))
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'],'a') as f:f.write('shards='+json.dumps(shards)+'\n')
    print(json.dumps({'existing_records':len(ids),'shards':len(shards)}))

def recheck(shard):
    manifest=json.loads(Path('output/recheck-manifest.json').read_text())
    ids=manifest['event_ids'][shard*100:(shard+1)*100]
    rows=database('GET','events',params={'select':'*','source':'eq.classpass','event_id':'in.('+','.join(ids)+')'})
    markets=json.loads((ROOT/'global_markets.json').read_text(encoding='utf-8'))
    country_map={m['id']:m['country_code'] for m in markets}
    client=PublicClient(max_requests=150)
    items=[];checks=[];snapshots=[]
    for before in rows:
        identity=before['event_id'].split(':')[1]
        try:
            source=client.json('GET','/_api/v1/schedules/'+identity)
            row,status=session_row(source,country_map)
            change=proposal(row,before,datetime.now(timezone.utc).isoformat(),source.get('id'))
            items.append(change)
            snapshots.append({'event_id':before['event_id'],'before':before,'source':source,
                              'verified_at':change['evidence']['verified_at']})
            checks.append({'event_id':before['event_id'],'source_state':status,'proposed_fields':list(change['patch']),
                           'availability':source.get('availability'),'source_starts_at':row['starts_at'],
                           'source_ends_at':row['ends_at'],'verified_at':change['evidence']['verified_at']})
        except CollectionError as exc:
            checks.append({'event_id':before['event_id'],'source_state':'unverified_retained','error':str(exc)})
            if '403' in str(exc) or '401' in str(exc):break
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

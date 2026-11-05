"""Read-only all-country reconciliation; never equates an access gap with zero."""
import json,os
from collections import Counter
from datetime import datetime,timedelta,timezone
from pathlib import Path
from run_global import ROOT,database
from global_collector import ISO2

def pages(resource,params):
    result=[];offset=0
    while True:
        rows=database('GET',resource,params=dict(params,limit=1000,offset=offset))
        result.extend(rows)
        if len(rows)<1000:return result
        offset+=len(rows)

def report():
    manifest=json.loads(Path('output/recheck-manifest.json').read_text())
    cutoff=(datetime.fromisoformat(manifest['created_at'])-timedelta(hours=6)).isoformat()
    events=pages('events',{'source':'eq.classpass','select':'event_id,country,ends_at','order':'event_id'})
    seen={r['event_id']:r['last_seen_at'] for r in pages('classpass_event_freshness',
          {'select':'event_id,last_seen_at','last_seen_at':'gte.'+cutoff,'order':'event_id'})}
    confirmed=Counter();expired=Counter()
    for row in events:
        if row['event_id'] in seen:
            confirmed[row['country']]+=1
            if row['ends_at'] and datetime.fromisoformat(row['ends_at'])<datetime.fromisoformat(seen[row['event_id']]):expired[row['country']]+=1
    jobs=database('GET','directory_jobs_global_test',params={'source':'eq.classpass-production','select':'market_id,country,stats,updated_at'})
    jobs={r['market_id']:r for r in jobs if r['updated_at']>=manifest['created_at']}
    markets=json.loads((ROOT/'global_markets.json').read_text(encoding='utf-8-sig'))
    coverage=json.loads((ROOT/'country_coverage_refresh.json').read_text())
    rows=[]
    for country in coverage['countries']:
        code=ISO2[country['iso3']];routes=[m['id'] for m in markets if m['country_code']==country['iso3']]
        attempted=[jobs[i] for i in routes if i in jobs]
        complete=sum(j['stats'].get('status')=='complete' for j in attempted)
        status=('source_or_directory_gap' if not routes else
                'published_routes_checked_national_coverage_unproven' if complete==len(routes) else 'incomplete')
        rows.append({'country':country['country'],'iso2':code,'source_verified_events':confirmed[code],
                     'source_confirmed_expired':expired[code],'routes':len(routes),'attempted':len(attempted),
                     'completed_routes':complete,'status':status,'discovery_evidence':country})
    result={'generated_at':datetime.now(timezone.utc).isoformat(),'target':'events','countries':rows,
            'country_count':len(rows),'existing_manifest':manifest,
            'limitation':'Route completion does not prove national completeness; gaps and access failures are unresolved, never confirmed zero.'}
    assert len(rows)==40
    Path('output/country-refresh-report.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    lines=['## ClassPass top-40 country reconciliation','',result['limitation'],'',
           '| Country | Source-verified events | Confirmed expired | Routes completed / planned | Coverage |',
           '|---|---:|---:|---:|---|']
    for r in rows:lines.append(f"| {r['country']} | {r['source_verified_events']} | {r['source_confirmed_expired']} | {r['completed_routes']} / {r['routes']} | {r['status']} |")
    text='\n'.join(lines)+'\n';Path('output/country-refresh-report.md').write_text(text,encoding='utf-8')
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a',encoding='utf-8') as f:f.write(text)
    print(text)

if __name__=='__main__':report()

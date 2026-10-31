"""Public US fitness discovery and seven-day schedules, with explicit coverage."""
import argparse
import json
import os
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from curl_cffi import requests

from collector import CollectionError

ORIGIN = 'https://classpass.com'
STATES = set('AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC PR VI GU AS MP'.split())
MARKETS = json.loads(Path(__file__).with_name('us_markets.json').read_text())
US_MSAS = {m['id'] for m in MARKETS}


def utcnow():
    return datetime.now(timezone.utc)


def slug(text):
    value = unicodedata.normalize('NFKD',text).encode('ascii','ignore').decode().lower()
    return re.sub('[^a-z0-9]+','-',value).strip('-')


def save(path, data):
    path.parent.mkdir(exist_ok=True,parents=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data,indent=2),encoding='utf8')
    tmp.replace(path)


class PublicClient:
    def __init__(self, delay=0.8, max_requests=5000):
        self.session = requests.Session()
        self.delay, self.max_requests = delay,max_requests
        self.count, self.last = 0,0
        self.venue_cache = {}

    def request(self, method, path, **kwargs):
        url = path if path.startswith('https://') else ORIGIN+path
        if urlparse(url).hostname != 'classpass.com':
            raise CollectionError('Unexpected source host')
        for attempt in range(3):
            if self.count >= self.max_requests:
                raise CollectionError('Request budget reached; coverage incomplete')
            time.sleep(max(0,self.delay-(time.monotonic()-self.last)))
            self.count += 1
            r=self.session.request(method,url,headers={'platform':'web'},impersonate='chrome',timeout=45,**kwargs)
            self.last=time.monotonic()
            if r.status_code in (401,403):
                raise CollectionError(f'Source denied access (HTTP {r.status_code}); no retry')
            if r.status_code==429 or r.status_code>=500:
                if attempt==2:break
                retry=r.headers.get('Retry-After','')
                if retry.isdigit() and int(retry)>60:
                    raise CollectionError('Source requested long cooldown; coverage incomplete')
                time.sleep(int(retry) if retry.isdigit() else 5*(attempt+1))
                continue
            break
        if r.status_code != 200:
            raise CollectionError(f'Source HTTP {r.status_code}')
        return r

    def json(self, method, path, **kwargs):
        try: return self.request(method,path,**kwargs).json()
        except ValueError as exc:raise CollectionError('Source did not return valid JSON') from exc


def first_directory(html):
    node=BeautifulSoup(html,'html.parser').find('script',id='store')
    if node is None:raise CollectionError('Search directory missing; not a verified empty result')
    try:
        store=json.loads(node.text)
        modules=store['entities']['searchLayoutByName']['data']['web_search_page_1']
        return directory_module(modules)
    except (KeyError,TypeError,ValueError) as exc:
        raise CollectionError('Search source format changed') from exc


def directory_module(modules):
    found=[v['data'] for v in modules.values() if v.get('module_type')=='SCHEDULE_RESULT']
    if len(found)!=1 or found[0].get('module_error'):
        raise CollectionError('Invalid search results module')
    x=found[0]
    if not isinstance(x.get('venue_tab_items'),list) or not isinstance(x.get('total_hits'),int):
        raise CollectionError('Search counts/list missing')
    return x


def merge_venue_metadata(venue, detail):
    """Recover missing address fields only from published structured geography."""
    if detail.get('id') != venue.get('id'):
        raise CollectionError('Venue detail identity mismatch')
    out=dict(venue)
    out['alias']=venue.get('alias') or detail.get('alias')
    out['name']=venue.get('name') or detail.get('name')
    address=dict(detail.get('address') or {})
    address.update({k:v for k,v in (venue.get('address') or {}).items() if v})
    location=dict(detail.get('location') or {})
    location.update({k:v for k,v in (venue.get('location') or {}).items() if v is not None})
    geo=detail.get('global_location') or {}
    country=((geo.get('msa') or {}).get('country') or {}).get('short_code') or location.get('country_short_code')
    if country:location['country_short_code']=country
    area=(geo.get('areas') or {}).get('city')
    if country=='US' and isinstance(area,str):
        parts=[p.strip() for p in area.rsplit(',',2)]
        if len(parts)==3 and parts[1] in STATES and parts[2] in ('USA','United States'):
            if not address.get('state') or address['state']==parts[1]:
                address['state']=address.get('state') or parts[1]
                address['city']=address.get('city') or parts[0]
    out['address']=address;out['location']=location
    return out


def enrich_venue(client,venue):
    address=venue.get('address') or {}
    if address.get('city') and address.get('state') and venue.get('alias'):
        return venue
    identity=venue['id']
    if identity not in client.venue_cache:
        client.venue_cache[identity]=client.json('GET',f'/_api/v2/venues/{identity}')
    return merge_venue_metadata(venue,client.venue_cache[identity])


def discover(client, market, root, sample_pages=0):
    result=client.json('GET','/_api/unisearch/v1/search_url',params={'tag_id':'activity','msa_id':market['id']})
    path=result.get('url','')
    if not path.startswith('/search/') or '?' in path:
        raise CollectionError('No verified fitness directory for market')
    page=first_directory(client.request('GET',path).text)
    expected=page['total_hits']
    seen,cursors={},set()
    count=0
    capped=expected>=10000
    while True:
        count+=1
        for venue in page['venue_tab_items']:
            venue = dict(venue)
            venue['id'] = venue.get('id',venue.get('venue_id'))
            if not isinstance(venue.get('id'),int) or not venue.get('alias'):
                save(root/'invalid_directory_item.json',venue)
                raise CollectionError('Directory venue lacks identity')
            seen[venue['id']]=venue
        save(root/'discovery.json',{'market':market,'source':ORIGIN+path,'expected':expected,'discovered':len(seen),'pages':count,'source_count_capped':capped,'venues':list(seen.values())})
        if len(seen)>=expected:break
        if sample_pages and count>=sample_pages:break
        cursor=page.get('cursor')
        if not page['venue_tab_items'] or not cursor or cursor in cursors:
            raise CollectionError(f'Directory did not reconcile ({len(seen)}/{expected})')
        cursors.add(cursor)
        payload={'search_request':{'cursor':cursor},'modules':['web_search_results_01']}
        response=client.json('POST','/_api/unisearch/v1/layout/web_search_page',json=payload)
        try:page=directory_module(response['data']['modules'])
        except (KeyError,TypeError) as exc:raise CollectionError('Directory pagination failed') from exc
        if page['total_hits']!=expected:
            raise CollectionError('Directory count changed during enumeration; retry run')
    # US-facing searches can return Canadian studios. Verify geography per record.
    resolved=[enrich_venue(client,v) for v in seen.values()]
    venues=[v for v in resolved if str((v.get('address') or {}).get('state') or '').upper() in STATES
            and (v.get('location') or {}).get('country_short_code','US')=='US']
    return venues,{'directory_expected':expected,'directory_discovered':len(seen),'us_venues':len(venues),'excluded_non_us_or_unknown':len(seen)-len(venues),'directory_pages':count,'directory_complete':len(seen)==expected and not capped,'source_count_capped':capped}


def parse_session(item, start, end):
    venue=item.get('venue') or {}
    address=venue.get('address') or {}
    country=(venue.get('location') or {}).get('country_short_code')
    region=str(address.get('state') or '').upper()
    if country and country!='US':return None,'non_us'
    if region not in STATES:return None,'unknown_geography'
    msa=(venue.get('location') or {}).get('msa_id')
    if not country and msa not in US_MSAS:return None,'unknown_geography'
    cls=item.get('class') or {}
    if 'activity' not in (item.get('verticals') or []) or cls.get('entity_type')!='CLASS':
        return None,'not_fitness_class'
    if not isinstance(item.get('id'),int) or not isinstance(venue.get('id'),int):
        raise CollectionError('Invalid source session identity')
    try:
        begins=datetime.fromtimestamp(item['starttime'],timezone.utc)
        finishes=datetime.fromtimestamp(item['endtime'],timezone.utc)
    except (KeyError,TypeError,ValueError,OverflowError) as exc:raise CollectionError('Invalid session time') from exc
    if finishes<=begins:raise CollectionError('Session end precedes start')
    if not max(start,utcnow())<=begins<end:return None,'outside_window'
    title=str(cls.get('name') or '').strip()
    city=str(address.get('city') or '').strip()
    if not title or not city or not venue.get('alias') or not cls.get('alias'):
        return None,'missing_required_fields'
    row={'event_id':f"classpass:{item['id']}",'source':'classpass','title':title,
         'starts_at':begins.isoformat(),'ends_at':finishes.isoformat(),
         'city':city,'city_slug':slug(city),'region':region,'country':'US','category':'fitness',
         'url':f"{ORIGIN}/classes/{venue['alias']}/{cls['alias']}/{item['id']}",
         'venue':venue.get('name'),'organizer':venue.get('name'),
         'description':cls.get('description'),'street':address.get('address_line1'),
         'zip':address.get('zip_code')}
    location=venue.get('location') or {}
    for key in ['lat','lon']:
        if isinstance(location.get(key),(int,float)):row[key]=location[key]
    return {k:v for k,v in row.items() if v is not None and v!=''},None


def schedule_pages(client, ids, day):
    payload={'date':day,'venue':ids,'report_ineligible_classes':True,'exclude_past_booking':True}
    response=client.json('POST','/_api/v3/search/schedules',json=payload)
    cursors=set()
    while True:
        rows=response.get('schedules')
        if not isinstance(rows,list):raise CollectionError('Schedule list missing; cannot count as empty')
        if any((r.get('venue') or {}).get('id') not in ids for r in rows):
            raise CollectionError('Source returned an unrequested venue')
        yield rows
        cursor=response.get('cursor')
        if not cursor:break
        if cursor in cursors:raise CollectionError('Repeated schedule cursor')
        cursors.add(cursor)
        response=client.json('GET','/_api/v3/search/schedules',params={'cursor':cursor})


def collect_market(market, root, sample=False, max_requests=5000):
    root=Path(root);root.mkdir(exist_ok=True,parents=True)
    started=utcnow();cutoff=started+timedelta(days=7)
    client=PublicClient(max_requests=max_requests)
    summary={'market_id':market['id'],'market':market['name'],'status':'running','window_start':started.isoformat(),'window_end':cutoff.isoformat(),'coverage':'US fitness directory search footprint; seven-day rolling schedule window','pages':0,'verified_rows':0,'excluded':{}}
    all_rows={}
    try:
        venues,coverage=discover(client,market,root,sample_pages=2 if sample else 0)
        summary.update(coverage)
        tz=ZoneInfo(market['timezone'])
        day=started.astimezone(tz).date();last=(cutoff-timedelta(microseconds=1)).astimezone(tz).date()
        dates=[]
        while day<=last:dates.append(day.isoformat());day+=timedelta(days=1)
        tasks=[(date,venues[i:i+50]) for date in dates for i in range(0,len(venues),50)]
        summary['expected_tasks']=len(tasks);summary['completed_tasks']=0
        for index,(date,chunk) in enumerate(tasks):
            if sample and index>=2:break
            for items in schedule_pages(client,[v['id'] for v in chunk],date):
                summary['pages']+=1
                for item in items:
                    item=dict(item)
                    item['venue']=enrich_venue(client,item.get('venue') or {})
                    row,reason=parse_session(item,started,cutoff)
                    if row:all_rows[row['event_id']]=row
                    else:summary['excluded'][reason]=summary['excluded'].get(reason,0)+1
                save(root/'events.json',list(all_rows.values()))
            summary['completed_tasks']+=1
            summary['verified_rows']=len(all_rows)
            save(root/'summary.json',summary)
            print(json.dumps({'market_id':market['id'],'task':index+1,'tasks':len(tasks),'rows':len(all_rows),'requests':client.count}),flush=True)
        complete=(summary['directory_complete'] and summary['completed_tasks']==len(tasks)
                  and not summary['excluded_non_us_or_unknown']
                  and not summary['excluded'].get('unknown_geography')
                  and not summary['excluded'].get('missing_required_fields'))
        if not all_rows and (summary['excluded'].get('unknown_geography') or summary['excluded'].get('missing_required_fields')):
            raise CollectionError('No sessions could be verified; unresolved source fields')
        summary.update(status='complete' if complete else 'partial',verified_rows=len(all_rows),requests=client.count,
                       venue_details_consulted=len(client.venue_cache),finished_at=utcnow().isoformat())
    except Exception as exc:
        summary.update(status='failed',error=str(exc) if isinstance(exc,CollectionError) else type(exc).__name__,verified_rows=len(all_rows),requests=client.count,finished_at=utcnow().isoformat())
        save(root/'summary.json',summary)
        raise
    save(root/'summary.json',summary)
    return summary,list(all_rows.values())


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--market',required=True,type=int)
    parser.add_argument('--sample',action='store_true')
    parser.add_argument('--max-requests',type=int,default=5000)
    parser.add_argument('--write-production',action='store_true')
    args=parser.parse_args()
    market=next(m for m in MARKETS if m['id']==args.market)
    root=Path('output')/f"market-{args.market}"
    if args.sample and args.write_production:
        parser.error('Sample collection cannot write production')
    summary,rows=collect_market(market,root,args.sample,args.max_requests)
    if args.write_production:
        summary['collection_status']=summary['status']
        try:
            result=write_us(market,summary,rows)
            summary['database']=result
        except Exception as exc:
            summary['status']='failed'
            summary['database']={'status':'failed','error':str(exc) if isinstance(exc,CollectionError) else type(exc).__name__}
            save(root/'summary.json',summary)
            raise
        save(root/'summary.json',summary)
    print(json.dumps(summary))


def write_us(market,summary,rows):
    secret=os.environ.get('SUPABASE_SECRET_KEY')
    if not secret:raise CollectionError('SUPABASE_SECRET_KEY missing')
    config=json.loads(Path(__file__).with_name('config.json').read_text())
    headers={'apikey':secret,'Content-Type':'application/json'}
    if secret.startswith('eyJ'):headers['Authorization']='Bearer '+secret
    run=os.environ.get('GITHUB_RUN_ID') or utcnow().strftime('%Y%m%d%H%M%S')
    attempt=os.environ.get('GITHUB_RUN_ATTEMPT','1')
    key=f'{run}:{attempt}:{market["id"]}'
    # Re-check dates after potentially long discovery. Never upload stale starts.
    rows=[r for r in rows if datetime.fromisoformat(r['starts_at'])>=utcnow()]
    chunks=[rows[i:i+250] for i in range(0,len(rows),250)] or [[]]
    result={'inserted':0,'updated':0,'processed':0,'run_key':key}
    for index,chunk in enumerate(chunks):
        payload={'p_rows':chunk,'p_market_id':market['id'],'p_run_key':key,'p_batch':index,'p_final':index==len(chunks)-1,'p_stats':summary}
        for attempt in range(3):
            try:
                response=requests.post(config['supabase_url'].rstrip('/')+'/rest/v1/rpc/sync_classpass_us',json=payload,headers=headers,timeout=60)
            except requests.RequestsError:
                if attempt==2:raise CollectionError('Database request failed; partial writes may exist')
                time.sleep(2*(attempt+1));continue
            if response.status_code>=500 and attempt<2:time.sleep(2*(attempt+1));continue
            break
        if response.status_code!=200:raise CollectionError(f'US database merge HTTP {response.status_code}; partial writes may exist')
        out=response.json()
        if out.get('target')!='events' or out.get('processed')!=len(chunk):raise CollectionError('Database batch confirmation mismatch')
        for field in ['inserted','updated','processed']:result[field]+=out[field]
    return result


if __name__=='__main__':main()

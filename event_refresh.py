"""Refresh existing ACTIVE/OpenSports events only. No inserts or venue writes."""
import argparse,html,json,os,re,time
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlparse,urljoin,parse_qs
from bs4 import BeautifulSoup
from curl_cffi import requests
from event_db import db

HOSTS={'active':{'active.com','www.active.com','activekids.com','www.activekids.com'},
       'opensports':{'opensports.net','www.opensports.net'}}
FIELDS={'title','description','organizer','organizer_url','starts_at','ends_at','venue','street','zip','city','region','lat','lon'}

class SourceError(RuntimeError):pass

def timestamp(s):
 d=datetime.fromisoformat(s.replace('Z','+00:00'))
 if d.tzinfo is None:raise SourceError('Source timestamp has no timezone')
 return d.astimezone(timezone.utc)

def utc(s):return timestamp(s).isoformat()
def text(s):return BeautifulSoup(str(s or ''),'html.parser').get_text(' ',strip=True)
def norm(s):return re.sub(r'\s+',' ',html.unescape(str(s))).strip()

def source_get(url,source):
 # Explicitly follow and validate redirects, including each destination.
 for redirects in range(5):
  p=urlparse(url)
  if p.hostname not in HOSTS[source] or p.scheme not in ('http','https') or p.username or p.port not in (None,80,443):
   raise SourceError('Unexpected source URL/redirect')
  url=p._replace(scheme='https').geturl()
  for attempt in range(3):
   try:r=requests.get(url,impersonate='chrome',allow_redirects=False,timeout=30)
   except requests.RequestsError:
    if attempt<2:time.sleep(2**attempt);continue
    raise SourceError('Source transport failure') from None
   if r.status_code in (429,500,502,503,504) and attempt<2:
    delay=r.headers.get('Retry-After','')
    if delay.isdigit() and int(delay)>30:raise SourceError('Source cooldown exceeds this attempt')
    time.sleep(max(2**attempt,int(delay) if delay.isdigit() else 0));continue
   break
  if r.status_code in (301,302,303,307,308):url=urljoin(url,r.headers.get('Location',''));continue
  if r.status_code!=200:raise SourceError(f'Source HTTP {r.status_code}')
  if len(r.content)>8*1024*1024:raise SourceError('Source page exceeds budget')
  return r.text
 raise SourceError('Too many source redirects')

def validate_dates(row):
 if not row.get('title') or not row.get('starts_at') or not row.get('ends_at'):raise SourceError('Source lacks title or timezone-aware dates')
 if timestamp(row['ends_at'])<=timestamp(row['starts_at']):raise SourceError('Invalid source date range')
 return row

def parse_opensports(page,before):
 soup=BeautifulSoup(page,'html.parser');tag=soup.find('script',id='__NEXT_DATA__')
 if not tag:raise SourceError('OpenSports public event data missing')
 data=json.loads(tag.get_text());posts=data.get('props',{}).get('initialState',{}).get('postDetail',{}).get('list',[])
 identity=before['event_id'].split(':',1)[1]
 matches=[p for p in posts if str(p.get('id'))==identity]
 if len(matches)!=1:raise SourceError('OpenSports event identity mismatch')
 p=matches[0]
 if p.get('isPublic') is not True or p.get('aliasID')!=urlparse(before['url']).path.rstrip('/').split('/')[-1]:
  raise SourceError('Unverified public event identity')
 place=p.get('place') or {};coords=place.get('latlng') or {}
 row={'title':p.get('title'),'description':text(p.get('description')),
      'starts_at':utc(p['start']),'ends_at':utc(p['end']),
      'organizer':p.get('groupName'),'venue':place.get('title')}
 # The published address is a full address, not a verified street-only value.
 if not before.get('street') and place.get('address'):row['street']=place['address']
 for target,src in [('lat','latitude'),('lon','longitude')]:
  if not before.get(target) and isinstance(coords.get(src),(int,float)):row[target]=coords[src]
 return validate_dates(row)

def schema_events(soup):
 def walk(x):
  if isinstance(x,list):
   for v in x:yield from walk(v)
  elif isinstance(x,dict):
   types=x.get('@type',[]);types=[types] if isinstance(types,str) else types
   if 'Event' in types or any(str(t).endswith('Event') for t in types):yield x
   if '@graph' in x:yield from walk(x['@graph'])
 for script in soup.select('script[type="application/ld+json"]'):
  try:yield from walk(json.loads(script.get_text()))
  except (ValueError,TypeError):continue

def parse_active(page,before,source_url=None):
 soup=BeautifulSoup(page,'html.parser');expected=before['event_id'].split(':',1)[1];landing=None;asset=None
 for tag in soup.find_all('script'):
  if tag.get('data-component-name')=='EventLanding':landing=json.loads(tag.get_text());break
 if landing:
  if str(landing.get('assetId') or landing.get('eventId'))!=expected:raise SourceError('ACTIVE identity mismatch')
 else:
  m=re.search(r'window\[[\"\x27]asset[\"\x27]\]\s*=\s*',page)
  if m:asset,_=json.JSONDecoder().raw_decode(page[m.end():].lstrip())
  if not asset or str(asset.get('assetGuid'))!=expected:raise SourceError('ACTIVE main asset identity missing/mismatched')
 path=urlparse(source_url or before['url']).path.rstrip('/')
 schemas=[x for x in schema_events(soup) if urlparse(x.get('url','')).path.rstrip('/')==path]
 if len(schemas)>1:raise SourceError('Ambiguous ACTIVE event schemas')
 node=schemas[0] if schemas else {};details=(landing or {}).get('eventDetails') or {}
 row={'title':details.get('eventTitle') or (asset or {}).get('assetName'),
      'description':text(((landing or {}).get('aboutSection') or {}).get('aboutEvent') or node.get('description'))}
 calendar=(landing or {}).get('addToCalender') or {}
 dates=parse_qs(urlparse(calendar.get('googleCalendar','')).query).get('dates',[])
 if dates and len(dates[0].split('/'))==2:
  start,end=dates[0].split('/')
  row.update(starts_at=utc(start),ends_at=utc(end))
 elif node.get('startDate') and node.get('endDate'):
  row.update(starts_at=utc(node['startDate']),ends_at=utc(node['endDate']))
 # Never interpret timezone-naive asset dates as UTC.
 location=details.get('eventLocation') or {};place=node.get('location') or {};address=place.get('address') or {}
 if not isinstance(address,dict):address={}
 for target,src in [('street','addressLine'),('zip','zipCode'),('city','city'),('region','state'),('venue','placeName')]:
  if location.get(src):row[target]=location[src]
 for target,src in [('street','streetAddress'),('zip','postalCode'),('city','addressLocality'),('region','addressRegion')]:
  if target not in row and address.get(src):row[target]=address[src]
 if not row.get('venue') and place.get('name'):row['venue']=place['name']
 if landing:
  row['organizer']=landing.get('organizerName')
  if landing.get('organizerUrl'):row['organizer_url']=urljoin(source_url or before['url'],landing['organizerUrl'])
 return validate_dates(row)

def proposal(row,before):
 patch={}
 for k,v in row.items():
  if k not in FIELDS or v is None or (isinstance(v,str) and not v.strip()):continue
  old=before.get(k)
  # Venue/profile refresh is a later phase. Keep populated location fields
  # together rather than changing a city while retaining its old coordinates.
  if k in ('venue','street','zip','city','region','lat','lon') and old not in (None,''):continue
  if k in ('starts_at','ends_at'):
   if old and timestamp(old)==timestamp(v):continue
  elif old is not None and norm(old)==norm(v):continue
  if k in ('lat','lon') and old is not None:continue
  if k=='street' and old:
   a=re.sub(r'\W','',norm(old)).casefold();b=re.sub(r'\W','',norm(v)).casefold()
   if a.startswith(b) or b.startswith(a):continue
  patch[k]=v
 return patch

def plan(source,sample):
 rows=[];after=None
 while True:
  page=db('rpc/existing_event_refresh_candidates','POST',{'p_source':source,'p_after':after or '', 'p_limit':500});rows.extend(page)
  if not page or len(page)<500:break
  after=page[-1]['event_id']
 if sample:rows=rows[:sample]
 manifest={'source':source,'created_at':datetime.now(timezone.utc).isoformat(),'ids':[r['event_id'] for r in rows],
           'countries':dict(Counter(r.get('country') or 'unknown' for r in rows))}
 out=Path('output');out.mkdir(exist_ok=True);(out/f'{source}-manifest.json').write_text(json.dumps(manifest))
 shards=list(range((len(rows)+99)//100)) or [0]
 with open(os.environ['GITHUB_OUTPUT'],'a') as f:f.write('shards='+json.dumps(shards)+'\n')
 print(json.dumps({'source':source,'existing_events':len(rows),'countries':manifest['countries'],'shards':len(shards),'sample':sample}))

def refresh(source,shard,apply):
 manifest=json.loads(Path(f'output/{source}-manifest.json').read_text());ids=manifest['ids'][shard*100:(shard+1)*100]
 counts=Counter();countries=defaultdict(Counter);errors=Counter();started=time.monotonic()
 locators=json.loads(Path('active_existing_urls.json').read_text(encoding='utf8')) if source=='active' else {}
 for i,eid in enumerate(ids):
  if time.monotonic()-started>480:
   counts['unprocessed']+=len(ids)-i;errors['Attempt time budget reached']+=1;break
  rows=db('events',params={'event_id':'eq.'+eid,'source':'eq.'+source,'select':'*','limit':1})
  if not rows:counts['missing']+=1;continue
  before=rows[0];country=before.get('country') or 'unknown'
  try:
   source_url=locators.get(eid,before['url'])
   page=source_get(source_url,source)
   row=parse_active(page,before,source_url) if source=='active' else parse_opensports(page,before)
   patch=proposal(row,before)
   if apply:
    result=db('rpc/refresh_existing_directory_event','POST',{'p_source':source,'p_event_id':eid,'p_expected':before,
     'p_patch':patch,'p_evidence':{'source':source,'event_id':eid,'original_url':before['url'],'fetched_url':source_url,
                                'verified_at':datetime.now(timezone.utc).isoformat()}})
    action=result['action']
   else:action='would_update' if patch else 'unchanged'
   counts[action]+=1;countries[country][action]+=1
   if timestamp(row['ends_at'])<datetime.now(timezone.utc):counts['source_confirmed_ended']+=1
  except (SourceError,ValueError,KeyError,TypeError) as exc:
   counts['unverified']+=1;countries[country]['unverified']+=1
   # Fixed error categories only; never publish page content or database rows.
   reason=str(exc) if isinstance(exc,SourceError) else 'Unrecognized source format'
   errors[reason]+=1
   if reason.startswith('Source HTTP 403') or 'cooldown' in reason or reason.startswith('Source HTTP 429'):
    counts['unprocessed']+=len(ids)-i-1;break
  time.sleep(.75)
 report={'source':source,'shard':shard,'planned':len(ids),'apply':apply,'counts':dict(counts),
         'countries':{k:dict(v) for k,v in countries.items()},'errors':dict(errors),'verified_at':datetime.now(timezone.utc).isoformat()}
 Path(f'output/{source}-{shard}-summary.json').write_text(json.dumps(report,indent=2))
 with open(os.environ.get('GITHUB_STEP_SUMMARY',os.devnull),'a') as f:f.write('```json\n'+json.dumps(report,indent=2)+'\n```\n')
 print(json.dumps(report))
 if counts['unverified'] or counts['unprocessed'] or counts['conflict']:raise SystemExit(1)

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--source',choices=HOSTS,required=True);p.add_argument('--plan',action='store_true')
 p.add_argument('--sample',type=int,default=0);p.add_argument('--shard',type=int,default=0);p.add_argument('--apply',action='store_true');a=p.parse_args()
 if a.sample<0 or a.sample>100:raise SystemExit('Sample must be 0..100')
 plan(a.source,a.sample) if a.plan else refresh(a.source,a.shard,a.apply)

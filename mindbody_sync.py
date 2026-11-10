"""Public Mindbody studio/schedule adapter. No booking or account endpoints."""
import argparse,json,os,re,time,unicodedata
from datetime import datetime,timedelta,timezone
from pathlib import Path
from urllib.parse import urlparse
from curl_cffi import requests
from event_db import db

ROOT=Path(__file__).parent
COUNTRIES='US CN DE JP GB IN FR RU IT BR CA KR MX AU ES TR ID NL SA CH PL TW BE AR IL SE IE SG AE NO AT TH CO MY VN BD PH DK ZA RO'.split()
BASE='https://prod-mkt-gateway.mindbody.io/v1/'
HEADERS={'Content-Type':'application/vnd.api+json','Accept':'application/vnd.api+json',
 'X-MB-APP-NAME':'mindbody.io','X-MB-APP-VERSION':'dfc9484f8','Origin':'https://www.mindbodyonline.com'}

def utc(s):
 d=datetime.fromisoformat(s.replace('Z','+00:00'))
 if d.tzinfo is None:raise ValueError('Source timestamp has no timezone')
 return d.astimezone(timezone.utc)

def slug(s):
 return re.sub(r'[^a-z0-9]+','-',unicodedata.normalize('NFKD',s).encode('ascii','ignore').decode().lower()).strip('-')

def clean(p):
 return {k:v for k,v in p.items() if v is not None and v!='' and v!=[]}

class PublicClient:
 def __init__(self):self.calls=0;self.session=requests.Session(impersonate='chrome')
 def post(self,path,payload):
  if path not in ('search/locations','location/schedules'):raise ValueError('Unsupported public endpoint')
  self.calls+=1
  if self.calls>700:raise RuntimeError('Public request budget exhausted')
  time.sleep(.3)
  r=self.session.post(BASE+path,headers=HEADERS,json=payload,timeout=40)
  if r.status_code in (403,429):raise RuntimeError(f'Mindbody access blocked: HTTP {r.status_code}; no bypass attempted')
  if r.status_code!=200:raise RuntimeError(f'Mindbody HTTP {r.status_code}')
  x=r.json()
  if not isinstance(x.get('data'),list):raise ValueError('Unexpected public response')
  return x
 def location(self,url,identity):
  u=urlparse(url or '')
  if u.hostname!='www.mindbodyonline.com' or not u.path.startswith('/explore/locations/'):raise ValueError('Unrecognized studio URL')
  x=self.post('search/locations',{'page':{'size':1,'number':1},'filter':{'radius':0,'locationSlugs':[u.path.rstrip('/').split('/')[-1]]}})
  if len(x['data'])!=1 or 'mindbody:'+str(x['data'][0]['id'])!=identity:raise ValueError('Studio source identity mismatch or unavailable')
  return x['data'][0]

def venue_patch(location):
 a=location['attributes'];lat,lon=map(float,a['latLon'].split(','))
 return clean(dict(name=a.get('name'),description=a.get('description') or a.get('locationDescription'),
  street=' '.join(filter(None,[a.get('address'),a.get('address2')])),zip=a.get('postalCode'),city=a.get('city'),
  region=a.get('state'),country=a.get('countryCode'),lat=lat,lon=lon,phone=a.get('phone'),
  logo_url=a.get('logo'),image_url=next((i['url'] for i in a.get('galleryImages',[]) if i.get('url')),None) or a.get('stockImage'),
  amenities=a.get('amenities'),activities=a.get('categories'),
  url='https://www.mindbodyonline.com/explore/locations/'+a['slug']))

def session_row(item,location,now):
 outer=item.get('attributes',{})
 if outer.get('subType')!='class_time':return None
 a=outer.get('attributes',{});course=a.get('course') or {};loc=location['attributes']
 if a.get('isCancelled') is not False:return None
 start,end=utc(a['startTime']),utc(a['endTime'])
 if not now<=start<now+timedelta(days=7) or end<=start:return None
 ref=json.loads(a['inventoryRefJson']);lr=json.loads(loc['inventoryRefJson'])
 if ref.get('inventory_source')!='MB' or any(str(ref.get(k))!=str(lr.get(k)) for k in ('mb_site_id','mb_location_id','mb_master_location_id')):raise ValueError('Class/studio identity mismatch')
 if not all(str(ref.get(k,'')).isdigit() for k in ('mb_site_id','mb_location_id','mb_class_id')):raise ValueError('Invalid class identity')
 eid='mindbody:'+':'.join(str(ref[k]) for k in ('mb_site_id','mb_location_id','mb_class_id'))
 if not course.get('name'):raise ValueError('Class has no title')
 p=venue_patch(location)
 description=course.get('description') or ''
 # Preserve source information in existing description; do not invent new schema fields.
 for label,value in [('Instructor',(a.get('staff') or {}).get('name')),('Prerequisites',a.get('prerequisiteNotes')),
                     ('Cancellation policy',a.get('cancellationPolicy')),('Refund policy',a.get('refundPolicy'))]:
  if value and value.strip() and '\ufffd' not in value:description+='\n\n'+label+': '+value.strip()
 status=(a.get('status') or {}).get('status')
 patch=clean(dict(title=course['name'],description=description.strip(),organizer=loc.get('name'),organizer_url=p['url'],
  starts_at=start.isoformat(),ends_at=end.isoformat(),venue=loc.get('locationName') or loc.get('name'),
  **{k:p.get(k) for k in ('street','zip','city','region','country','lat','lon')},
  url=('https://www.mindbodyonline.com/explore/fitness/classes/'+course['slug'] if course.get('slug') else p['url']),
  registration_status=status,subcategories=[s for s in [course.get('category'),course.get('subCategory')] if s],
  currency=loc.get('currencyCode')))
 option=a.get('purchaseOption') or {}
 if a.get('isFreeToEnroll') is True:patch['price']=0
 elif option.get('isSingleSession') is True and option.get('isIntroOffer') is False:
  price=(option.get('pricing') or {}).get('online')
  if price is not None and float(price)>=0:patch['price']=float(price)
 return eid,patch,ref

def rows(resource,params):
 result=[];offset=0
 while True:
  page=db(resource,params={**params,'offset':offset,'limit':500});result+=page
  if len(page)<500:return result
  offset+=len(page)

def merge(kind,identity,patch,expected,evidence,apply):
 if apply:return db('rpc/sync_mindbody_record','POST',dict(p_kind=kind,p_identity=identity,p_patch=patch,p_expected=expected,p_evidence=evidence))
 return {'action':'preview'}

def run(country,apply,limit,new_events,profile_limit=3):
 out=ROOT/'output';out.mkdir(exist_ok=True)
 stats={'country':country,'mode':'production' if apply else 'preview','started_at':datetime.now(timezone.utc).isoformat(),
  'venues_checked':0,'event_candidates':0,'events_inserted':0,'actions':{},'gaps':[],'new_venues_discovered':0}
 client=PublicClient();started=time.monotonic()
 checkpoint=db('directory_jobs_global_test',params={'source':'eq.mindbody','country':'eq.'+country,'select':'stats'})
 saved=checkpoint[0]['stats'] if checkpoint else {};cursor=saved.get('cursor','')
 venues=rows('venues',{'venue_id':'like.mindbody:*','country':'eq.'+country,'select':'*','order':'venue_id.asc'})
 stats['known_venues']=len(venues)
 after=[v for v in venues if v['venue_id']>cursor];selected=(after+[v for v in venues if v['venue_id']<=cursor])[:limit]
 existing={e['event_id']:e for e in rows('events',{'source':'eq.mindbody','country':'eq.'+country,'select':'*'})}
 # Revisit studios with already-published upcoming events daily before expanding.
 priority_urls={e.get('organizer_url') for e in existing.values() if e.get('ends_at') and utc(e['ends_at'])>datetime.now(timezone.utc)}
 priority=[v for v in venues if v.get('url') in priority_urls]
 selected=(priority+[v for v in selected if v.get('url') not in priority_urls])[:max(limit,len(priority))]
 selected=selected[:200]
 discovery_target=next((v['venue_id'] for v in selected if v.get('url') not in priority_urls),selected[0]['venue_id'] if selected else None)
 stats['existing_events']=len(existing);last=cursor or 'mindbody:0'
 profile_cursor=saved.get('profile_cursor','');stats['profile_cursor']=profile_cursor
 profile_order=([v for v in venues if v['venue_id']>profile_cursor]+[v for v in venues if v['venue_id']<=profile_cursor])[:profile_limit]
 location_cache={};profile_verified=set();profile_failed=False
 for start in range(0,len(profile_order),50):
  if time.monotonic()-started>420:
   stats['gaps'].append('Studio profile budget reached; saved cursor resumes next run');break
  batch=profile_order[start:start+50]
  try:
   slugs=[]
   for v in batch:
    u=urlparse(v.get('url') or '')
    if u.hostname=='www.mindbodyonline.com' and u.path.startswith('/explore/locations/'):slugs.append(u.path.rstrip('/').split('/')[-1])
   found=client.post('search/locations',{'page':{'size':50,'number':1},'filter':{'radius':0,'locationSlugs':slugs}})['data'] if slugs else []
   by_id={'mindbody:'+str(v['id']):v for v in found};items=[];verified_at=datetime.now(timezone.utc).isoformat()
   for old in batch:
    identity=old['venue_id'];loc=by_id.get(identity)
    if not loc or loc['attributes'].get('countryCode')!=country or 'Fitness' not in loc['attributes'].get('categoryTypes',[]):
     stats['unverified_profiles']=stats.get('unverified_profiles',0)+1;continue
    try:patch=venue_patch(loc)
    except (KeyError,ValueError,TypeError):
     stats['unverified_profiles']=stats.get('unverified_profiles',0)+1;continue
    items.append({'identity':identity,'patch':patch,'expected':old,'evidence':{'verified_at':verified_at,'location_id':str(loc['id']),'country':country}})
    if any(v['venue_id']==identity for v in selected):location_cache[identity]=loc
   results=db('rpc/sync_mindbody_venues','POST',{'p_items':items}) if apply and items else [{'action':'preview','identity':x['identity']} for x in items]
   for result in results:
    key='profile_'+result['action'];stats['actions'][key]=stats['actions'].get(key,0)+1
    if result['action']=='storage_guard_skipped':raise RuntimeError('400 MiB storage guard')
    profile_verified.add(result['identity'])
   stats['profiles_verified']=stats.get('profiles_verified',0)+len(items)
   stats['profile_cursor']=batch[-1]['venue_id']
   if apply:db('rpc/save_mindbody_checkpoint','POST',dict(p_country=country,p_stats={**stats,'cursor':last}))
  except Exception as e:
   stats['gaps'].append('Profile refresh: '+str(e)[:160]);stats['blocked']=True;profile_failed=True;break
 if stats.get('unverified_profiles'):stats['gaps'].append('Some stored studio profiles unavailable or not verified as fitness; retained unchanged')
 if len(profile_order)<len(venues):stats['gaps'].append('Profile row limit; remainder rotates next run')
 def record(kind,identity,patch,old,evidence):
  r=merge(kind,identity,patch,old,evidence,apply);key=kind+'_'+r['action'];stats['actions'][key]=stats['actions'].get(key,0)+1
  if r['action']=='storage_guard_skipped':raise RuntimeError('400 MiB storage guard; no further inserts')
  if kind=='event' and r['action']=='inserted':stats['events_inserted']+=1
  if kind=='venue' and old is None and r['action']=='inserted':stats['new_venues_discovered']+=1
 for seed in selected:
  if profile_failed:break
  if time.monotonic()-started>720:stats['gaps'].append('Runtime budget; remaining venues resume next run');break
  try:
   loc=location_cache.get(seed['venue_id']) or client.location(seed['url'],seed['venue_id']);a=loc['attributes']
   if a.get('countryCode')!=country or 'Fitness' not in a.get('categoryTypes',[]):raise ValueError('Country/category no longer verified')
   proof={'verified_at':datetime.now(timezone.utc).isoformat(),'location_id':str(loc['id']),'country':country}
   if seed['venue_id'] not in profile_verified:record('venue',seed['venue_id'],venue_patch(loc),seed,proof)
   now=datetime.now(timezone.utc)
   data=client.post('location/schedules',dict(location_ref_json=a['inventoryRefJson'],location_timezone=a['timezone'],
       start_time_from=now.isoformat(),start_time_to=(now+timedelta(days=7)).isoformat()))['data']
   if len(data)>=1000:stats['gaps'].append('Schedule response large; truncation not ruled out for '+seed['venue_id'])
   for item in data:
    parsed=session_row(item,loc,now)
    if not parsed:continue
    eid,patch,ref=parsed;stats['event_candidates']+=1
    if eid not in existing and stats['events_inserted']>=new_events:
     if 'New event budget reached' not in stats['gaps']:stats['gaps'].append('New event budget reached')
     continue
    evidence={**proof,'site_id':str(ref['mb_site_id']),'mb_location_id':str(ref['mb_location_id']),'class_id':str(ref['mb_class_id'])}
    record('event',eid,patch,existing.get(eid),evidence)
    if eid not in existing:existing[eid]=dict(patch,event_id=eid) # Duplicate response protection; reread before further changes.
   stats['venues_checked']+=1;last=seed['venue_id']
   # Bounded new studio discovery around the rotating first verified seed.
   if seed['venue_id']==discovery_target:
    vp=venue_patch(loc)
    nearby=client.post('search/locations',{'page':{'size':100,'number':1},'sort':'distance',
       'filter':{'latitude':vp['lat'],'longitude':vp['lon'],'radius':25000,'categoryTypes':['Fitness']}})
    known={v['venue_id'] for v in venues};new_count=0
    for found in nearby['data']:
     fa=found.get('attributes',{});identity='mindbody:'+str(found['id'])
     if identity in known or fa.get('countryCode')!=country or 'Fitness' not in fa.get('categoryTypes',[]):continue
     if new_count>=10:break
     record('venue',identity,venue_patch(found),None,{'verified_at':datetime.now(timezone.utc).isoformat(),'location_id':str(found['id']),'country':country})
     known.add(identity);new_count+=1
    stats['discovery_found_near_seed']=nearby.get('meta',{}).get('found')
    stats['gaps'].append('New studio discovery limited to first 100 nearby results and 10 inserts; national discovery incomplete')
   if apply:db('rpc/save_mindbody_checkpoint','POST',dict(p_country=country,p_stats={'cursor':last,'profile_cursor':stats['profile_cursor'],'last_venue_verified_at':proof['verified_at']}))
  except Exception as e:
   stats['gaps'].append(seed['venue_id']+': '+str(e)[:160])
   if isinstance(e,ValueError):
    # One unavailable/malformed studio must not permanently block a country.
    # Keep its gap explicit and revisit it on the next full rotation.
    last=seed['venue_id'];stats['unverified_venues']=stats.get('unverified_venues',0)+1
    if apply:db('rpc/save_mindbody_checkpoint','POST',dict(p_country=country,p_stats={'cursor':last,'profile_cursor':stats['profile_cursor'],'unverified_venue':last}))
    continue
   stats['blocked']=True
   # Do not advance past an access/database failure; permit a later retry.
   break
 stats['cursor']=last;stats['public_requests']=client.calls
 if not venues:stats['gaps'].append('No stored seed venues; this country has not been exhaustively searched')
 elif len(selected)<len(venues):stats['gaps'].append('Rotating studio batch; not all stored studios checked today')
 stats['status']='blocked' if stats.get('blocked') else 'partial' if stats['gaps'] else 'selected_studios_verified'
 if apply and last:db('rpc/save_mindbody_checkpoint','POST',dict(p_country=country,p_stats=stats))
 (out/('mindbody-'+country+'-summary.json')).write_text(json.dumps(stats,indent=2),encoding='utf8')
 if os.environ.get('GITHUB_STEP_SUMMARY'):
  with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:f.write('### Mindbody '+country+'\n```json\n'+json.dumps(stats,indent=2)+'\n```\n')
 print(json.dumps(stats))
 return stats

def main():
 p=argparse.ArgumentParser();p.add_argument('--country',choices=COUNTRIES,required=True);p.add_argument('--apply',action='store_true')
 p.add_argument('--venues',type=int,default=100);p.add_argument('--new-events',type=int,default=50);p.add_argument('--profile-venues',type=int,default=3);a=p.parse_args()
 if not 1<=a.venues<=200 or not 0<=a.new_events<=100 or not 1<=a.profile_venues<=25000:raise ValueError('Stage budget exceeded')
 result=run(a.country,a.apply,a.venues,a.new_events,a.profile_venues)
 if result.get('blocked'):raise SystemExit(2)
if __name__=='__main__':main()

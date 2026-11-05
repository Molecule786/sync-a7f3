"""Field-preserving ClassPass refresh; never rolls expired IDs into future classes."""
import html,json,re
from datetime import datetime,timezone
from pathlib import Path
from collector import CollectionError,timestamp
from global_collector import country_of,ISO2
from us_collector import slug

FIELDS={'title','organizer','organizer_url','description','starts_at','ends_at','venue','street','zip',
        'city','city_slug','region','country','lat','lon','url'}

def normalized(value):
    return re.sub(r'\s+',' ',html.unescape(str(value))).strip().casefold()

def proposal(row,before,observed,session_id=None):
    eid=row['event_id'];identity=eid.split(':')[1]
    if session_id is not None and str(session_id)!=identity:
        raise CollectionError('Session identity mismatch')
    patch={}; reasons={}
    for key,value in row.items():
        if key not in FIELDS or value in (None,''):
            continue
        old=(before or {}).get(key)
        if before:
            if key=='country' and old and old!=value:
                raise CollectionError('Source country does not match existing record')
            if key in ('starts_at','ends_at') and old and timestamp(old)==timestamp(value):continue
            if key not in ('starts_at','ends_at') and old is not None and normalized(old)==normalized(value):continue
            if key in ('lat','lon') and old is not None:continue
            if key=='url' and old and re.search(r'/'+identity+r'/?$',old):continue
            if key=='city_slug' and old and normalized(before.get('city',''))==normalized(row.get('city','')):continue
            if key=='street' and old:
                a=re.sub(r'[^a-z0-9]','',normalized(old));b=re.sub(r'[^a-z0-9]','',normalized(value))
                if a.startswith(b) or b.startswith(a):continue
        patch[key]=value
        reasons[key]='Source provides previously missing detail' if old in (None,'') else 'Verified same-session source value changed'
    return {'event_id':eid,'expected_before':before,'patch':patch,'reasons':reasons,
            'evidence':{'source':'classpass','session_id':identity,'verified_at':observed}}

def session_row(item,markets):
    venue=item.get('venue') or {};cls=item.get('class') or {};address=venue.get('address') or {}
    country=country_of(venue,markets)
    if not country or not isinstance(item.get('id'),int) or not venue.get('alias') or not cls.get('alias'):
        raise CollectionError('Unverified session identity/geography')
    if 'activity' not in (item.get('verticals') or []) or cls.get('entity_type')!='CLASS':
        raise CollectionError('Not a scheduled fitness class')
    begins=datetime.fromtimestamp(item['starttime'],timezone.utc);ends=datetime.fromtimestamp(item['endtime'],timezone.utc)
    if ends<=begins:raise CollectionError('Invalid session duration')
    row={'event_id':f"classpass:{item['id']}",'title':cls.get('name'),'organizer':venue.get('name'),
         'description':cls.get('description'),'starts_at':begins.isoformat(),'ends_at':ends.isoformat(),
         'venue':venue.get('name'),'street':address.get('address_line1'),'zip':address.get('zip_code'),
         'city':address.get('city'),'city_slug':slug(address['city']) if address.get('city') else None,
         'region':address.get('state'),'country':country,
         'url':f"https://classpass.com/classes/{venue['alias']}/{cls['alias']}/{item['id']}"}
    if str(venue.get('website') or '').startswith(('https://','http://')):
        row['organizer_url']=venue['website']
    for key in ('lat','lon'):
        if isinstance((venue.get('location') or {}).get(key),(int,float)):row[key]=venue['location'][key]
    return row,('expired' if ends<datetime.now(timezone.utc) else 'upcoming')

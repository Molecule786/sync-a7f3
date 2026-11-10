"""Fixed Supabase destinations; secrets stay exclusively in runner environment."""
import json,os,time
from pathlib import Path
from curl_cffi import requests

URL=json.loads((Path(__file__).parent/'config.json').read_text())['supabase_url']
READ={'events','classpass_event_freshness','classpass_market_events','classpass_session_details'}
WRITE={'rpc/refresh_existing_directory_event','rpc/purge_approved_expired_events','rpc/existing_event_refresh_candidates'}
READ.update({'venues_global_test','events_global_test','directory_jobs_global_test'})
READ.add('venues')
WRITE.update({'rpc/sync_mindbody_record','rpc/save_mindbody_checkpoint','rpc/sync_mindbody_venues'})
WRITE.update({'rpc/directory_retention_cache_state','rpc/prune_directory_retention_cache',
              'rpc/purge_source_confirmed_expired_events'})

def db(resource,method='GET',payload=None,params=None):
 if (method=='GET' and resource not in READ) or (method=='POST' and resource not in WRITE) or method not in ('GET','POST'):
  raise RuntimeError('Forbidden database operation')
 secret=os.environ.get('SUPABASE_SECRET_KEY','')
 if not secret:raise RuntimeError('Missing SUPABASE_SECRET_KEY')
 headers={'apikey':secret,'Content-Type':'application/json'}
 if secret.startswith('eyJ'):headers['Authorization']='Bearer '+secret
 for attempt in range(3 if method=='GET' else 1):
  try:
   response=requests.request(method,URL+'/rest/v1/'+resource,headers=headers,json=payload,params=params,timeout=55)
  except requests.RequestsError:
   if method=='GET' and attempt<2:time.sleep(2**attempt);continue
   raise RuntimeError('Database transport failure; no source data or credential logged') from None
  if response.status_code in (429,500,502,503,504) and method=='GET' and attempt<2:
   time.sleep(2**attempt);continue
  if not 200<=response.status_code<300:raise RuntimeError(f'Database request failed: HTTP {response.status_code}')
  return response.json()

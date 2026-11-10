"""Bounded source-confirmed retention; encrypted recovery precedes every purge."""
import argparse,base64,gzip,hashlib,json,os,secrets,time
from collections import Counter
from datetime import datetime,timedelta,timezone
from pathlib import Path
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from event_db import db
from event_refresh import source_get,parse_active,parse_opensports,timestamp,SourceError
from precise_refresh import session_row
from collector import CollectionError
from us_collector import PublicClient

ROOT=Path('output/retention')
SOURCES=('classpass','active','opensports')


def save(path,value):
 path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,indent=2),encoding='utf8')


class Backup:
 def __init__(self,root,cutoff):
  self.root=root;root.mkdir(parents=True,exist_ok=True)
  key=secrets.token_bytes(32);self.aes=AESGCM(key)
  pub=serialization.load_pem_public_key(Path('cleanup_backup_public.pem').read_bytes())
  wrapped=pub.encrypt(key,padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),algorithm=hashes.SHA256(),label=None))
  self.manifest={'cutoff':cutoff,'events':0,'counts':{},'chunks':[],
   'wrapped_key':base64.b64encode(wrapped).decode(),'format':'gzip+AES256GCM+RSA-OAEP-SHA256-v1'}
 def add(self,content):
  raw=json.dumps(content,ensure_ascii=False,separators=(',',':')).encode()
  if len(raw)>64*1024*1024:raise RuntimeError('Backup chunk exceeds recovery bound')
  index=len(self.manifest['chunks']);nonce=secrets.token_bytes(12)
  sealed=nonce+self.aes.encrypt(nonce,gzip.compress(raw),str(index).encode())
  assert gzip.decompress(self.aes.decrypt(nonce,sealed[12:],str(index).encode()))==raw
  name=f'{index:04}.bin';(self.root/name).write_bytes(sealed)
  self.manifest['chunks'].append({'file':name,'sha256':hashlib.sha256(sealed).hexdigest(),'raw_sha256':hashlib.sha256(raw).hexdigest()})
  for table,rows in content.items():self.manifest['counts'][table]=self.manifest['counts'].get(table,0)+len(rows)
  self.manifest['events']+=len(content.get('events',[]))
 def finish(self):save(self.root/'manifest.json',self.manifest)


def export_pages(table,params,backup,limit=25):
 offset=0
 while True:
  rows=db(table,params={**params,'select':'*','limit':limit,'offset':offset})
  if rows:backup.add({table:rows})
  if len(rows)<limit:return
  offset+=len(rows)


def cache_backup():
 state=db('rpc/directory_retention_cache_state','POST',{})
 backup=Backup(ROOT/'cache/encrypted',datetime.now(timezone.utc).isoformat())
 export_pages('venues_global_test',{'order':'id.asc'},backup)
 export_pages('directory_jobs_global_test',{'order':'source.asc,market_id.asc'},backup,5)
 backup.finish();save(ROOT/'cache/expected.json',state)
 if backup.manifest['counts'].get('venues_global_test',0)!=state['profiles']:
  raise RuntimeError('Profile backup count mismatch')
 if db('rpc/directory_retention_cache_state','POST',{})!=state:
  raise RuntimeError('Cache changed during backup; nothing eligible for removal')
 print(json.dumps({'encrypted_cache_backup_counts':backup.manifest['counts']}))


def cache_purge():
 state=json.loads((ROOT/'cache/expected.json').read_text())
 result=db('rpc/prune_directory_retention_cache','POST',{'p_expected':state})
 save(ROOT/'cache/summary.json',result);print(json.dumps(result))


def plan():
 cutoff=(datetime.now(timezone.utc)-timedelta(hours=24)).isoformat()
 ids=[];offset=0
 while len(ids)<40000:
  rows=db('events',params={'select':'event_id,source','source':'in.(classpass,active,opensports)',
   'ends_at':'lt.'+cutoff,'order':'ends_at.asc,event_id.asc','limit':500,'offset':offset})
  ids.extend(r['event_id'] for r in rows)
  if len(rows)<500:break
  offset+=len(rows)
 manifest={'cutoff':cutoff,'ids':ids,'size':200,'limit':40000,'backlog_may_remain':len(ids)>=40000}
 save(ROOT/'manifest.json',manifest)
 with open(os.environ['GITHUB_OUTPUT'],'a') as f:f.write('shards='+json.dumps(list(range((len(ids)+199)//200)) or [0])+'\n')
 print(json.dumps({'expiry_candidates':len(ids),'shards':(len(ids)+199)//200,'grace_hours':24}))


def confirmed(before,row,cutoff):
 if row.get('event_id',before['event_id'])!=before['event_id']:return False
 if row.get('country',before.get('country'))!=before.get('country'):return False
 try:
  return (timestamp(before['starts_at'])<timestamp(before['ends_at'])<timestamp(cutoff)
          and timestamp(row['starts_at'])<timestamp(row['ends_at'])<timestamp(cutoff))
 except (KeyError,TypeError,ValueError,SourceError):return False


def verify(shard):
 manifest=json.loads((ROOT/'manifest.json').read_text());ids=manifest['ids'][shard*200:(shard+1)*200]
 root=ROOT/str(shard);root.mkdir(parents=True,exist_ok=True)
 backup=Backup(root/'encrypted',manifest['cutoff']);counts=Counter();eligible=[];blocked=set()
 locators=json.loads(Path('active_existing_urls.json').read_text())
 markets={m['id']:m['country_code'] for m in json.loads(Path('global_markets.json').read_text(encoding='utf-8-sig'))}
 client=PublicClient(max_requests=250);client.deadline=time.monotonic()+480
 for i,eid in enumerate(ids):
  if time.monotonic()>=client.deadline:
   counts['unprocessed']+=len(ids)-i;break
  rows=db('events',params={'event_id':'eq.'+eid,'select':'*','limit':1})
  if not rows:counts['already_missing']+=1;continue
  before=rows[0];source=before['source']
  if source in blocked:counts['unverified']+=1;continue
  try:
   if source=='classpass':
    identity=eid.split(':',1)[1];url='https://classpass.com/_api/v1/schedules/'+identity
    raw=client.json('GET','/_api/v1/schedules/'+identity)
    row,_=session_row(raw,markets,existing_record=True)
   else:
    url=locators.get(eid,before.get('url'))
    page=source_get(url,source)
    row=parse_active(page,before,url) if source=='active' else parse_opensports(page,before)
   if not confirmed(before,row,manifest['cutoff']):
    counts['retained_rescheduled_or_unconfirmed']+=1;continue
   evidence={'event_id':eid,'source':source,'country':before.get('country'),'original_url':before.get('url'),
    'fetched_url':url,'starts_at':row['starts_at'],'ends_at':row['ends_at'],
    'verified_at':datetime.now(timezone.utc).isoformat()}
   item={'expected':before,'evidence':evidence};eligible.append(item)
   content={'events':[before]}
   for table in ('classpass_market_events','classpass_session_details','classpass_event_freshness'):
    content[table]=db(table,params={'event_id':'eq.'+eid,'select':'*','limit':1000})
    if len(content[table])>=1000:raise RuntimeError('Child backup pagination bound reached')
   backup.add(content);counts['source_confirmed_expired']+=1
  except (CollectionError,SourceError,ValueError,KeyError,TypeError) as exc:
   counts['unverified']+=1
   if any(v in str(exc) for v in ('403','429','401','cooldown','budget')):blocked.add(source)
  if source!='classpass':time.sleep(.75)
 client.session.close();backup.finish()
 if len(eligible)!=backup.manifest['events']:raise RuntimeError('Backup eligibility mismatch')
 save(root/'pending.json',eligible);save(root/'summary.json',{'shard':shard,'planned':len(ids),'counts':dict(counts)})
 print(json.dumps({'shard':shard,'planned':len(ids),'counts':dict(counts)}))


def purge(shard):
 root=ROOT/str(shard);manifest=json.loads((ROOT/'manifest.json').read_text())
 items=json.loads((root/'pending.json').read_text());counts=Counter()
 backup=json.loads((root/'encrypted/manifest.json').read_text())
 if backup['events']!=len(items):raise RuntimeError('Verified backup mismatch')
 for chunk in backup['chunks']:
  if hashlib.sha256((root/'encrypted'/chunk['file']).read_bytes()).hexdigest()!=chunk['sha256']:
   raise RuntimeError('Encrypted backup changed')
 for start in range(0,len(items),25):
  counts.update(db('rpc/purge_source_confirmed_expired_events','POST',
   {'p_items':items[start:start+25],'p_cutoff':manifest['cutoff']}))
 result=json.loads((root/'summary.json').read_text());result['purge']=dict(counts)
 save(root/'summary.json',result);print(json.dumps(result))
 with open(os.environ.get('GITHUB_STEP_SUMMARY',os.devnull),'a') as f:f.write('```json\n'+json.dumps(result,indent=2)+'\n```\n')
 if counts['conflicts']:raise SystemExit('Concurrent edits retained; inspect summary')


if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('mode',choices=['cache_backup','cache_purge','plan','verify','purge'])
 p.add_argument('--shard',type=int,default=0);a=p.parse_args()
 if a.mode in ('verify','purge'):globals()[a.mode](a.shard)
 else:globals()[a.mode]()

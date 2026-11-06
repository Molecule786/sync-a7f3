"""One-off approved cleanup. Upload only encrypted backup files, never private/ ."""
import argparse,base64,gzip,hashlib,json,os,secrets
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from event_db import db

CUTOFF='2026-11-06T05:12:13.351404Z'
ROOT=Path('output/expired_cleanup')

def dt(s):
 d=datetime.fromisoformat(s.replace('Z','+00:00'))
 if d.tzinfo is None:raise ValueError('Missing timezone')
 return d.astimezone(timezone.utc)

def digest(data):return hashlib.sha256(data).hexdigest()

def backup():
 private=ROOT/'private';encrypted=ROOT/'encrypted'
 private.mkdir(parents=True,exist_ok=True);encrypted.mkdir(parents=True,exist_ok=True)
 key=secrets.token_bytes(32);aes=AESGCM(key)
 pub=serialization.load_pem_public_key(Path('cleanup_backup_public.pem').read_bytes())
 wrapped=pub.encrypt(key,padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),algorithm=hashes.SHA256(),label=None))
 manifest={'cutoff':CUTOFF,'counts':{},'skipped_invalid':0,'chunks':[],
           'wrapped_key':base64.b64encode(wrapped).decode(),'format':'gzip+AES256GCM+RSA-OAEP-SHA256-v1'}
 counts=Counter();after=None;seen=set();index=0
 with (private/'events.jsonl').open('w',encoding='utf8') as pending:
  while True:
   params={'select':'*','source':'in.(classpass,active,opensports)','ends_at':'lt.'+CUTOFF,'order':'event_id.asc','limit':100}
   if after:params['event_id']='gt.'+after
   rows=db('events','GET',params=params)
   if not rows:break
   valid=[]
   for e in rows:
    if e['event_id'] in seen:raise RuntimeError('Repeated backup page')
    seen.add(e['event_id'])
    if not e.get('starts_at') or not dt(e['starts_at'])<dt(e['ends_at'])<dt(CUTOFF):
     manifest['skipped_invalid']+=1;continue
    valid.append(e);counts[e['source']]+=1
   if valid:
    ids='in.('+','.join(e['event_id'] for e in valid)+')'
    content={'events':valid}
    for table in ['classpass_event_freshness','classpass_market_events','classpass_session_details']:
     # A page has at most 25 event IDs; paginate the mapping table too.
     result=[];offset=0
     while True:
      child=db(table,'GET',params={'select':'*','event_id':ids,'order':('event_id.asc,market_id.asc' if table=='classpass_market_events' else 'event_id.asc'),'limit':500,'offset':offset})
      result.extend(child)
      if len(child)<500:break
      offset+=len(child)
     content[table]=result
    raw=json.dumps(content,separators=(',',':'),ensure_ascii=False).encode()
    packed=gzip.compress(raw);nonce=secrets.token_bytes(12)
    sealed=nonce+aes.encrypt(nonce,packed,str(index).encode())
    # Verify authenticated round trip before making a deletion eligible.
    assert gzip.decompress(aes.decrypt(nonce,sealed[12:],str(index).encode()))==raw
    name=f'{index:04}.bin';(encrypted/name).write_bytes(sealed)
    manifest['chunks'].append({'file':name,'sha256':digest(sealed),'raw_sha256':digest(raw),'events':len(valid)})
    for e in valid:pending.write(json.dumps(e,separators=(',',':'),ensure_ascii=False)+'\n')
    index+=1
   after=rows[-1]['event_id']
   if len(rows)<100:break
 manifest['counts']=dict(counts);manifest['events']=sum(counts.values())
 if manifest['events']!=17861:raise RuntimeError('Candidate count changed: review before deleting')
 (encrypted/'manifest.json').write_text(json.dumps(manifest,indent=2))
 print(json.dumps({'backup_verified':manifest['events'],'counts':dict(counts),'invalid_dates_retained':manifest['skipped_invalid']}))

def purge():
 manifest=json.loads((ROOT/'encrypted/manifest.json').read_text())
 if manifest['events']!=17861 or manifest['cutoff']!=CUTOFF:raise RuntimeError('Unexpected backup')
 for item in manifest['chunks']:
  if digest((ROOT/'encrypted'/item['file']).read_bytes())!=item['sha256']:raise RuntimeError('Backup changed')
 total=Counter();batch=[]
 def flush():
  if not batch:return
  result=db('rpc/purge_approved_expired_events','POST',{'p_expected':batch,'p_cutoff':CUTOFF})
  total.update(result);batch.clear()
 with (ROOT/'private/events.jsonl').open(encoding='utf8') as f:
  for line in f:
   e=json.loads(line)
   if batch and (len(batch)>=25 or len(json.dumps(batch))+len(line)>4*1024*1024):flush()
   batch.append(e)
  flush()
 report={'cutoff':CUTOFF,'backup_events':manifest['events'],**total}
 (ROOT/'cleanup_result.json').write_text(json.dumps(report,indent=2))
 print(json.dumps(report))
 if total['conflicts']:raise RuntimeError('Concurrent edits retained; inspect cleanup report')

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('mode',choices=['backup','purge']);a=p.parse_args()
 backup() if a.mode=='backup' else purge()

"""Local recovery verification. Never upload the private key or decrypted output."""
import argparse,base64,gzip,hashlib,io,json,zipfile
from collections import Counter
from pathlib import Path
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

def decrypt(archive,private_key,output):
 key=serialization.load_pem_private_key(Path(private_key).read_bytes(),password=None)
 counts=Counter()
 with zipfile.ZipFile(archive) as z:
  manifest=json.loads(z.read('manifest.json'))
  symmetric=key.decrypt(base64.b64decode(manifest['wrapped_key']),padding.OAEP(
   mgf=padding.MGF1(hashes.SHA256()),algorithm=hashes.SHA256(),label=None))
  aes=AESGCM(symmetric)
  with gzip.open(output,'wt',encoding='utf8') as out:
   for index,item in enumerate(manifest['chunks']):
    sealed=z.read(item['file'])
    if hashlib.sha256(sealed).hexdigest()!=item['sha256']:raise ValueError('Encrypted chunk checksum mismatch')
    packed=aes.decrypt(sealed[:12],sealed[12:],str(index).encode())
    with gzip.GzipFile(fileobj=io.BytesIO(packed)) as f:raw=f.read(64*1024*1024+1)
    if len(raw)>64*1024*1024 or hashlib.sha256(raw).hexdigest()!=item['raw_sha256']:
     raise ValueError('Plaintext chunk checksum/size mismatch')
    obj=json.loads(raw)
    for name,rows in obj.items():counts[name]+=len(rows)
    out.write(raw.decode()+'\n')
 if counts['events']!=manifest['events']:raise ValueError('Backup event count mismatch')
 report={'cutoff':manifest['cutoff'],'backup_counts':dict(counts),'verification':manifest.get('verification'),
         'verified_output':str(Path(output).resolve())}
 Path(str(output)+'.verified.json').write_text(json.dumps(report,indent=2))
 print(json.dumps(report))

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('archive');p.add_argument('private_key');p.add_argument('output');a=p.parse_args()
 decrypt(a.archive,a.private_key,a.output)

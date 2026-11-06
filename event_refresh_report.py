"""Summarize every planned existing-record shard without hiding missing jobs."""
import argparse,json,os
from collections import Counter,defaultdict
from pathlib import Path

def report(source):
 root=Path('output');manifest=json.loads((root/f'{source}-manifest.json').read_text())
 counts=Counter();countries=defaultdict(Counter);missing=[];applied=set()
 for shard in range(max(1,(len(manifest['ids'])+99)//100)):
  p=root/f'{source}-{shard}-summary.json'
  if not p.exists():
   missing.append(shard);counts['unprocessed']+=len(manifest['ids'][shard*100:(shard+1)*100]);continue
  data=json.loads(p.read_text());counts.update(data['counts']);applied.add(data['apply'])
  for country,values in data['countries'].items():countries[country].update(values)
 complete=not missing and not any(counts[k] for k in ('unverified','unprocessed','conflict','missing'))
 result={'source':source,'scope':'Existing stored events only; national inventory not assessed',
         'planned':len(manifest['ids']),'complete_existing_set':complete,'production':applied=={True},
         'counts':dict(counts),'missing_shards':missing,'countries':{}}
 for country,planned in manifest['countries'].items():
  values=dict(countries[country]);values['planned']=planned
  accounted=sum(countries[country][k] for k in ('updated','unchanged','would_update','unverified','conflict','missing'))
  values['unaccounted']=max(0,planned-accounted);result['countries'][country]=values
 path=root/f'{source}-refresh-report.json';path.write_text(json.dumps(result,indent=2))
 with open(os.environ.get('GITHUB_STEP_SUMMARY',os.devnull),'a') as f:
  f.write('## '+source+' existing-event refresh\n\n')
  f.write(f"Planned **{result['planned']}**; updated **{counts['updated']}**; unchanged **{counts['unchanged']}**. ")
  f.write('Existing-record coverage: **'+('complete' if complete else 'incomplete — see gaps below')+'**.\n\n')
  f.write(result['scope']+'.\n\n| Country | Planned | Updated | Unchanged | Unverified | Unaccounted |\n|---|---:|---:|---:|---:|---:|\n')
  for country,v in sorted(result['countries'].items()):
   f.write(f"| {country} | {v['planned']} | {v.get('updated',0)} | {v.get('unchanged',0)} | {v.get('unverified',0)} | {v['unaccounted']} |\n")
  if not result['production']:f.write('\nPreview: no database writes were requested.\n')
 print(json.dumps(result))
 if not complete:raise SystemExit(1)

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--source',choices=['active','opensports'],required=True);report(p.parse_args().source)

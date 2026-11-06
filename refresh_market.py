"""Bounded resumptions; unfinished work remains explicitly partial."""
import argparse,json,os
from run_global import ROOT,run
from collector import CollectionError

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--market',type=int,required=True)
    p.add_argument('--attempts',type=int,default=3);a=p.parse_args()
    if not 1<=a.attempts<=3:raise SystemExit('At most three bounded attempts per job')
    all_markets=json.loads((ROOT/'global_markets.json').read_text(encoding='utf-8-sig'))
    market=next(m for m in all_markets if m['id']==a.market)
    results=[]
    for attempt in range(a.attempts):
        try:
            result=run(market,{m['id']:m['country_code'] for m in all_markets},150,True)
        except Exception as exc:
            result={'status':'failed','market_id':a.market,'retryable':False,
                    'error':str(exc) if isinstance(exc,CollectionError) else type(exc).__name__}
            root=ROOT/'output/global'/str(a.market);root.mkdir(parents=True,exist_ok=True)
            (root/'summary.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        results.append(result);print(json.dumps(result),flush=True)
        # Never repeat access failures, source gaps or an explicit cooldown.
        if result['status']=='complete' or not result.get('retryable') or result.get('retry_not_before'):break
    (ROOT/'output/global'/str(a.market)/'attempt-results.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:
            f.write('## '+market['name']+' / '+market['country_code']+'\n\n```json\n'+json.dumps(results,indent=2)+'\n```\n')
    if result['status']!='complete':raise SystemExit(2)

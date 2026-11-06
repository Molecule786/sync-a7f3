"""Plan all routes daily; the retry schedule selects recent unfinished work only."""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from run_global import ROOT, database
from recheck_existing import plan


def retry_candidates(markets, jobs, now):
    wanted = set()
    for job in jobs:
        stats = job.get('stats') or {}
        start = stats.get('window_start')
        if not start or stats.get('status') == 'complete' or not stats.get('retryable'):
            continue
        age = now - datetime.fromisoformat(start.replace('Z', '+00:00'))
        cooldown = stats.get('retry_not_before')
        if timedelta(0) <= age < timedelta(hours=12) and (
            not cooldown or now >= datetime.fromisoformat(cooldown.replace('Z', '+00:00'))
        ):
            wanted.add(job['market_id'])
    return [m for m in markets if m['id'] in wanted]


def main():
    retry_only = os.environ.get('RETRY_ONLY') == 'true'
    health = database('POST', 'rpc/directory_global_test_health', {})
    if not health.get('can_collect'):
        raise SystemExit('400 MiB storage guard: stopped before starting source work')
    plan()
    markets = json.loads((ROOT / 'global_markets.json').read_text(encoding='utf-8-sig'))
    countries = json.loads((ROOT / 'global_scope.json').read_text())['countries']
    assert len(countries) == 40 and len({c['iso3'] for c in countries}) == 40
    assert {m['country_code'] for m in markets} <= {c['iso3'] for c in countries}
    if retry_only:
        jobs = database('GET', 'directory_jobs_global_test', params={
            'source': 'eq.classpass-production', 'select': 'market_id,stats,updated_at',
            'order': 'market_id.asc', 'limit': 1000})
        markets = retry_candidates(markets, jobs, datetime.now(timezone.utc))
    first = {}
    for market in markets:
        first.setdefault(market['country_code'], market['id'])
    selected = list(first.values()) + [m['id'] for m in markets if m['id'] not in first.values()]
    if os.environ.get('REQUEST_SCOPE') == 'existing_only':
        selected=[]
    manifest = {'retry_only': retry_only, 'market_ids': selected,
                'run_existing': not retry_only and os.environ.get('REQUEST_SCOPE') != 'markets_only',
                'scope_countries': 40, 'database_bytes': health['bytes']}
    Path('output').mkdir(exist_ok=True)
    Path('output/classpass-route-plan.json').write_text(json.dumps(manifest, indent=2))
    existing_path=Path('output/recheck-manifest.json')
    existing=json.loads(existing_path.read_text())
    existing['execution_plan']=manifest
    existing_path.write_text(json.dumps(existing,indent=2))
    with open(os.environ['GITHUB_OUTPUT'], 'a') as f:
        f.write('markets=' + json.dumps(selected) + '\n')
        f.write('has_markets=' + str(bool(selected)).lower() + '\n')
        f.write('run_existing=' + str(manifest['run_existing']).lower() + '\n')
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as f:
        f.write('## ClassPass route plan\n\n' + json.dumps(manifest, indent=2) + '\n\n'
                'All 40 countries are reconciled. Missing routes remain unresolved, never zero. '
                'Retry jobs resume recent retryable work only; source failures are retained.\n')


if __name__ == '__main__':
    main()

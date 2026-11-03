"""Read the public, published ClassPass market catalogue. No database writes."""
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
from collector import CollectionError, fetch
from curl_cffi import requests

def catalogue(html):
    tag = BeautifulSoup(html, 'html.parser').find('script', id='store')
    if tag is None:
        raise CollectionError('Public market catalogue missing')
    entities = json.loads(tag.text)['entities']
    markets = entities['msas']['data']['default']
    if not isinstance(markets, list) or not markets:
        raise CollectionError('Empty market catalogue is not verified coverage')
    ids = set()
    for market in markets:
        if not isinstance(market.get('id'), int) or market['id'] in ids:
            raise CollectionError('Invalid or duplicate market ID')
        ids.add(market['id'])
        if not market.get('country_code') or not market.get('alias'):
            raise CollectionError('Market country or alias missing')
        ZoneInfo(market['timezone'])
    return markets

def main():
    with requests.Session() as session:
        html = fetch(session, 'https://classpass.com/locations')
    markets = catalogue(html)
    root = Path(__file__).resolve().parent
    (root/'classpass_published_markets.json').write_text(json.dumps(markets,indent=2,ensure_ascii=False),encoding='utf-8')
    selected = {r['iso3'] for r in json.loads((root/'global_scope.json').read_text())['countries']}
    markets = [m for m in markets if m['country_code'] in selected]
    (root/'global_markets.json').write_text(json.dumps(markets, indent=2, ensure_ascii=False), encoding='utf-8')
    report = {'discovered_at': datetime.now(timezone.utc).isoformat(),
              'source': 'https://classpass.com/locations',
              'markets': len(markets), 'countries': dict(sorted(Counter(m['country_code'] for m in markets).items())),
              'scope': 'Top 40 nominal-GDP economies only; see global_scope.json',
              'coverage': 'Published market catalogue only; fitness directories and schedules require reconciliation.'}
    (root/'global_discovery.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))

if __name__ == '__main__':
    main()

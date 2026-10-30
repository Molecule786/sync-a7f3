"""Bounded public-schedule pilot. Default mode never writes to a database."""
import argparse
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from curl_cffi import requests


class CollectionError(RuntimeError):
    pass


def timestamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise CollectionError('Source timestamp has no timezone')
    return result.astimezone(timezone.utc)


def parse_schedule(html, config, now):
    soup = BeautifulSoup(html, 'html.parser')
    events = []
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(script.string or script.get_text())
        except json.JSONDecodeError as exc:
            raise CollectionError('Invalid source structured data') from exc
        nodes = data if isinstance(data, list) else data.get('@graph', [data])
        for node in nodes:
            types = node.get('@type', [])
            if 'Event' not in ([types] if isinstance(types, str) else types):
                continue
            url = node.get('url', '')
            parsed = urlparse(url)
            match = re.search(r'/classes/[^/]+/[^/]+/(\d+)/?$', parsed.path)
            if parsed.scheme != 'https' or parsed.hostname != 'classpass.com' or not match:
                raise CollectionError('A class lacks a stable ClassPass session ID')
            title = str(node.get('name', '')).strip()
            if not title:
                raise CollectionError('A class has no title')
            try:
                start, end = timestamp(node['startDate']), timestamp(node['endDate'])
            except (KeyError, ValueError, TypeError) as exc:
                raise CollectionError('Invalid class dates') from exc
            if end <= start:
                raise CollectionError('Class end must be after start')
            if not now <= start < now + timedelta(days=config['days_ahead']):
                continue
            place = node.get('location') or {}
            row = {
                'event_id': 'classpass:' + match[1], 'source': 'classpass',
                'title': title, 'starts_at': start.isoformat(), 'ends_at': end.isoformat(),
                'url': url, 'country': config['country'], 'city': config['city'],
                'city_slug': config['city_slug'], 'region': config['region'],
                'category': 'fitness',
            }
            if place.get('name'):
                row['venue'] = row['organizer'] = place['name']
            address = place.get('address')
            if isinstance(address, str) and address.strip():
                row['street'] = address.strip()
            elif isinstance(address, dict):
                for src, dst in [('streetAddress', 'street'), ('postalCode', 'zip')]:
                    if address.get(src):
                        row[dst] = address[src]
            if node.get('description'):
                row['description'] = node['description']
            # Credits are not money. Do not invent price, currency or availability.
            events.append(row)
    if soup.select_one('[data-testid="Schedule"]') is None:
        raise CollectionError('Schedule page missing; access failure or source format changed')
    return events


def fetch(session, url):
    # Sequential requests, bounded retries; do not retry access denial.
    for attempt in range(3):
        response = session.get(url, impersonate='chrome', timeout=30)
        if response.status_code in (401, 403):
            raise CollectionError(f'ClassPass denied access (HTTP {response.status_code})')
        if response.status_code == 429 or response.status_code >= 500:
            if attempt == 2:
                break
            delay = response.headers.get('Retry-After', '')
            if delay.isdigit() and int(delay) > 60:
                raise CollectionError('Source requested a long cooldown; retry next run')
            time.sleep(int(delay) if delay.isdigit() else 5 * (attempt + 1))
            continue
        break
    if response.status_code != 200:
        raise CollectionError(f'Source HTTP {response.status_code}')
    return response.text


def collect(config, now=None):
    now = now or datetime.now(timezone.utc)
    rows, seen = [], set()
    with requests.Session() as session:
        # Fetch every configured pilot studio before any write. No partial batch on failure.
        for index, url in enumerate(config['studios']):
            if index:
                time.sleep(2)
            for row in parse_schedule(fetch(session, url), config, now):
                if row['event_id'] not in seen:
                    rows.append(row)
                    seen.add(row['event_id'])
    rows.sort(key=lambda r: (r['starts_at'], r['event_id']))
    if not rows:
        raise CollectionError('No upcoming classes verified; database unchanged')
    return rows[:config['limit']]


def write_test(config, rows):
    secret = os.environ.get('SUPABASE_SECRET_KEY')
    if not secret:
        raise CollectionError('SUPABASE_SECRET_KEY is missing')
    headers = {'apikey': secret, 'Content-Type': 'application/json'}
    # Legacy JWTs need Authorization; sb_secret keys use the apikey header.
    if secret.startswith('eyJ'):
        headers['Authorization'] = 'Bearer ' + secret
    response = requests.post(
        config['supabase_url'].rstrip('/') + '/rest/v1/rpc/sync_classpass_test',
        headers=headers, json={'p_rows': rows}, timeout=30,
    )
    if response.status_code != 200:
        # Never echo headers, keys or arbitrary server response bodies into Actions logs.
        raise CollectionError(f'Test database merge failed (HTTP {response.status_code})')
    result = response.json()
    if not isinstance(result, dict) or result.get('processed') != len(rows):
        raise CollectionError('Database did not confirm the full batch')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--write-test', action='store_true')
    args = parser.parse_args()
    config = json.loads(Path('config.json').read_text())
    output = Path('output')
    output.mkdir(exist_ok=True)
    try:
        rows = collect(config)
        (output / 'preview.json').write_text(json.dumps(rows, indent=2), encoding='utf-8')
        result = write_test(config, rows) if args.write_test else {'mode': 'preview'}
        summary = {'status': 'success', 'verified_rows': len(rows), **result,
                   'finished_at': datetime.now(timezone.utc).isoformat(),
                   'coverage': 'Pilot: current public schedule from two configured NYC studios; not all NYC or all seven days'}
    except Exception as exc:
        summary = {'status': 'failed', 'error': str(exc) if isinstance(exc, CollectionError) else type(exc).__name__,
                   'finished_at': datetime.now(timezone.utc).isoformat()}
        (output / 'summary.json').write_text(json.dumps(summary, indent=2))
        print(json.dumps(summary))
        raise SystemExit(1)
    (output / 'summary.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary))


if __name__ == '__main__':
    main()

"""Worldwide public fitness collection. File output only: no database writer.

Country is verified per studio. Directory cursors and completed date/venue tasks
are checkpointed separately, keeping large markets out of a single JSON blob.
"""
import argparse
import gzip
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from collector import CollectionError
from enrichment import class_details, session_details, studio_record
from us_collector import PublicClient, first_directory, directory_module, save, slug, schedule_pages, merge_venue_metadata
from sync_state import RetryableError

ISO2 = dict(zip(
    'ARE AUS AUT BEL BRA CAN CHE CHL DEU DNK ESP FIN FRA GBR HKG IDN IND IRL JPN KWT MYS NLD NOR NZL PHL PRT QAT SGP SWE THA USA'.split(),
    'AE AU AT BE BR CA CH CL DE DK ES FI FR GB HK ID IN IE JP KW MY NL NO NZ PH PT QA SG SE TH US'.split()))
ISO2.update(dict(zip('CHN RUS ITA KOR MEX TUR SAU POL TWN ARG ISR COL VNM BGD ZAF ROU'.split(),
                     'CN RU IT KR MX TR SA PL TW AR IL CO VN BD ZA RO'.split())))

def country_of(venue, markets):
    direct = (venue.get('location') or {}).get('country_short_code')
    nested = (((venue.get('global_location') or {}).get('msa') or {}).get('country') or {}).get('short_code')
    msa = (venue.get('location') or {}).get('msa_id')
    values = {ISO2.get(str(v).upper(), str(v).upper()) for v in (direct, nested, markets.get(msa)) if v}
    if len(values) > 1:
        return None
    return next(iter(values), None)

def parse_global(item, market, markets, start, end, now):
    venue, cls = item.get('venue') or {}, item.get('class') or {}
    country = country_of(venue, markets)
    if country is None:
        return None, 'unknown_or_conflicting_country'
    if country != ISO2[market['country_code']]:
        return None, 'outside_country'
    if 'activity' not in (item.get('verticals') or []) or cls.get('entity_type') != 'CLASS':
        return None, 'not_fitness_class'
    if not isinstance(item.get('id'), int) or not isinstance(venue.get('id'), int):
        raise CollectionError('Missing stable session/studio identity')
    begins = datetime.fromtimestamp(item['starttime'], timezone.utc)
    finishes = datetime.fromtimestamp(item['endtime'], timezone.utc)
    if finishes <= begins:
        raise CollectionError('Invalid class duration')
    if not max(start, now) <= begins < end:
        return None, 'outside_window'
    address = venue.get('address') or {}
    # Some countries have no state/province field. Do not require a US state.
    title, city = cls.get('name'), address.get('city')
    if not title or not city or not venue.get('alias') or not cls.get('alias'):
        return None, 'missing_required_fields'
    row = {'event_id': f"classpass:{item['id']}", 'source': 'classpass', 'title': title,
           'starts_at': begins.isoformat(), 'ends_at': finishes.isoformat(),
           'country': country, 'city': city, 'city_slug': slug(city),
           'region': address.get('state'), 'venue': venue.get('name'), 'organizer': venue.get('name'),
           'description': cls.get('description'), 'street': address.get('address_line1'),
           'zip': address.get('zip_code'),
           'url': f"https://classpass.com/classes/{venue['alias']}/{cls['alias']}/{item['id']}",
           'category': 'fitness'}
    if str(venue.get('website') or '').startswith(('https://','http://')):
        row['organizer_url']=venue['website']
    for key in ('lat', 'lon'):
        value = (venue.get('location') or {}).get(key)
        if isinstance(value, (int, float)):
            row[key] = value
    return {k:v for k,v in row.items() if v is not None and v != ''}, None

def write_gzip(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_bytes(gzip.compress(json.dumps(value, ensure_ascii=False).encode('utf-8'), mtime=0))
    temporary.replace(path)

def collect(market, markets, root, max_requests=150, profile_loader=None):
    root.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    state_path = root/'checkpoint.json'
    state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
    if state and (state.get('market_id') != market['id'] or state.get('country') != ISO2[market['country_code']]):
        raise CollectionError('Checkpoint market/country mismatch')
    if state and now - datetime.fromisoformat(state['window_start']) > timedelta(hours=12):
        # New generation paths retain earlier source snapshots without mixing windows.
        state = {}
    if not state:
        state = {'market_id':market['id'], 'country':ISO2[market['country_code']],
                 'window_start':now.isoformat(), 'window_end':(now+timedelta(days=7)).isoformat(),
                 'generation':now.strftime('%Y%m%dT%H%M%S'), 'venues':{}, 'directory_pages':0,
                 'directory_done':False, 'tasks':{}, 'excluded':{}, 'status':'partial'}
    start, end = (datetime.fromisoformat(state[k]) for k in ('window_start', 'window_end'))
    generation = root/state['generation']
    generation.mkdir(parents=True, exist_ok=True)
    client = PublicClient(max_requests=max_requests)
    profile_cache_hits = profile_source_requests = 0
    retry_at = state.get('retry_not_before')
    if retry_at and now < datetime.fromisoformat(retry_at):
        return {'status':'cooldown', 'retry_not_before':retry_at, 'market_id':market['id']}
    state.pop('error', None)
    state['retryable'] = True
    try:
        while not state['directory_done']:
            if state.get('cursor'):
                data = client.json('POST', '/_api/unisearch/v1/layout/web_search_page',
                                   json={'search_request':{'cursor':state['cursor']}, 'modules':['web_search_results_01']})
                page = directory_module(data['data']['modules'])
            else:
                path = market.get('directory_path') or client.json('GET', '/_api/unisearch/v1/search_url',
                                   params={'tag_id':'activity', 'msa_id':market['id']}).get('url', '')
                if not path.startswith('/search/') or '?' in path:
                    raise CollectionError('No verified fitness directory; not a confirmed zero')
                page = first_directory(client.request('GET', path).text)
            if 'expected' in state and state['expected'] != page['total_hits']:
                # Directory changes require fresh enumeration; retain old generation evidence.
                state.update(venues={}, cursor=None, directory_pages=0)
                state.pop('expected', None)
                save(state_path, state)
                raise RetryableError('Directory count changed; restart discovery on retry')
            state['expected'] = page['total_hits']
            for venue in page['venue_tab_items']:
                venue = dict(venue)
                identity = venue.get('id', venue.get('venue_id'))
                if not isinstance(identity, int) or not venue.get('alias'):
                    raise CollectionError('Invalid directory venue identity')
                venue['id'] = identity
                state['venues'][str(identity)] = venue
            state['directory_pages'] += 1
            state['directory_done'] = len(state['venues']) >= state['expected']
            old_cursor = state.get('cursor')
            state['cursor'] = page.get('cursor')
            save(state_path, state)
            if not state['directory_done'] and (not state['cursor'] or state['cursor'] == old_cursor or not page['venue_tab_items']):
                raise CollectionError('Directory pagination did not reconcile')
        venue_ids = sorted(int(v) for v in state['venues'])
        tz = ZoneInfo(market['timezone'])
        day, last = start.astimezone(tz).date(), (end-timedelta(microseconds=1)).astimezone(tz).date()
        if market.get('country_wide_fallback'):
            # A country route can span time zones. Request both edge dates;
            # parse_global still enforces the exact UTC seven-day interval.
            day -= timedelta(days=1)
            last += timedelta(days=1)
        dates = []
        while day <= last:
            dates.append(day.isoformat())
            day += timedelta(days=1)
        tasks = [(date, venue_ids[i:i+10]) for date in dates for i in range(0,len(venue_ids),10)]
        state['expected_tasks'] = len(tasks)
        for date, ids in tasks:
            key = date+'-'+str(ids[0])
            if key in state['tasks']:
                continue
            # Cache full studio profiles individually; avoid one ever-growing enrichment blob.
            profiles = {}
            known_countries=state.setdefault('venue_countries',{})
            missing_profiles = [i for i in ids if not (generation/'venues'/f'{i}.json.gz').exists()]
            if profile_loader and missing_profiles:
                for studio in profile_loader(missing_profiles):
                    if studio.get('studio_id') in ids and studio.get('raw_profile'):
                        write_gzip(generation/'venues'/f"{studio['studio_id']}.json.gz", studio)
                        profile_cache_hits += 1
            for identity in ids:
                if str(identity) in known_countries and known_countries[str(identity)] != ISO2[market['country_code']]:
                    continue
                path = generation/'venues'/f'{identity}.json.gz'
                if path.exists():
                    profile = json.loads(gzip.decompress(path.read_bytes()))['raw_profile']
                else:
                    profile_source_requests += 1
                    profile = client.json('GET', f'/_api/v2/venues/{identity}')
                    if profile.get('id') != identity:
                        raise CollectionError('Studio profile identity mismatch')
                    studio = studio_record(identity, directory=state['venues'][str(identity)], profile=profile,
                                           checked_at=datetime.now(timezone.utc).isoformat())
                    write_gzip(path, studio)
                profiles[identity] = profile
                known_countries[str(identity)]=country_of(profile,markets)
                save(state_path,state)
            rows, excluded, pages, source_venues = {}, {}, 0, {}
            eligible=[i for i in ids if i in profiles and country_of(profiles[i],markets)==ISO2[market['country_code']]]
            unknown=[i for i in ids if i in profiles and country_of(profiles[i],markets) is None]
            if unknown:
                excluded['unknown_or_conflicting_country']=len(unknown)
            for items in schedule_pages(client, eligible, date) if eligible else []:
                pages += 1
                for source in items:
                    item = dict(source)
                    venue = dict(profiles[source['venue']['id']])
                    venue.update({k:v for k,v in source['venue'].items() if v not in (None, '', {}, [])})
                    address = dict(profiles[source['venue']['id']].get('address') or {})
                    address.update({k:v for k,v in (source['venue'].get('address') or {}).items() if v})
                    venue['address'] = address
                    # Preserve verified profile geography when schedule metadata is sparse.
                    location = dict(profiles[source['venue']['id']].get('location') or {})
                    location.update(source['venue'].get('location') or {})
                    venue['location'] = location
                    venue = merge_venue_metadata(venue, profiles[source['venue']['id']])
                    item['venue'] = venue
                    source_venues[source['venue']['id']] = source['venue']
                    observed = datetime.now(timezone.utc)
                    row, reason = parse_global(item, market, markets, start, end, observed)
                    if row:
                        rows[row['event_id']] = {'event':row, 'details':session_details(source,observed.isoformat()),
                                                'class_details':class_details(source.get('class') or {}),
                                                'raw_source':source, 'observed_at':observed.isoformat(),
                                                'venue_source_id':source['venue']['id']}
                    else:
                        excluded[reason] = excluded.get(reason,0)+1
            for identity, source_venue in source_venues.items():
                path = generation/'venues'/f'{identity}.json.gz'
                studio = json.loads(gzip.decompress(path.read_bytes()))
                write_gzip(path,studio_record(identity,schedule=source_venue,previous=studio))
            write_gzip(generation/'tasks'/f'{key}.json.gz', list(rows.values()))
            state['tasks'][key] = {'rows':len(rows), 'pages':pages}
            for reason,count in excluded.items():
                state['excluded'][reason] = state['excluded'].get(reason,0)+count
            save(state_path,state)
        gaps = any(state['excluded'].get(k) for k in ('unknown_or_conflicting_country','missing_required_fields'))
        state['status'] = 'complete' if not gaps and state['expected'] < 10000 else 'partial'
        state['retryable'] = False if gaps or state['expected'] >= 10000 else state['status'] != 'complete'
        if market.get('country_wide_fallback') and not any(c==ISO2[market['country_code']] for c in state.get('venue_countries',{}).values()):
            state.update(status='partial',retryable=False,error='Country-labelled route has no verified domestic studios; country coverage remains unresolved')
        state['expected_tasks'] = len(tasks)
    except CollectionError as exc:
        state.update(status='partial' if isinstance(exc,RetryableError) else 'failed', error=str(exc),
                     retryable=isinstance(exc,RetryableError))
        if isinstance(exc,RetryableError) and exc.cooldown:
            state['retry_not_before'] = (datetime.now(timezone.utc)+timedelta(seconds=exc.cooldown)).isoformat()
    finally:
        save(state_path,state)
        client.session.close()
    summary = {k:state.get(k) for k in ('market_id','country','window_start','window_end','status','error',
                                       'retryable','expected','expected_tasks','excluded','generation')}
    summary.update(market=market['name'], venues_discovered=len(state['venues']),
                   completed_tasks=len(state['tasks']), verified_rows=sum(t['rows'] for t in state['tasks'].values()),
                   requests=client.count, database_writes=0,
                   profile_cache_hits=profile_cache_hits, profile_source_requests=profile_source_requests,
                   coverage='Published market footprint only; not proof of entire-country or worldwide completion')
    save(root/'summary.json',summary)
    return summary

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--market', required=True, type=int)
    parser.add_argument('--max-requests', type=int, default=150)
    args = parser.parse_args()
    if not 1 <= args.max_requests <= 150:
        parser.error('Maximum 150 requests per bounded invocation')
    all_markets = json.loads(Path(__file__).with_name('global_markets.json').read_text(encoding='utf-8'))
    market = next(m for m in all_markets if m['id'] == args.market)
    summary = collect(market,{m['id']:m['country_code'] for m in all_markets},Path('output/global')/str(args.market),args.max_requests)
    print(json.dumps(summary))
    if summary['status'] != 'complete':
        raise SystemExit(2)

if __name__ == '__main__':
    main()

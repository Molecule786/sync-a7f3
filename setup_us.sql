-- Additive US expansion; existing pilot function and records remain usable.
begin;
create table public.classpass_us_runs (
 run_key text primary key, market_id integer not null,
 started_at timestamptz not null default now(), finished_at timestamptz,
 status text not null check(status in ('writing','complete','partial','failed')),
 processed integer not null default 0, inserted integer not null default 0, updated integer not null default 0,
 stats jsonb not null default '{}'
);
create table public.classpass_us_batches (
 run_key text references public.classpass_us_runs(run_key), batch integer,
 payload_hash text not null, processed integer not null, inserted integer not null, updated integer not null,
 primary key(run_key,batch)
);
alter table public.classpass_us_runs enable row level security;
alter table public.classpass_us_batches enable row level security;
revoke all on public.classpass_us_runs,public.classpass_us_batches from public,anon,authenticated;
grant select,insert,update on public.classpass_us_runs to service_role;
grant select,insert on public.classpass_us_batches to service_role;
create function public.sync_classpass_us(p_rows jsonb, p_market_id integer, p_run_key text, p_batch integer, p_final boolean, p_stats jsonb) returns jsonb
language plpgsql security invoker set search_path = '' as $$
declare
  item jsonb;
  old_row public.events;
  n_insert integer := 0;
  n_update integer := 0;
  n_processed integer := 0;
  prior public.classpass_us_batches;
begin
  if p_rows is null or jsonb_typeof(p_rows) <> 'array' or jsonb_array_length(p_rows) not between 0 and 500 then
    raise exception 'Expected up to 500 verified US events';
  end if;
  -- Transaction-scoped lock prevents overlapping pilot merges.
  perform pg_advisory_xact_lock(724903617);
  if p_market_id <> all(array[1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,32,34,43,44,45,46,47,49,50,59])
     or p_market_id is null or p_run_key is null or p_run_key !~ '^[A-Za-z0-9_.:-]{1,100}$'
     or p_batch is null or p_batch<0 or p_final is null
     or coalesce(p_stats->>'status','') not in ('complete','partial','failed')
     or (jsonb_array_length(p_rows)=0 and not p_final) then
    raise exception 'Invalid US run metadata';
  end if;
  if pg_database_size(current_database()) >= 350*1024*1024 then
    raise exception 'Free-plan storage guard reached (350 MiB); no US writes';
  end if;
  select * into prior from public.classpass_us_batches where run_key=p_run_key and batch=p_batch;
  if found then
    if prior.payload_hash <> md5(p_rows::text) then raise exception 'Batch identity collision'; end if;
    return jsonb_build_object('processed',prior.processed,'inserted',prior.inserted,'updated',prior.updated,'target','events');
  end if;
  insert into public.classpass_us_runs(run_key,market_id,status,stats)
  values(p_run_key,p_market_id,'writing',p_stats)
  on conflict(run_key) do nothing;
  if (select market_id from public.classpass_us_runs where run_key=p_run_key) <> p_market_id then raise exception 'Market collision'; end if;
  for item in select value from jsonb_array_elements(p_rows) loop
    if item->>'source' is distinct from 'classpass'
       or coalesce(item->>'event_id','') !~ '^classpass:[0-9]+$'
       or coalesce(btrim(item->>'title'),'') = ''
       or item->>'country' is distinct from 'US'
       or coalesce(btrim(item->>'city'),'') = ''
       or coalesce(btrim(item->>'city_slug'),'') = ''
       or coalesce(item->>'region','') <> all(string_to_array('AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC PR VI GU AS MP',' '))
       or coalesce(item->>'url','') !~ ('^https://classpass[.]com/classes/[^/]+/[^/]+/' || substring(item->>'event_id' from 11) || '/?$')
       or item->>'starts_at' is null or item->>'ends_at' is null then
      raise exception 'Invalid ClassPass US record';
    end if;
    if (item->>'ends_at')::timestamptz <= (item->>'starts_at')::timestamptz
       or (item->>'starts_at')::timestamptz < now() - interval '5 minutes'
       or (item->>'starts_at')::timestamptz > now() + interval '7 days' then
      raise exception 'Invalid or stale class time';
    end if;
    select * into old_row from public.events where event_id=item->>'event_id';
    if found and old_row.source <> 'classpass' then
      raise exception 'Source collision';
    end if;
    if found then n_update := n_update+1; else n_insert := n_insert+1; end if;
    insert into public.events (
      event_id,title,organizer,description,starts_at,ends_at,venue,street,zip,
      city,city_slug,region,country,category,url,source,updated_at
    ) values (
      item->>'event_id',item->>'title',nullif(item->>'organizer',''),nullif(item->>'description',''),
      (item->>'starts_at')::timestamptz,(item->>'ends_at')::timestamptz,
      nullif(item->>'venue',''),nullif(item->>'street',''),nullif(item->>'zip',''),
      item->>'city',item->>'city_slug',item->>'region','US','fitness',item->>'url','classpass',now()
    ) on conflict (event_id) do update set
      title=excluded.title,
      organizer=coalesce(excluded.organizer,events.organizer),
      description=coalesce(excluded.description,events.description),
      starts_at=excluded.starts_at,ends_at=excluded.ends_at,
      venue=coalesce(excluded.venue,events.venue),
      street=coalesce(excluded.street,events.street),
      zip=coalesce(excluded.zip,events.zip),
      city=excluded.city,city_slug=excluded.city_slug,region=excluded.region,
      country=excluded.country,category=excluded.category,url=excluded.url,updated_at=now();
    if (item->>'lat') is not null or (item->>'lon') is not null then
      if (item->>'lat')::float8 not between -90 and 90 or (item->>'lon')::float8 not between -180 and 180 then
        raise exception 'Invalid coordinates';
      end if;
      update public.events set lat=coalesce((item->>'lat')::float8,lat),lon=coalesce((item->>'lon')::float8,lon) where event_id=item->>'event_id';
    end if;
    -- Preserve is_hidden, created_at, price and all fields unavailable in the source.
    insert into public.classpass_event_freshness(event_id,last_seen_at)
    values(item->>'event_id',now())
    on conflict(event_id) do update set last_seen_at=excluded.last_seen_at;
    n_processed := n_processed+1;
  end loop;
  if n_processed>0 then
    insert into public.classpass_sync_runs(processed,inserted,updated,coverage)
    values(n_processed,n_insert,n_update,'US seven-day collection: market '||p_market_id||', run '||p_run_key);
  end if;
  insert into public.classpass_us_batches(run_key,batch,payload_hash,processed,inserted,updated)
  values(p_run_key,p_batch,md5(p_rows::text),n_processed,n_insert,n_update);
  update public.classpass_us_runs set processed=processed+n_processed,inserted=inserted+n_insert,updated=updated+n_update,
    status=case when p_final then p_stats->>'status' else 'writing' end,
    finished_at=case when p_final then now() else null end,stats=p_stats where run_key=p_run_key;
  return jsonb_build_object('processed',n_processed,'inserted',n_insert,'updated',n_update,'target','events');
end $$;
revoke all on function public.sync_classpass_us(jsonb,integer,text,integer,boolean,jsonb) from public, anon, authenticated;
grant execute on function public.sync_classpass_us(jsonb,integer,text,integer,boolean,jsonb) to service_role;
commit;

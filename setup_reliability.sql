-- Reliability migration v1: no deletions, no cancellation inference, no new public access.
begin;
alter table public.classpass_event_freshness add column source_changed_at timestamptz;
alter table public.classpass_us_runs add column unchanged integer not null default 0;
alter table public.classpass_us_batches add column unchanged integer not null default 0;
create table public.classpass_collection_checkpoints (
 market_id integer primary key check(market_id in (50,34,45)),
 snapshot jsonb not null, updated_at timestamptz not null default now()
);
create table public.classpass_market_events (
 market_id integer not null, event_id text not null references public.events(event_id),
 last_seen_at timestamptz not null,last_seen_snapshot text not null,
 missing_checks integer not null default 0,last_missing_snapshot text,last_missing_at timestamptz,
 primary key(market_id,event_id)
);
create index classpass_market_events_event_idx on public.classpass_market_events(event_id);
alter table public.classpass_collection_checkpoints enable row level security;
alter table public.classpass_market_events enable row level security;
revoke all on public.classpass_collection_checkpoints,public.classpass_market_events from public,anon,authenticated;
grant select,insert,update on public.classpass_collection_checkpoints,public.classpass_market_events to service_role;
create function public.classpass_checkpoint_read(p_market_id integer) returns jsonb
language sql security invoker set search_path='' as $$
 select snapshot from public.classpass_collection_checkpoints where market_id=p_market_id;
$$;
create function public.classpass_checkpoint_save(p_market_id integer,p_snapshot jsonb) returns jsonb
language plpgsql security invoker set search_path='' as $$
begin
 if p_market_id not in (50,34,45) or p_market_id is null
    or p_snapshot is null or jsonb_typeof(p_snapshot)<>'object'
    or (p_snapshot->>'market_id')::integer is distinct from p_market_id
    or (p_snapshot->>'version')::integer is distinct from 1
    or coalesce(p_snapshot->>'status','') not in ('running','collected','failed','partial','complete')
    or (p_snapshot->>'window_start')::timestamptz is null
    or (p_snapshot->>'window_start')::timestamptz > now()+interval '1 minute'
    or octet_length(p_snapshot::text)>8*1024*1024 then raise exception 'Invalid checkpoint'; end if;
 if pg_database_size(current_database())>=350*1024*1024 then raise exception 'Storage guard reached'; end if;
 insert into public.classpass_collection_checkpoints(market_id,snapshot)
 values(p_market_id,p_snapshot) on conflict(market_id) do update set snapshot=excluded.snapshot,updated_at=now()
 where (classpass_collection_checkpoints.snapshot->>'window_start')::timestamptz
       <=(excluded.snapshot->>'window_start')::timestamptz;
 if not found then raise exception 'Newer collection already exists'; end if;
 return jsonb_build_object('saved',true);
end $$;
revoke all on function public.classpass_checkpoint_read(integer),public.classpass_checkpoint_save(integer,jsonb) from public,anon,authenticated;
grant execute on function public.classpass_checkpoint_read(integer),public.classpass_checkpoint_save(integer,jsonb) to service_role;
create or replace function public.sync_classpass_us(p_rows jsonb, p_market_id integer, p_run_key text, p_batch integer, p_final boolean, p_stats jsonb) returns jsonb
language plpgsql security invoker set search_path = '' as $$
declare
  item jsonb;
  old_row public.events;
  n_insert integer := 0;
  n_update integer := 0;
  n_processed integer := 0;
  prior public.classpass_us_batches;
  n_unchanged integer := 0;
  observed_at timestamptz;
  scope_start timestamptz;
  scope_end timestamptz;
  scope_count integer;
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
    return jsonb_build_object('processed',prior.processed,'inserted',prior.inserted,'updated',prior.updated,'target','events','unchanged',prior.unchanged);
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
    if found and old_row.source is distinct from 'classpass' then raise exception 'Source collision'; end if;
    observed_at := coalesce((item->>'_observed_at')::timestamptz,now());
    if observed_at > now()+interval '1 minute' or observed_at < now()-interval '12 hours' then
      raise exception 'Invalid observation timestamp';
    end if;
    if not found then n_insert := n_insert+1; end if;
    insert into public.events (
      event_id,title,organizer,description,starts_at,ends_at,venue,street,zip,
      city,city_slug,region,country,category,url,source,lat,lon,updated_at
    ) values (
      item->>'event_id',item->>'title',nullif(item->>'organizer',''),nullif(item->>'description',''),
      (item->>'starts_at')::timestamptz,(item->>'ends_at')::timestamptz,
      nullif(item->>'venue',''),nullif(item->>'street',''),nullif(item->>'zip',''),
      item->>'city',item->>'city_slug',item->>'region','US','fitness',item->>'url','classpass',(item->>'lat')::float8,(item->>'lon')::float8,now()
    ) on conflict (event_id) do update set
      title=excluded.title,
      organizer=coalesce(excluded.organizer,events.organizer),
      description=coalesce(excluded.description,events.description),
      starts_at=excluded.starts_at,ends_at=excluded.ends_at,
      venue=coalesce(excluded.venue,events.venue),
      street=coalesce(excluded.street,events.street),
      zip=coalesce(excluded.zip,events.zip),
      city=excluded.city,city_slug=excluded.city_slug,region=excluded.region,
      country=excluded.country,category=excluded.category,url=excluded.url,
      lat=coalesce(excluded.lat,events.lat),lon=coalesce(excluded.lon,events.lon),updated_at=now()
    where row(events.title,events.organizer,events.description,events.starts_at,events.ends_at,
              events.venue,events.street,events.zip,events.city,events.city_slug,events.region,
              events.country,events.category,events.url,events.lat,events.lon)
      is distinct from row(excluded.title,coalesce(excluded.organizer,events.organizer),
              coalesce(excluded.description,events.description),excluded.starts_at,excluded.ends_at,
              coalesce(excluded.venue,events.venue),coalesce(excluded.street,events.street),
              coalesce(excluded.zip,events.zip),excluded.city,excluded.city_slug,excluded.region,
              excluded.country,excluded.category,excluded.url,coalesce(excluded.lat,events.lat),
              coalesce(excluded.lon,events.lon));
    if found then
      if old_row.event_id is not null then n_update := n_update+1; end if;
      insert into public.classpass_event_freshness(event_id,last_seen_at,source_changed_at)
      values(item->>'event_id',observed_at,now()) on conflict(event_id) do update
      set source_changed_at=excluded.source_changed_at;
    else n_unchanged := n_unchanged+1;
    end if;
    if (item->>'lat') is not null or (item->>'lon') is not null then
      if (item->>'lat')::float8 not between -90 and 90 or (item->>'lon')::float8 not between -180 and 180 then
        raise exception 'Invalid coordinates';
      end if;
    end if;
    -- Preserve is_hidden, created_at, price and all fields unavailable in the source.
    insert into public.classpass_event_freshness(event_id,last_seen_at)
    values(item->>'event_id',observed_at)
    on conflict(event_id) do update set last_seen_at=greatest(classpass_event_freshness.last_seen_at,excluded.last_seen_at);
    insert into public.classpass_market_events(market_id,event_id,last_seen_at,last_seen_snapshot)
    values(p_market_id,item->>'event_id',observed_at,p_stats->>'window_start')
    on conflict(market_id,event_id) do update set
      last_seen_at=greatest(classpass_market_events.last_seen_at,excluded.last_seen_at),
      last_seen_snapshot=excluded.last_seen_snapshot,missing_checks=0,last_missing_snapshot=null;
    n_processed := n_processed+1;
  end loop;
  if n_processed>0 then
    insert into public.classpass_sync_runs(processed,inserted,updated,coverage)
    values(n_processed,n_insert,n_update,'US seven-day collection: market '||p_market_id||', run '||p_run_key);
  end if;
  insert into public.classpass_us_batches(run_key,batch,payload_hash,processed,inserted,updated,unchanged)
  values(p_run_key,p_batch,md5(p_rows::text),n_processed,n_insert,n_update,n_unchanged);
  update public.classpass_us_runs set processed=processed+n_processed,inserted=inserted+n_insert,updated=updated+n_update,
    unchanged=unchanged+n_unchanged,
    status=case when p_final then p_stats->>'status' else 'writing' end,
    finished_at=case when p_final then now() else null end,stats=p_stats where run_key=p_run_key;
  if p_final and p_stats->>'status'='complete' then
    scope_start := (p_stats->>'window_start')::timestamptz;
    scope_end := (p_stats->>'window_end')::timestamptz;
    if scope_start is null or scope_end is null or scope_end-scope_start<>interval '7 days' then
      raise exception 'Invalid reconciliation window';
    end if;
    -- A fresh complete snapshot is evidence of absence only for previously mapped rows,
    -- inside the checked future window. Never infer cancellation or change is_hidden.
    -- A resumed snapshot uses its original observation window and cannot count twice.
    update public.classpass_market_events m set missing_checks=m.missing_checks+1,
      last_missing_snapshot=p_stats->>'window_start',last_missing_at=now()
    from public.events e
    where m.market_id=p_market_id and e.event_id=m.event_id and e.source='classpass'
      and e.starts_at>=greatest(scope_start,now()) and e.starts_at<scope_end
      and m.last_seen_snapshot is distinct from p_stats->>'window_start'
      and m.last_seen_at<scope_start
      and m.last_missing_snapshot is distinct from p_stats->>'window_start'
      and (m.last_missing_at is null or m.last_missing_at<scope_start-interval '6 hours');
  end if;
  return jsonb_build_object('processed',n_processed,'inserted',n_insert,'updated',n_update,'target','events','unchanged',n_unchanged);
end $$;

create view public.classpass_event_status with(security_invoker=true) as
select e.event_id,e.title,e.starts_at,e.ends_at,e.is_hidden,f.last_seen_at,f.source_changed_at,
 case when e.is_hidden then 'hidden'
      when e.ends_at<=now() then 'ended'
      when e.starts_at<=now() then 'started'
      when m.missing_checks>=2 then 'unverified_missing'
      when f.last_seen_at is null or f.last_seen_at<now()-interval '36 hours' then 'stale'
      else 'listed' end as listing_status,
 (not coalesce(e.is_hidden,false) and e.starts_at>now()) as is_upcoming,
 'unknown'::text as booking_availability
from public.events e left join public.classpass_event_freshness f using(event_id)
left join (select event_id,min(missing_checks) missing_checks from public.classpass_market_events group by event_id) m using(event_id)
where e.source='classpass';
revoke all on public.classpass_event_status from public,anon,authenticated;
grant select on public.classpass_event_status to service_role;
commit;

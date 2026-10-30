-- Production pilot: only ClassPass NYC rows, maximum ten per batch.
-- No deletes; missing classes are not inferred to be cancellations.
begin;
create table public.classpass_sync_runs (
 id uuid primary key default gen_random_uuid(),
 finished_at timestamptz not null default now(),
 processed integer not null, inserted integer not null, updated integer not null,
 coverage text not null default 'Limited pilot: current public day of two NYC studios; maximum ten classes'
);
create table public.classpass_event_freshness (
 event_id text primary key references public.events(event_id),
 last_seen_at timestamptz not null
);
alter table public.classpass_sync_runs enable row level security;
alter table public.classpass_event_freshness enable row level security;
revoke all on public.classpass_sync_runs, public.classpass_event_freshness from public, anon, authenticated;
grant select, insert on public.classpass_sync_runs to service_role;
grant select, insert, update on public.classpass_event_freshness to service_role;
create function public.sync_classpass_events(p_rows jsonb) returns jsonb
language plpgsql security invoker set search_path = '' as $$
declare
  item jsonb;
  old_row public.events;
  n_insert integer := 0;
  n_update integer := 0;
  n_processed integer := 0;
begin
  if p_rows is null or jsonb_typeof(p_rows) <> 'array' or jsonb_array_length(p_rows) not between 1 and 10 then
    raise exception 'Expected 1 to 10 verified pilot events';
  end if;
  -- Transaction-scoped lock prevents overlapping pilot merges.
  perform pg_advisory_xact_lock(724903617);
  for item in select value from jsonb_array_elements(p_rows) loop
    if item->>'source' is distinct from 'classpass'
       or coalesce(item->>'event_id','') !~ '^classpass:[0-9]+$'
       or coalesce(btrim(item->>'title'),'') = ''
       or item->>'country' is distinct from 'US'
       or item->>'city_slug' is distinct from 'new-york'
       or coalesce(item->>'url','') !~ ('^https://classpass[.]com/classes/[^/]+/[^/]+/' || substring(item->>'event_id' from 11) || '/?$')
       or item->>'starts_at' is null or item->>'ends_at' is null then
      raise exception 'Invalid ClassPass pilot record';
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
    -- Preserve is_hidden, created_at, price and all fields unavailable in the source.
    insert into public.classpass_event_freshness(event_id,last_seen_at)
    values(item->>'event_id',now())
    on conflict(event_id) do update set last_seen_at=excluded.last_seen_at;
    n_processed := n_processed+1;
  end loop;
  insert into public.classpass_sync_runs(processed,inserted,updated) values(n_processed,n_insert,n_update);
  return jsonb_build_object('processed',n_processed,'inserted',n_insert,'updated',n_update,'target','events');
end $$;
revoke all on function public.sync_classpass_events(jsonb) from public, anon, authenticated;
grant execute on function public.sync_classpass_events(jsonb) to service_role;
commit;

-- Isolated pilot only. Does not modify public.events or its existing rows.
begin;
create table public.classpass_events_test (like public.events including all);
alter table public.classpass_events_test enable row level security;
revoke all on public.classpass_events_test from public, anon, authenticated;
grant select, insert, update on public.classpass_events_test to service_role;

create table public.classpass_test_runs (
  id uuid primary key default gen_random_uuid(),
  finished_at timestamptz not null default now(),
  processed integer not null,
  inserted integer not null,
  updated integer not null
);
alter table public.classpass_test_runs enable row level security;
revoke all on public.classpass_test_runs from public, anon, authenticated;
grant select, insert on public.classpass_test_runs to service_role;

create function public.sync_classpass_test(p_rows jsonb) returns jsonb
language plpgsql security invoker set search_path = '' as $$
declare
  item jsonb;
  old_row public.classpass_events_test;
  n_insert integer := 0;
  n_update integer := 0;
  n_processed integer := 0;
begin
  if jsonb_typeof(p_rows) <> 'array' or jsonb_array_length(p_rows) not between 1 and 10 then
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
       or coalesce(item->>'url','') !~ '^https://classpass[.]com/classes/'
       or item->>'starts_at' is null or item->>'ends_at' is null then
      raise exception 'Invalid ClassPass pilot record';
    end if;
    if (item->>'ends_at')::timestamptz <= (item->>'starts_at')::timestamptz
       or (item->>'starts_at')::timestamptz < now() - interval '5 minutes'
       or (item->>'starts_at')::timestamptz > now() + interval '7 days' then
      raise exception 'Invalid or stale class time';
    end if;
    select * into old_row from public.classpass_events_test where event_id=item->>'event_id';
    if found and old_row.source <> 'classpass' then
      raise exception 'Source collision';
    end if;
    if found then n_update := n_update+1; else n_insert := n_insert+1; end if;
    insert into public.classpass_events_test (
      event_id,title,organizer,description,starts_at,ends_at,venue,street,zip,
      city,city_slug,region,country,category,url,source,updated_at
    ) values (
      item->>'event_id',item->>'title',nullif(item->>'organizer',''),nullif(item->>'description',''),
      (item->>'starts_at')::timestamptz,(item->>'ends_at')::timestamptz,
      nullif(item->>'venue',''),nullif(item->>'street',''),nullif(item->>'zip',''),
      item->>'city',item->>'city_slug',item->>'region','US','fitness',item->>'url','classpass',now()
    ) on conflict (event_id) do update set
      title=excluded.title,
      organizer=coalesce(excluded.organizer,classpass_events_test.organizer),
      description=coalesce(excluded.description,classpass_events_test.description),
      starts_at=excluded.starts_at,ends_at=excluded.ends_at,
      venue=coalesce(excluded.venue,classpass_events_test.venue),
      street=coalesce(excluded.street,classpass_events_test.street),
      zip=coalesce(excluded.zip,classpass_events_test.zip),
      city=excluded.city,city_slug=excluded.city_slug,region=excluded.region,
      country=excluded.country,category=excluded.category,url=excluded.url,updated_at=now();
    -- Preserve is_hidden, created_at, price and all fields unavailable in the source.
    n_processed := n_processed+1;
  end loop;
  insert into public.classpass_test_runs(processed,inserted,updated) values(n_processed,n_insert,n_update);
  return jsonb_build_object('processed',n_processed,'inserted',n_insert,'updated',n_update,'target','classpass_events_test');
end $$;
revoke all on function public.sync_classpass_test(jsonb) from public, anon, authenticated;
grant execute on function public.sync_classpass_test(jsonb) to service_role;
commit;

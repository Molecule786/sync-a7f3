-- Additive isolated worldwide collection. Never writes to public.events.
begin;
create table public.venues_global_test (
 id uuid primary key default gen_random_uuid(),
 source text not null, source_id text not null, country char(2) not null,
 name text, city text, details jsonb not null default '{}',
 observed_at timestamptz not null, updated_at timestamptz not null default now(),
 unique(source,source_id)
);
create table public.events_global_test (like public.events including defaults);
alter table public.events_global_test add primary key(id), add unique(event_id),
 add column venue_id uuid references public.venues_global_test(id),
 add column details jsonb not null default '{}', add column observed_at timestamptz,
 add column origin_market integer;
create table public.directory_jobs_global_test (
 source text not null, market_id integer not null, country char(2) not null,
 compressed_state text, stats jsonb not null default '{}',
 updated_at timestamptz not null default now(), primary key(source,market_id)
);
alter table public.venues_global_test enable row level security;
alter table public.events_global_test enable row level security;
alter table public.directory_jobs_global_test enable row level security;
revoke all on public.venues_global_test,public.events_global_test,public.directory_jobs_global_test from public,anon,authenticated;
grant select,insert,update on public.venues_global_test,public.events_global_test,public.directory_jobs_global_test to service_role;

create function public.sync_directory_global_test(p_market integer,p_country text,p_venues jsonb,p_events jsonb)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare v jsonb; x jsonb; e jsonb; vid uuid; n integer:=0; nv integer:=0; ni integer:=0; nu integer:=0;
begin
 if p_market is null or p_market<=0 or p_country !~ '^[A-Z]{2}$'
 or jsonb_typeof(p_venues) is distinct from 'array' or jsonb_array_length(p_venues)>100
 or jsonb_typeof(p_events) is distinct from 'array' or jsonb_array_length(p_events)>250
 or octet_length(p_venues::text)+octet_length(p_events::text)>8388608 then
  raise exception 'Invalid global test batch';
 end if;
 if pg_database_size(current_database())+octet_length(p_venues::text)+octet_length(p_events::text)>367001600 then
  raise exception '350 MiB database guard: stop expansion before adding more data';
 end if;
 perform pg_advisory_xact_lock(724903618);
 for v in select value from jsonb_array_elements(p_venues) loop
  if coalesce(v->>'studio_id','') !~ '^[0-9]+$' then raise exception 'Invalid studio identity'; end if;
  insert into public.venues_global_test(source,source_id,country,name,city,details,observed_at)
  values('classpass',v->>'studio_id',p_country,v#>>'{details,name}',v#>>'{details,address,city}',v,
   coalesce((v->>'profile_checked_at')::timestamptz,now()))
  on conflict(source,source_id) do update set country=excluded.country,name=coalesce(excluded.name,venues_global_test.name),
   city=coalesce(excluded.city,venues_global_test.city),details=excluded.details,observed_at=excluded.observed_at,updated_at=now();
  nv:=nv+1;
 end loop;
 for x in select value from jsonb_array_elements(p_events) loop
  e:=x->'event';
  if e->>'source' is distinct from 'classpass' or coalesce(e->>'event_id','') !~ '^classpass:[0-9]+$'
   or e->>'country' is distinct from p_country or coalesce(btrim(e->>'title'),'')=''
   or coalesce(e->>'url','') !~ '^https://classpass[.]com/classes/'
   or split_part(e->>'event_id',':',2) is distinct from regexp_replace(e->>'url','^.*/','')
   or e->>'starts_at' is null or e->>'ends_at' is null
   or (e->>'ends_at')::timestamptz<=(e->>'starts_at')::timestamptz
   or (e->>'starts_at')::timestamptz>now()+interval '8 days'
   or x->>'observed_at' is null then raise exception 'Invalid global test event'; end if;
  if (e->>'starts_at')::timestamptz<now() then continue; end if;
  select id into vid from public.venues_global_test where source='classpass' and source_id=x->>'venue_source_id' and country=p_country;
  if vid is null then raise exception 'Unverified event venue/country'; end if;
  if exists(select 1 from public.events_global_test where event_id=e->>'event_id') then nu:=nu+1; else ni:=ni+1; end if;
  insert into public.events_global_test(event_id,title,organizer,description,starts_at,ends_at,venue,street,zip,
   city,city_slug,region,country,lat,lon,category,url,source,venue_id,details,observed_at,origin_market,updated_at)
  values(e->>'event_id',e->>'title',e->>'organizer',e->>'description',(e->>'starts_at')::timestamptz,
   (e->>'ends_at')::timestamptz,e->>'venue',e->>'street',e->>'zip',e->>'city',e->>'city_slug',e->>'region',p_country,
   (e->>'lat')::float8,(e->>'lon')::float8,'fitness',e->>'url','classpass',vid,
   jsonb_build_object('session',x->'details','class',x->'class_details','raw_class',x#>'{raw_source,class}',
    'raw_session',(x->'raw_source')-'venue'-'class'),(x->>'observed_at')::timestamptz,p_market,now())
  on conflict(event_id) do update set title=excluded.title,organizer=coalesce(excluded.organizer,events_global_test.organizer),
   description=coalesce(excluded.description,events_global_test.description),starts_at=excluded.starts_at,ends_at=excluded.ends_at,
   venue=excluded.venue,street=coalesce(excluded.street,events_global_test.street),zip=coalesce(excluded.zip,events_global_test.zip),
   city=excluded.city,city_slug=excluded.city_slug,region=excluded.region,country=excluded.country,
   lat=coalesce(excluded.lat,events_global_test.lat),lon=coalesce(excluded.lon,events_global_test.lon),url=excluded.url,
   venue_id=excluded.venue_id,details=excluded.details,observed_at=excluded.observed_at,origin_market=excluded.origin_market,updated_at=now();
  n:=n+1;
 end loop;
 return jsonb_build_object('target','events_global_test','processed',n,'venues',nv,'inserted',ni,'updated',nu);
end $$;
revoke all on function public.sync_directory_global_test(integer,text,jsonb,jsonb) from public,anon,authenticated;
grant execute on function public.sync_directory_global_test(integer,text,jsonb,jsonb) to service_role;
create function public.directory_global_test_health() returns jsonb
language sql security invoker set search_path='' as $$
 select jsonb_build_object('bytes',pg_database_size(current_database()),
  'can_collect',pg_database_size(current_database())<367001600);
$$;
revoke all on function public.directory_global_test_health() from public,anon,authenticated;
grant execute on function public.directory_global_test_health() to service_role;
commit;

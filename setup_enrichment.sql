-- Detailed ClassPass source snapshots. No new public grants or frontend changes.
begin;
create table public.classpass_studios (
 studio_id bigint primary key,market_ids integer[] not null default '{}', details jsonb not null default '{}',
 raw_directory jsonb,raw_schedule jsonb,raw_profile jsonb,
 profile_checked_at timestamptz,last_seen_at timestamptz not null default now()
);
create table public.classpass_classes (
 studio_id bigint references public.classpass_studios(studio_id),class_id bigint,
 details jsonb not null,raw_class jsonb not null,last_seen_at timestamptz not null default now(),
 primary key(studio_id,class_id)
);
create table public.classpass_session_details (
 event_id text primary key references public.events(event_id),studio_id bigint not null,class_id bigint not null,
 details jsonb not null,raw_session jsonb not null,observed_at timestamptz not null,
 source_changed_at timestamptz not null default now(),
 foreign key(studio_id,class_id) references public.classpass_classes(studio_id,class_id)
);
create index classpass_session_studio_idx on public.classpass_session_details(studio_id);
alter table public.classpass_studios enable row level security;
alter table public.classpass_classes enable row level security;
alter table public.classpass_session_details enable row level security;
revoke all on public.classpass_studios,public.classpass_classes,public.classpass_session_details from public,anon,authenticated;
grant select,insert,update on public.classpass_studios,public.classpass_classes,public.classpass_session_details to service_role;
create or replace function public.classpass_checkpoint_save(p_market_id integer,p_snapshot jsonb) returns jsonb
language plpgsql security invoker set search_path='' as $$
begin
 if p_market_id not in (50,34,45) or p_market_id is null
    or p_snapshot is null or jsonb_typeof(p_snapshot)<>'object'
    or (p_snapshot->>'market_id')::integer is distinct from p_market_id
    or coalesce((p_snapshot->>'version')::integer,0) not in (1,2)
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

create function public.classpass_cached_profiles(p_market_id integer) returns jsonb
language sql security invoker set search_path='' as $$
 select coalesce(jsonb_agg(jsonb_build_object('studio_id',s.studio_id,'raw_profile',s.raw_profile,
 'profile_checked_at',s.profile_checked_at)),'[]'::jsonb)
 from public.classpass_studios s where s.raw_profile is not null
 and s.profile_checked_at>now()-interval '7 days'
 and p_market_id=any(s.market_ids);
$$;
create function public.sync_classpass_enriched(p_rows jsonb,p_market_id integer,p_run_key text,
 p_batch integer,p_final boolean,p_stats jsonb,p_enrichment jsonb) returns jsonb
language plpgsql security invoker set search_path='' as $$
declare item jsonb;result jsonb;seen_at timestamptz;vid bigint;cid bigint;
begin
 if p_enrichment is null or jsonb_typeof(p_enrichment)<>'object'
 or jsonb_typeof(p_enrichment->'studios') is distinct from 'array'
 or jsonb_typeof(p_enrichment->'classes') is distinct from 'array'
 or jsonb_typeof(p_enrichment->'sessions') is distinct from 'array'
 or jsonb_array_length(p_enrichment->'studios')>500
 or jsonb_array_length(p_enrichment->'classes')>500
 or jsonb_array_length(p_enrichment->'sessions')<>jsonb_array_length(p_rows)
 or octet_length(p_enrichment::text)>8*1024*1024 then raise exception 'Invalid enrichment batch'; end if;
 if exists(select value->>'event_id' from jsonb_array_elements(p_rows)
 except select value->>'event_id' from jsonb_array_elements(p_enrichment->'sessions'))
 or exists(select value->>'event_id' from jsonb_array_elements(p_enrichment->'sessions')
 except select value->>'event_id' from jsonb_array_elements(p_rows)) then
 raise exception 'Enrichment event scope mismatch'; end if;
 result:=public.sync_classpass_us(p_rows,p_market_id,p_run_key,p_batch,p_final,p_stats);
 for item in select value from jsonb_array_elements(p_enrichment->'studios') loop
  vid:=(item->>'studio_id')::bigint;
  if vid is null or vid<=0 or jsonb_typeof(item->'details') is distinct from 'object' then raise exception 'Invalid studio'; end if;
  insert into public.classpass_studios(studio_id,market_ids,details,raw_directory,raw_schedule,raw_profile,profile_checked_at)
  values(vid,array[p_market_id],jsonb_strip_nulls(item->'details'),nullif(item->'raw_directory','null'),
   nullif(item->'raw_schedule','null'),nullif(item->'raw_profile','null'),(item->>'profile_checked_at')::timestamptz)
  on conflict(studio_id) do update set market_ids=array(select distinct x from unnest(classpass_studios.market_ids||excluded.market_ids) x),details=classpass_studios.details||jsonb_strip_nulls(excluded.details),
   raw_directory=coalesce(excluded.raw_directory,classpass_studios.raw_directory),
   raw_schedule=coalesce(excluded.raw_schedule,classpass_studios.raw_schedule),
   raw_profile=coalesce(excluded.raw_profile,classpass_studios.raw_profile),
   profile_checked_at=coalesce(excluded.profile_checked_at,classpass_studios.profile_checked_at),last_seen_at=now();
 end loop;
 for item in select value from jsonb_array_elements(p_enrichment->'classes') loop
  vid:=(item->>'studio_id')::bigint;cid:=(item->>'class_id')::bigint;
  if vid is null or cid is null or cid<=0 or jsonb_typeof(item->'raw_class') is distinct from 'object'
  or (item->'raw_class'->>'id')::bigint is distinct from cid then raise exception 'Invalid class identity'; end if;
  insert into public.classpass_classes(studio_id,class_id,details,raw_class)
  values(vid,cid,jsonb_strip_nulls(item->'details'),item->'raw_class')
  on conflict(studio_id,class_id) do update set details=classpass_classes.details||jsonb_strip_nulls(excluded.details),
   raw_class=excluded.raw_class,last_seen_at=now();
 end loop;
 for item in select value from jsonb_array_elements(p_enrichment->'sessions') loop
  vid:=(item->>'studio_id')::bigint;cid:=(item->>'class_id')::bigint;
  seen_at:=(item->>'observed_at')::timestamptz;
  if seen_at is null or seen_at>now()+interval '1 minute' or seen_at<now()-interval '12 hours'
  or jsonb_typeof(item->'raw_session') is distinct from 'object'
  or 'classpass:'||(item->'raw_session'->>'id') is distinct from item->>'event_id'
  or vid is null or cid is null then raise exception 'Invalid session evidence'; end if;
  insert into public.classpass_session_details(event_id,studio_id,class_id,details,raw_session,observed_at)
  values(item->>'event_id',vid,cid,item->'details',item->'raw_session',seen_at)
  on conflict(event_id) do update set studio_id=excluded.studio_id,class_id=excluded.class_id,
   details=excluded.details,raw_session=excluded.raw_session,observed_at=excluded.observed_at,
   source_changed_at=case when (classpass_session_details.details-'availability_observed_at')
      is distinct from (excluded.details-'availability_observed_at')
      or classpass_session_details.raw_session is distinct from excluded.raw_session
      or classpass_session_details.studio_id<>excluded.studio_id or classpass_session_details.class_id<>excluded.class_id
      then now() else classpass_session_details.source_changed_at end
   where classpass_session_details.observed_at<=excluded.observed_at;
 end loop;
 return result||jsonb_build_object('enriched',jsonb_array_length(p_enrichment->'sessions'));
end $$;
revoke all on function public.classpass_cached_profiles(integer),public.sync_classpass_enriched(jsonb,integer,text,integer,boolean,jsonb,jsonb) from public,anon,authenticated;
grant execute on function public.classpass_cached_profiles(integer),public.sync_classpass_enriched(jsonb,integer,text,integer,boolean,jsonb,jsonb) to service_role;
create view public.classpass_detailed_events with(security_invoker=true) as
select e.*,d.studio_id as classpass_studio_id,d.class_id as classpass_class_id,
 d.details->>'instructor_name' as instructor_name,d.details->>'instructor_id' as instructor_id,
 d.details->'duration_minutes' as duration_minutes,c.details->'activities' as class_activities,
 c.details->>'level' as class_level,c.details->'ratings' as class_ratings,
 d.details->'retail_price_in_credits' as retail_price_in_credits,
 d.details->'availability' as source_availability,d.details->>'availability_observed_at' as availability_observed_at,
 d.details->'is_livestream' as is_livestream,d.details->'tag_ids' as classpass_tag_ids,
 d.details->>'cancellation_policy_source_code' as cancellation_policy_source_code,
 s.details->>'description' as studio_description,s.details->>'profile_url' as studio_profile_url,
 s.details->>'phone' as studio_phone,s.details->>'website' as studio_website,
 s.details->>'timezone' as studio_timezone,s.details->'images' as studio_images,
 s.details->'amenities' as studio_amenities,s.details->'opening_hours' as studio_opening_hours,
 s.details->'rating_average' as studio_rating,s.details->'rating_count' as studio_rating_count,
 s.details->>'rating_count_display' as studio_rating_count_display,s.details->'social_links' as studio_social_links,
 s.details->>'how_to_get_there' as how_to_get_there,
 coalesce(c.details->>'what_to_bring',s.details->>'what_to_bring') as what_to_bring,
 s.details->>'when_to_arrive' as when_to_arrive,s.details->>'booking_window' as booking_window,
 s.details->'requirements' as studio_requirements,s.details->'safety_and_cleanliness' as safety_and_cleanliness,
 s.details->>'pro_tip' as studio_pro_tip,s.profile_checked_at,d.observed_at as details_observed_at,
 d.raw_session as raw_session_attributes,c.raw_class as raw_class_attributes,
 s.raw_schedule as raw_studio_schedule_attributes,s.raw_directory as raw_studio_directory_attributes,
 s.raw_profile as raw_studio_profile_attributes
from public.events e join public.classpass_session_details d using(event_id)
join public.classpass_classes c on c.studio_id=d.studio_id and c.class_id=d.class_id
join public.classpass_studios s on s.studio_id=d.studio_id where e.source='classpass';
revoke all on public.classpass_detailed_events from public,anon,authenticated;
grant select on public.classpass_detailed_events to service_role;
commit;

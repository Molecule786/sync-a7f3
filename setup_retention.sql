-- Source-confirmed retention. No new persistent tables or public access.
begin;
create or replace function public.directory_retention_cache_state()
returns jsonb language sql stable security invoker set search_path='' as $$
 select jsonb_build_object(
 'profiles',(select count(*) from public.venues_global_test),
 'fresh_profiles',(select count(*) from public.venues_global_test where observed_at>=now()-interval '20 hours'),
 'test_events',(select count(*) from public.events_global_test),
 'profile_hash',(select md5(coalesce(string_agg(md5(to_jsonb(v)::text),'' order by id),'')) from public.venues_global_test v),
 'jobs_hash',(select md5(coalesce(string_agg(md5(to_jsonb(j)::text),'' order by source,market_id),'')) from public.directory_jobs_global_test j));
$$;
create or replace function public.prune_directory_retention_cache(p_expected jsonb)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare state jsonb; profiles integer:=0; jobs integer:=0; original_count integer;
begin
 perform pg_advisory_xact_lock(724903618);
 lock table public.venues_global_test,public.events_global_test,public.directory_jobs_global_test in access exclusive mode;
 state:=public.directory_retention_cache_state();
 if state is distinct from p_expected then raise exception 'Cache changed since backup; retained';end if;
 -- Keep warm profiles exactly; referenced test caches are left alone.
 if (state->>'test_events')::integer=0 and
    (state->>'profiles')::integer>(state->>'fresh_profiles')::integer then
  create temporary table retention_profiles_copy on commit drop as
   select * from public.venues_global_test where observed_at>=now()-interval '20 hours';
  profiles:=(state->>'profiles')::integer-(select count(*) from pg_temp.retention_profiles_copy);
  truncate table public.events_global_test,public.venues_global_test;
  insert into public.venues_global_test select * from pg_temp.retention_profiles_copy;
 end if;
 -- Rebuild only the temporary job store to release old TOAST allocation.
 -- All rows, stats, countries and original timestamps survive unchanged.
 create temporary table retention_jobs_copy on commit drop as select * from public.directory_jobs_global_test;
 select count(*) into original_count from pg_temp.retention_jobs_copy;
 update pg_temp.retention_jobs_copy set compressed_state=null
 where compressed_state is not null
 and stats->>'window_start' is not null
 and (stats->>'window_start')::timestamptz<now()-interval '12 hours';
 get diagnostics jobs=row_count;
 if jobs>0 then
  truncate table public.directory_jobs_global_test;
  insert into public.directory_jobs_global_test select * from pg_temp.retention_jobs_copy;
 end if;
 if (select count(*) from public.directory_jobs_global_test)<>original_count then
  raise exception 'Checkpoint metadata count changed';end if;
 return jsonb_build_object('stale_profiles_removed',profiles,'expired_checkpoint_payloads_cleared',jobs,
  'checkpoint_metadata_retained',original_count,'database_bytes',pg_database_size(current_database()));
end $$;
create or replace function public.purge_source_confirmed_expired_events(p_items jsonb,p_cutoff timestamptz)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare x jsonb; old public.events; n integer:=0; conflicts integer:=0; evidence jsonb; expected jsonb;
begin
 if p_cutoff is null or p_cutoff>now()-interval '24 hours'
 or jsonb_typeof(p_items) is distinct from 'array' or jsonb_array_length(p_items)>25
 or octet_length(p_items::text)>8388608 then raise exception 'Invalid retention batch';end if;
 perform pg_advisory_xact_lock(724903619);
 for x in select value from jsonb_array_elements(p_items) loop
  expected:=x->'expected';evidence:=x->'evidence';
  select * into old from public.events where event_id=expected->>'event_id' for update;
  if not found then continue;end if;
  if to_jsonb(old) is distinct from expected then conflicts:=conflicts+1;continue;end if;
  if old.source not in ('classpass','active','opensports') or old.starts_at is null
  or old.ends_at is null or old.starts_at>=old.ends_at or old.ends_at>=p_cutoff
  or evidence->>'event_id' is distinct from old.event_id or evidence->>'source' is distinct from old.source
  or evidence->>'country' is distinct from old.country::text
  or evidence->>'original_url' is distinct from old.url
  or evidence->>'verified_at' is null or evidence->>'starts_at' is null or evidence->>'ends_at' is null
  or (evidence->>'verified_at')::timestamptz not between now()-interval '30 minutes' and now()+interval '5 minutes'
  or not ((evidence->>'starts_at')::timestamptz<(evidence->>'ends_at')::timestamptz
          and (evidence->>'ends_at')::timestamptz<p_cutoff)
  or (old.source='classpass' and coalesce(evidence->>'fetched_url','') !~ '^https://classpass[.]com/_api/v1/schedules/[0-9]+$')
  or (old.source='active' and coalesce(evidence->>'fetched_url','') !~ '^https://(www[.])?(active[.]com|activekids[.]com)/')
  or (old.source='opensports' and coalesce(evidence->>'fetched_url','') !~ '^https://(www[.])?opensports[.]net/posts/')
  or (old.source='classpass' and evidence->>'fetched_url' is distinct from
      'https://classpass.com/_api/v1/schedules/'||split_part(old.event_id,':',2))
  then raise exception 'Record lacks valid fresh source expiry evidence';end if;
  delete from public.classpass_market_events where event_id=old.event_id;
  delete from public.classpass_session_details where event_id=old.event_id;
  delete from public.classpass_event_freshness where event_id=old.event_id;
  delete from public.events where id=old.id;
  n:=n+1;
 end loop;
 return jsonb_build_object('deleted',n,'conflicts',conflicts);
end $$;
revoke all on function public.directory_retention_cache_state(),public.prune_directory_retention_cache(jsonb),
 public.purge_source_confirmed_expired_events(jsonb,timestamptz) from public,anon,authenticated;
grant execute on function public.directory_retention_cache_state(),public.prune_directory_retention_cache(jsonb),
 public.purge_source_confirmed_expired_events(jsonb,timestamptz) to service_role;
commit;

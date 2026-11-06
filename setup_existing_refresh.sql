-- Shared existing-event refresh; no tables, columns, inserts or venue writes.
begin;
create or replace function public.existing_event_refresh_candidates(p_source text,p_after text default '',p_limit integer default 500)
returns table(event_id text,country character(2)) language sql stable security invoker set search_path='' as $$
 select e.event_id,e.country from public.events e
 where p_source in ('active','opensports') and e.source=p_source and e.event_id>coalesce(p_after,'')
 and (e.ends_at>=now() or e.ends_at is null or e.starts_at>=e.ends_at)
 order by e.event_id limit least(greatest(p_limit,1),500);
$$;
revoke all on function public.existing_event_refresh_candidates(text,text,integer) from public,anon,authenticated;
grant execute on function public.existing_event_refresh_candidates(text,text,integer) to service_role;
create or replace function public.refresh_existing_directory_event(
 p_source text,p_event_id text,p_expected jsonb,p_patch jsonb,p_evidence jsonb
) returns jsonb language plpgsql security invoker set search_path='' as $$
declare old public.events; k text; assignments text;
 allowed text[]:=array['title','description','organizer','organizer_url','starts_at','ends_at',
 'venue','street','zip','city','region','lat','lon'];
begin
 if p_source not in ('active','opensports') or p_source is null
 or p_event_id not like p_source||':%' or jsonb_typeof(p_expected) is distinct from 'object'
 or jsonb_typeof(p_patch) is distinct from 'object' or octet_length(p_expected::text)>8388608
 or octet_length(p_patch::text)>1048576
 or p_evidence->>'event_id' is distinct from p_event_id
 or p_evidence->>'source' is distinct from p_source
 or p_evidence->>'verified_at' is null
 or (p_evidence->>'verified_at')::timestamptz not between now()-interval '45 minutes' and now()+interval '5 minutes'
 then raise exception 'Invalid existing-event refresh'; end if;
 select * into old from public.events where event_id=p_event_id for update;
 if not found then return jsonb_build_object('action','missing'); end if;
 if old.source is distinct from p_source or to_jsonb(old) is distinct from p_expected
 then return jsonb_build_object('action','conflict'); end if;
 if p_evidence->>'original_url' is distinct from old.url
 or p_evidence->>'fetched_url' is null
 or (p_source='active' and p_evidence->>'fetched_url' !~ '^https://(www[.])?(active[.]com|activekids[.]com)/')
 or (p_source='opensports' and p_evidence->>'fetched_url' !~ '^https://(www[.])?opensports[.]net/posts/')
 then raise exception 'Unexpected existing source URL'; end if;
 for k in select jsonb_object_keys(p_patch) loop
  if not(k=any(allowed)) or p_patch->k='null'::jsonb or btrim(p_patch->>k)=''
  then raise exception 'Disallowed or empty patch field: %',k; end if;
 end loop;
 if (p_patch ? 'starts_at' or p_patch ? 'ends_at') and
 coalesce((p_patch->>'ends_at')::timestamptz,old.ends_at)<=coalesce((p_patch->>'starts_at')::timestamptz,old.starts_at)
 then raise exception 'Invalid revised event time'; end if;
 if p_patch='{}'::jsonb then return jsonb_build_object('action','unchanged'); end if;
 select string_agg(format('%I=(jsonb_populate_record(NULL::public.events,$2)).%I',key,key),',')
 into assignments from jsonb_object_keys(p_patch) keys(key);
 execute 'update public.events set '||assignments||',updated_at=now() where id=$1' using old.id,p_patch;
 return jsonb_build_object('action','updated','fields',(select jsonb_agg(key) from jsonb_object_keys(p_patch) keys(key)));
end $$;
revoke all on function public.refresh_existing_directory_event(text,text,jsonb,jsonb,jsonb) from public,anon,authenticated;
grant execute on function public.refresh_existing_directory_event(text,text,jsonb,jsonb,jsonb) to service_role;

-- One-off cleanup, restricted to the exact approved historic cutoff.
create or replace function public.purge_approved_expired_events(p_expected jsonb,p_cutoff timestamptz)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare x jsonb; old public.events; n integer:=0; conflicts integer:=0;
begin
 if p_cutoff is null or p_cutoff>timestamptz '2026-11-06T05:12:13.351404Z'
 or jsonb_typeof(p_expected) is distinct from 'array' or jsonb_array_length(p_expected)>50
 or octet_length(p_expected::text)>8388608 then raise exception 'Invalid approved cleanup batch';end if;
 perform pg_advisory_xact_lock(724903619);
 for x in select value from jsonb_array_elements(p_expected) loop
  select * into old from public.events where event_id=x->>'event_id' for update;
  if not found then continue;end if;
  if to_jsonb(old) is distinct from x then conflicts:=conflicts+1;continue;end if;
  if old.source not in ('classpass','active','opensports') or old.starts_at is null
   or old.ends_at is null or old.starts_at>=old.ends_at or old.ends_at>=p_cutoff or old.ends_at>=now()
  then raise exception 'Record is not a valid approved ended event';end if;
  delete from public.classpass_market_events where event_id=old.event_id;
  delete from public.classpass_session_details where event_id=old.event_id;
  delete from public.classpass_event_freshness where event_id=old.event_id;
  delete from public.events where id=old.id;
  n:=n+1;
 end loop;
 return jsonb_build_object('deleted',n,'conflicts',conflicts);
end $$;
revoke all on function public.purge_approved_expired_events(jsonb,timestamptz) from public,anon,authenticated;
grant execute on function public.purge_approved_expired_events(jsonb,timestamptz) to service_role;
commit;

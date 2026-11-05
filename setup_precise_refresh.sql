-- No events columns, existing values, policies or unrelated-source rows are changed by installation.
begin;
create or replace function public.refresh_classpass_precise(p_items jsonb) returns jsonb
language plpgsql security invoker set search_path='' as $$
declare x jsonb; old public.events; patch jsonb; expected jsonb; proof jsonb;
 eid text; assignments text; k text; fields jsonb; audit jsonb:='[]';
 n_new integer:=0; n_changed integer:=0; n_same integer:=0; n_conflict integer:=0;
 allowed text[]:=array['title','organizer','organizer_url','description','starts_at','ends_at','venue',
  'street','zip','city','city_slug','region','country','lat','lon','url'];
 countries text[]:=array['US','CN','DE','JP','GB','IN','FR','RU','IT','BR','CA','KR','MX','AU','ES','TR',
 'ID','NL','SA','CH','PL','TW','BE','AR','IL','SE','IE','SG','AE','NO','AT','TH','CO','MY','VN','BD','PH','DK','ZA','RO'];
begin
 if p_items is null or jsonb_typeof(p_items)<>'array' or jsonb_array_length(p_items)>250
 or octet_length(p_items::text)>8388608 then raise exception 'Invalid refresh batch'; end if;
 perform pg_advisory_xact_lock(724903619);
 for x in select value from jsonb_array_elements(p_items) loop
  eid:=x->>'event_id'; patch:=x->'patch'; expected:=x->'expected_before'; proof:=x->'evidence';
  if coalesce(eid,'') !~ '^classpass:[0-9]+$' or jsonb_typeof(patch) is distinct from 'object'
   or proof->>'session_id' is distinct from split_part(eid,':',2)
   or proof->>'source' is distinct from 'classpass'
   or proof->>'verified_at' is null
   or (proof->>'verified_at')::timestamptz<now()-interval '12 hours'
   or (proof->>'verified_at')::timestamptz>now()+interval '5 minutes' then
   raise exception 'Unverified ClassPass session'; end if;
  for k in select jsonb_object_keys(patch) loop
   if not k=any(allowed) then raise exception 'Field is protected: %',k; end if;
   if patch->k='null'::jsonb or patch->>k='' then raise exception 'Missing source value cannot erase existing detail'; end if;
  end loop;
  select * into old from public.events where event_id=eid for update;
  if found then
   if old.source is distinct from 'classpass' or coalesce(old.country,'')<>all(countries) then raise exception 'Source or country collision'; end if;
   if expected is null or expected='null'::jsonb or to_jsonb(old) is distinct from expected then
    n_conflict:=n_conflict+1; audit:=audit||jsonb_build_array(jsonb_build_object('event_id',eid,'action','conflict_skipped')); continue;
   end if;
   if patch ? 'country' and patch->>'country' is distinct from old.country then raise exception 'Country mismatch requires review'; end if;
   -- Remove even equal-valued proposals server-side; no physical write or timestamp change for a no-op.
   select coalesce(jsonb_object_agg(key,value),'{}') into patch from jsonb_each(patch)
    where to_jsonb(old)->key is distinct from value;
   if patch='{}'::jsonb then
    insert into public.classpass_event_freshness(event_id,last_seen_at) values(eid,(proof->>'verified_at')::timestamptz)
     on conflict(event_id) do update set last_seen_at=greatest(classpass_event_freshness.last_seen_at,excluded.last_seen_at);
    n_same:=n_same+1; continue;
   end if;
   if (patch ? 'ends_at' or patch ? 'starts_at') and
    coalesce((patch->>'ends_at')::timestamptz,old.ends_at)<=coalesce((patch->>'starts_at')::timestamptz,old.starts_at)
    then raise exception 'Invalid revised session time'; end if;
   select string_agg(format('%I=(jsonb_populate_record(NULL::public.events,$2)).%I',key,key),',')
    into assignments from jsonb_object_keys(patch) as keys(key);
   execute 'update public.events set '||assignments||',updated_at=now() where id=$1' using old.id,patch;
   insert into public.classpass_event_freshness(event_id,last_seen_at,source_changed_at)
    values(eid,(proof->>'verified_at')::timestamptz,now()) on conflict(event_id) do update
    set last_seen_at=greatest(classpass_event_freshness.last_seen_at,excluded.last_seen_at),source_changed_at=excluded.source_changed_at;
   select jsonb_object_agg(key,to_jsonb(old)->key) into fields from jsonb_object_keys(patch) as keys(key);
   audit:=audit||jsonb_build_array(jsonb_build_object('event_id',eid,'action','updated','before',fields,'after',patch,
    'reason',x->'reasons','evidence',proof)); n_changed:=n_changed+1;
  else
   if expected is not null and expected<>'null'::jsonb then n_conflict:=n_conflict+1;continue;end if;
   if coalesce(patch->>'country','')<>all(countries) or coalesce(btrim(patch->>'title'),'')=''
    or patch->>'starts_at' is null or patch->>'ends_at' is null
    or (patch->>'starts_at')::timestamptz<now()-interval '5 minutes'
    or (patch->>'starts_at')::timestamptz>now()+interval '8 days'
    or (patch->>'ends_at')::timestamptz<=(patch->>'starts_at')::timestamptz
    or coalesce(patch->>'url','') !~ ('^https://classpass[.]com/classes/[^/]+/[^/]+/'||split_part(eid,':',2)||'/?$')
    then raise exception 'Invalid new upcoming ClassPass event';end if;
   if pg_database_size(current_database())>367001600 then raise exception '350 MiB storage guard prevents new inserts';end if;
   insert into public.events(event_id,title,organizer,organizer_url,description,starts_at,ends_at,venue,street,zip,
    city,city_slug,region,country,lat,lon,url,source,category)
   values(eid,patch->>'title',patch->>'organizer',patch->>'organizer_url',patch->>'description',
    (patch->>'starts_at')::timestamptz,(patch->>'ends_at')::timestamptz,patch->>'venue',patch->>'street',patch->>'zip',
    patch->>'city',patch->>'city_slug',patch->>'region',patch->>'country',(patch->>'lat')::float8,(patch->>'lon')::float8,
    patch->>'url','classpass','fitness');
   insert into public.classpass_event_freshness(event_id,last_seen_at,source_changed_at)
    values(eid,(proof->>'verified_at')::timestamptz,now()) on conflict(event_id) do update
    set last_seen_at=greatest(classpass_event_freshness.last_seen_at,excluded.last_seen_at),source_changed_at=excluded.source_changed_at;
   audit:=audit||jsonb_build_array(jsonb_build_object('event_id',eid,'action','inserted','reason','Verified new upcoming source session','evidence',proof));
   n_new:=n_new+1;
  end if;
 end loop;
 return jsonb_build_object('target','events','inserted',n_new,'updated',n_changed,'unchanged',n_same,
  'conflicts_skipped',n_conflict,'audit',audit);
end $$;
revoke all on function public.refresh_classpass_precise(jsonb) from public,anon,authenticated;
grant execute on function public.refresh_classpass_precise(jsonb) to service_role;
commit;

begin;
create or replace function public.sync_mindbody_record(p_kind text,p_identity text,p_patch jsonb,p_expected jsonb,p_evidence jsonb)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare old jsonb; k text; allowed text[]; tbl text; identity_col text; assignments text; cols text; vals text; affected integer; patch jsonb:=p_patch;
 countries text[]:=string_to_array('US CN DE JP GB IN FR RU IT BR CA KR MX AU ES TR ID NL SA CH PL TW BE AR IL SE IE SG AE NO AT TH CO MY VN BD PH DK ZA RO',' ');
begin
 if p_kind not in ('event','venue') or jsonb_typeof(patch) is distinct from 'object' or octet_length(patch::text)>131072
  or coalesce(p_evidence->>'country','')<>all(countries)
  or (p_evidence->>'verified_at') is null or (p_evidence->>'verified_at')::timestamptz<now()-interval '30 minutes'
  or (p_evidence->>'verified_at')::timestamptz>now()+interval '5 minutes' then raise exception 'Invalid Mindbody evidence';end if;
 if p_kind='venue' then
  if p_identity !~ '^mindbody:[0-9]+$' or p_identity is distinct from 'mindbody:'||(p_evidence->>'location_id') then raise exception 'Invalid studio identity';end if;
  tbl:='venues';identity_col:='venue_id';
  allowed:=array['name','description','street','zip','city','region','country','lat','lon','phone','website','email','logo_url','image_url','amenities','activities','url'];
 else
  if p_identity !~ '^mindbody:[0-9]+:[0-9]+:[0-9]+$' or p_identity is distinct from 'mindbody:'||(p_evidence->>'site_id')||':'||(p_evidence->>'mb_location_id')||':'||(p_evidence->>'class_id') then raise exception 'Invalid class identity';end if;
  tbl:='events';identity_col:='event_id';
  allowed:=array['title','organizer','organizer_url','description','starts_at','ends_at','venue','street','zip','city','region','country','lat','lon','url','price','currency','registration_status','subcategories'];
 end if;
 for k in select jsonb_object_keys(patch) loop
  if not k=any(allowed) or patch->k in ('null'::jsonb,'[]'::jsonb,'""'::jsonb) then raise exception 'Protected or empty field: %',k;end if;
 end loop;
 if patch->>'country' is distinct from p_evidence->>'country'
  or coalesce(patch->>'url','') !~ '^https://www[.]mindbodyonline[.]com/explore/(locations|fitness/classes)/[^/?#]+$'
  then raise exception 'Source geography/URL required';end if;
 if p_kind='event' and (patch->>'starts_at' is null or patch->>'ends_at' is null
  or (patch->>'ends_at')::timestamptz<=(patch->>'starts_at')::timestamptz) then raise exception 'Invalid session time';end if;
 perform pg_advisory_xact_lock(724903619);
 execute format('select to_jsonb(t) from public.%I t where %I=$1 for update',tbl,identity_col) into old using p_identity;
 if old is not null then
  if p_expected is null or old is distinct from p_expected then return jsonb_build_object('action','conflict_skipped');end if;
  if old->>'country' is distinct from patch->>'country' or (p_kind='event' and old->>'source' is distinct from 'mindbody') then raise exception 'Source/country collision';end if;
  if p_kind='venue' and coalesce((old->>'content_locked')::boolean,false) then return jsonb_build_object('action','locked_skipped');end if;
  if p_kind='venue' and coalesce((old->>'category_locked')::boolean,false) then patch:=patch-'activities';end if;
  select coalesce(jsonb_object_agg(key,value),'{}') into patch from jsonb_each(patch) where old->key is distinct from value;
  if patch='{}'::jsonb then return jsonb_build_object('action','unchanged');end if;
  select string_agg(format('%I=(jsonb_populate_record(NULL::public.%I,$2)).%I',key,tbl,key),',') into assignments from jsonb_object_keys(patch) as keys(key);
  execute format('update public.%I set %s,updated_at=now() where %I=$1',tbl,assignments,identity_col) using p_identity,patch;
  return jsonb_build_object('action','updated','fields',(select jsonb_agg(key) from jsonb_object_keys(patch) keys(key)));
 end if;
 if p_expected is not null and p_expected<>'null'::jsonb then return jsonb_build_object('action','conflict_skipped');end if;
 if pg_database_size(current_database())>419430400 then return jsonb_build_object('action','storage_guard_skipped');end if;
 if p_kind='event' then
  if coalesce(patch->>'title','')='' or (patch->>'starts_at')::timestamptz<now()-interval '5 minutes'
   or (patch->>'starts_at')::timestamptz>now()+interval '7 days 5 minutes' then raise exception 'New class outside collection window';end if;
  patch:=patch||jsonb_build_object('event_id',p_identity,'source','mindbody','category','fitness');
 else
  if coalesce(patch->>'name','')='' then raise exception 'Studio name missing';end if;
  patch:=patch||jsonb_build_object('venue_id',p_identity,'slug',replace(p_identity,':','-'),'category','fitness');
 end if;
 select string_agg(format('%I',key),','),string_agg(format('(jsonb_populate_record(NULL::public.%I,$1)).%I',tbl,key),',') into cols,vals from jsonb_object_keys(patch) keys(key);
 execute format('insert into public.%I(%s) select %s on conflict do nothing',tbl,cols,vals) using patch;
 get diagnostics affected=row_count;
 if affected=0 then return jsonb_build_object('action','conflict_skipped');end if;
 return jsonb_build_object('action','inserted');
end $$;
revoke all on function public.sync_mindbody_record(text,text,jsonb,jsonb,jsonb) from public,anon,authenticated;
grant execute on function public.sync_mindbody_record(text,text,jsonb,jsonb,jsonb) to service_role;

create or replace function public.save_mindbody_checkpoint(p_country text,p_stats jsonb) returns jsonb
language plpgsql security invoker set search_path='' as $$
declare countries text[]:=string_to_array('US CN DE JP GB IN FR RU IT BR CA KR MX AU ES TR ID NL SA CH PL TW BE AR IL SE IE SG AE NO AT TH CO MY VN BD PH DK ZA RO',' ');
begin
 if p_country !~ '^[A-Z]{2}$' or coalesce(p_stats->>'cursor','') !~ '^mindbody:[0-9]+$' or octet_length(p_stats::text)>16384 then raise exception 'Invalid Mindbody checkpoint';end if;
 if not p_country=any(countries) then raise exception 'Country outside scope';end if;
 insert into public.directory_jobs_global_test(source,market_id,country,stats) values('mindbody',array_position(countries,p_country),p_country,p_stats)
 on conflict(source,market_id) do update set stats=excluded.stats,country=excluded.country,updated_at=now();
 return jsonb_build_object('saved',true);
end $$;
revoke all on function public.save_mindbody_checkpoint(text,jsonb) from public,anon,authenticated;
grant execute on function public.save_mindbody_checkpoint(text,jsonb) to service_role;
commit;

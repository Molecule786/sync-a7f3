begin;
do $$
declare rowdata jsonb;enrichment jsonb;stats jsonb;result jsonb;value jsonb;original_price numeric;
begin
 select to_jsonb(e),price into rowdata,original_price from public.events e
 where source='classpass' and country='US' and starts_at>now()+interval '10 minutes'
 and starts_at<now()+interval '6 days' limit 1;
 if rowdata is null then raise exception 'No testable existing event'; end if;
 stats:=jsonb_build_object('status','partial','window_start',now(),'window_end',now()+interval '7 days');
 enrichment:=jsonb_build_object(
 'studios',jsonb_build_array(jsonb_build_object('studio_id',99999991,
 'details',jsonb_build_object('name','Rollback-only studio','amenities',jsonb_build_object('showers',false)),
 'raw_directory',jsonb_build_object('unknown_directory_field','preserved'),
 'raw_schedule',jsonb_build_object('id',99999991,'unknown_studio_field','preserved'),
 'raw_profile',jsonb_build_object('id',99999991,'unknown_profile_field','preserved'),'profile_checked_at',now())),
 'classes',jsonb_build_array(jsonb_build_object('studio_id',99999991,'class_id',99999992,
 'details',jsonb_build_object('level','Open Level'),'raw_class',jsonb_build_object('id',99999992,'unknown_class_field','preserved'))),
 'sessions',jsonb_build_array(jsonb_build_object('event_id',rowdata->>'event_id','studio_id',99999991,'class_id',99999992,
 'details',jsonb_build_object('retail_price_in_credits',0,'is_livestream',false,'instructor_name','Rollback fixture'),
 'raw_session',jsonb_build_object('id',substring(rowdata->>'event_id' from 11)::bigint,'unknown_session_field','preserved'),
 'observed_at',now())));
 result:=public.sync_classpass_enriched(jsonb_build_array(rowdata),50,'enrichment-rollback-test',0,true,stats,enrichment);
 if (result->>'enriched')::integer<>1 then raise exception 'Enrichment not confirmed'; end if;
 select to_jsonb(v) into value from public.classpass_detailed_events v where event_id=rowdata->>'event_id';
 if value->'raw_session_attributes'->>'unknown_session_field'<>'preserved'
 or value->'raw_class_attributes'->>'unknown_class_field'<>'preserved'
 or value->'raw_studio_profile_attributes'->>'unknown_profile_field'<>'preserved'
 or value->'studio_amenities'->'showers'<>'false'::jsonb
 or value->'retail_price_in_credits'<>'0'::jsonb then raise exception 'Source fields lost'; end if;
 if (select price from public.events where event_id=rowdata->>'event_id') is distinct from original_price then
 raise exception 'Credit cost changed cash price'; end if;
 if not exists(select 1 from jsonb_array_elements(public.classpass_cached_profiles(50)) x where (x->>'studio_id')::bigint=99999991) then raise exception 'Profile cache scope failed'; end if;
 -- Repeated upserts must not create duplicate sessions or studios.
 perform public.sync_classpass_enriched(jsonb_build_array(rowdata),50,'enrichment-rollback-test',0,true,stats,enrichment);
 if (select count(*) from public.classpass_session_details where event_id=rowdata->>'event_id')<>1 then
 raise exception 'Duplicate session'; end if;
 enrichment:=jsonb_set(enrichment,'{sessions,0,event_id}','"classpass:999999990009"');
 begin
 perform public.sync_classpass_enriched(jsonb_build_array(rowdata),50,'enrichment-invalid-test',0,true,stats,enrichment);
 raise exception 'Expected invalid scope rejection';
 exception when others then
 if sqlerrm='Expected invalid scope rejection' then raise;end if;
 end;
 if has_table_privilege('anon','public.classpass_detailed_events','select')
 or has_function_privilege('authenticated','public.sync_classpass_enriched(jsonb,integer,text,integer,boolean,jsonb,jsonb)','execute') then
 raise exception 'Enrichment was exposed publicly'; end if;
end $$;
rollback;
select 'PASS: raw fields, false/zero values, credits separate from price, profile cache, deduplication, scope rejection and private grants; test changes rolled back' as verification;

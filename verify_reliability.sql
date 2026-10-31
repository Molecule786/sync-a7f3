begin;
do $$
declare
 item jsonb; stats jsonb; result jsonb; original_updated timestamptz;
 snapshot jsonb; checks integer; next_start timestamptz:=now()+interval '2 days';
begin
 select to_jsonb(e),e.updated_at into item,original_updated from public.events e
 where e.source='classpass' and e.country='US' and e.starts_at>now()+interval '10 minutes'
 and e.starts_at<now()+interval '6 days' and e.region is not null limit 1;
 if item is null then raise exception 'No eligible real row for unchanged-write test'; end if;
 stats:=jsonb_build_object('status','partial','window_start',now(),'window_end',now()+interval '7 days');
 result:=public.sync_classpass_us(jsonb_build_array(item),50,'reliability-test-unchanged',0,true,stats);
 if (result->>'unchanged')::integer<>1 or (result->>'updated')::integer<>0 then
   raise exception 'Unchanged row was rewritten: %',result; end if;
 if (select updated_at from public.events where event_id=item->>'event_id') is distinct from original_updated then
   raise exception 'Unchanged updated_at moved'; end if;
 item:=item||jsonb_build_object('title',(item->>'title')||' [transaction test]');
 result:=public.sync_classpass_us(jsonb_build_array(item),50,'reliability-test-changed',0,true,stats);
 if (result->>'updated')::integer<>1 then raise exception 'Changed row not updated'; end if;
 if (select source_changed_at from public.classpass_event_freshness where event_id=item->>'event_id') is null then
   raise exception 'Missing change timestamp'; end if;
 item:=jsonb_build_object('event_id','classpass:999999990001','source','classpass','title','Rollback-only fixture',
 'country','US','city','Missoula','city_slug','missoula','region','MT',
 'url','https://classpass.com/classes/rollback-test/test/999999990001',
 'starts_at',next_start,'ends_at',next_start+interval '1 hour');
 result:=public.sync_classpass_us(jsonb_build_array(item),50,'reliability-test-fixture',0,true,stats);
 if (result->>'inserted')::integer<>1 then raise exception 'Fixture insert failed'; end if;
 update public.classpass_market_events set last_seen_at=now()-interval '2 days',last_seen_snapshot='older'
 where event_id='classpass:999999990001';
 stats:=jsonb_build_object('status','partial','window_start',now()-interval '7 hours',
 'window_end',now()-interval '7 hours'+interval '7 days');
 perform public.sync_classpass_us('[]',50,'reliability-test-partial',0,true,stats);
 select missing_checks into checks from public.classpass_market_events where event_id='classpass:999999990001';
 if checks<>0 then raise exception 'Partial collection marked missing'; end if;
 stats:=stats||jsonb_build_object('status','complete');
 perform public.sync_classpass_us('[]',50,'reliability-test-missing1',0,true,stats);
 perform public.sync_classpass_us('[]',50,'reliability-test-repeated-snapshot',0,true,stats);
 select missing_checks into checks from public.classpass_market_events where event_id='classpass:999999990001';
 if checks<>1 then raise exception 'Repeated snapshot counted twice: %',checks; end if;
 update public.classpass_market_events set last_missing_at=now()-interval '7 hours'
 where event_id='classpass:999999990001';
 stats:=jsonb_build_object('status','complete','window_start',now(),'window_end',now()+interval '7 days');
 perform public.sync_classpass_us('[]',50,'reliability-test-missing2',0,true,stats);
 if (select listing_status from public.classpass_event_status where event_id='classpass:999999990001')<>'unverified_missing'
 then raise exception 'Missing lifecycle flag not set'; end if;
 if (select is_hidden from public.events where event_id='classpass:999999990001') then
   raise exception 'Missing row was automatically hidden'; end if;
 result:=public.sync_classpass_us(jsonb_build_array(item),50,'reliability-test-reappeared',0,true,stats);
 if (select missing_checks from public.classpass_market_events where event_id='classpass:999999990001')<>0
 then raise exception 'Reappearance did not reset missing count'; end if;
 update public.events set starts_at=now()-interval '2 hours',ends_at=now()-interval '1 hour'
 where event_id='classpass:999999990001';
 if (select listing_status<>'ended' or is_upcoming from public.classpass_event_status where event_id='classpass:999999990001') then
 raise exception 'Expired listing still upcoming'; end if;
 snapshot:=jsonb_build_object('version',1,'market_id',50,'status','running','window_start',now());
 perform public.classpass_checkpoint_save(50,snapshot);
 if public.classpass_checkpoint_read(50)<>snapshot then raise exception 'Checkpoint roundtrip failed'; end if;
 if has_function_privilege('anon','public.classpass_checkpoint_read(integer)','execute')
 or has_table_privilege('anon','public.classpass_collection_checkpoints','select')
 or has_table_privilege('authenticated','public.classpass_event_status','select') then
 raise exception 'New tracking data exposed'; end if;
end $$;
rollback;
select 'PASS: unchanged writes, changed content, partial-scan safety, missing/reappearance, expiry, checkpoints and private permissions; all test changes rolled back' as verification;

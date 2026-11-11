-- Existing private working-cache policy only. No event/venue data is changed.
-- Idempotent; all existing grants and retention safeguards remain in place.
begin;
do $migration$
declare signature text; definition text;
begin
 foreach signature in array array[
  'public.directory_retention_cache_state()',
  'public.prune_directory_retention_cache(jsonb)'
 ] loop
  definition:=pg_get_functiondef(signature::regprocedure);
  if position('20 hours' in definition)>0 then
   definition:=replace(definition,'20 hours','72 hours');
   execute definition;
  elsif position('72 hours' in definition)=0 then
   raise exception 'Unexpected cache policy in %; unchanged',signature;
  end if;
 end loop;
end $migration$;
commit;

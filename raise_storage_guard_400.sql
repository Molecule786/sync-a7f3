-- Raise only the current global ClassPass guards from 350 to 400 MiB.
-- Preserve function bodies, privileges and policies; no event data changes.
begin;
do $migration$
declare r record; definition text; checked integer := 0;
begin
 for r in select p.oid,p.proname,p.prosrc from pg_proc p join pg_namespace n on n.oid=p.pronamespace
 where n.nspname='public' and p.proname in ('refresh_classpass_precise','sync_directory_global_test','directory_global_test_health') loop
  if r.prosrc not like '%367001600%' and r.prosrc not like '%419430400%' then
   raise exception 'Unexpected guard in %; migration aborted',r.proname;
  end if;
  definition := replace(replace(pg_get_functiondef(r.oid),'367001600','419430400'),'350 MiB','400 MiB');
  execute definition;
  checked := checked+1;
 end loop;
 if checked <> 3 then raise exception 'Expected exactly 3 guard functions, found %',checked; end if;
end $migration$;
commit;
select public.directory_global_test_health() as health,400 as guard_mib;

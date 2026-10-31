-- Read-only dashboard for ClassPass collection health.
select listing_status,count(*) as classes,
 count(*) filter(where is_upcoming) as upcoming
from public.classpass_event_status group by listing_status order by listing_status;

select market_id,snapshot->>'status' as status,
 snapshot->'summary'->>'completed_tasks' as completed_tasks,
 snapshot->'summary'->>'expected_tasks' as expected_tasks,
 snapshot->>'retryable' as retryable,
 updated_at
from public.classpass_collection_checkpoints order by market_id;

select market_id,status,processed,inserted,updated,unchanged,finished_at
from public.classpass_us_runs order by started_at desc limit 10;

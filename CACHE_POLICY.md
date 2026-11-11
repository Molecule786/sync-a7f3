# ClassPass private cache policy

Studio profiles are reusable for 72 hours from their original source observation.
Reading, compressing, or saving a cached profile never renews that timestamp.
Schedules and availability are still requested fresh for every uncompleted
date/studio task in each seven-day collection window. Profile ratings, descriptions
and facilities may therefore be up to three days old; this is a deliberate tradeoff.

The runner stores full studio cache records in a lossless gzip/base64 envelope
only when it is smaller than the original JSON. No published field is discarded,
including unknown source attributes, false, zero and empty values. Studio ID,
original observation time, name and city remain available to the existing RPC.
Readers accept old JSON and the new format. Checksums, identity, timestamp and
bounded decompression protect recovery; invalid cached data is fetched again.
Existing raw cache rows convert as they are reused or refreshed, not all at once.
This does not change public.events or public.venues fields or database permissions.

Profiles already loaded in a job are reused locally across dates instead of being
downloaded repeatedly from Supabase. Each summary reports profile_cache_hits,
profile_source_requests and profile_cache_hours for comparison with later runs.

Daily retention uses the same 72-hour threshold and retains referenced test
profiles. Existing encrypted backups, concurrent-change checks and the 400 MiB
insert guard remain. cache_policy_72h.sql updates the two installed retention
functions idempotently; setup_retention.sql contains the matching new-install policy.
No profile rows are purged by this migration.

Schedule checkpoints keep their current 12-hour recovery window, matching the
collector and retry planner. Within that window unfinished work resumes; beyond
it schedules are recollected for freshness. Compact country/status metadata stays.
This change does not promise exhaustive country coverage or cure source failures.

Do not enable obsolete workflows using older cache readers. No full cache rewrite,
VACUUM FULL, live-table migration, schedule change or storage-limit increase is
part of this deployment. Compare actual stored cache allocation after several
runs; gzip/base64 size alone is not proof of savings over PostgreSQL TOAST.

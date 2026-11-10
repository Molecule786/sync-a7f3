Current operation (2026-11-10): verified expiry retention starts daily at 01:13 UTC (06:13 Pakistan time). It backs up before clearing stale temporary data and deleting exact-source-confirmed events ended more than 24 hours ago. Unverified, rescheduled and concurrently edited rows are retained. The full ClassPass top-40 reconciliation follows successful retention automatically. One retry at 10:13 UTC (15:13 Pakistan time) resumes recent unfinished retryable ClassPass routes. The 400 MiB collection guard remains. ACTIVE refreshes daily at 04:43 UTC; OpenSports refreshes every six hours at minute 17. Encrypted recovery artifacts are retained seven days; the recovery key stays local. These cloud jobs run with the computer off. Historical pilot sections below are reference only.

# FitGlobe ClassPass daily US sync â€” staged rollout



## Current US expansion



The active free-plan stage collects the **next rolling seven days** for **Missoula (50), Milwaukee (34), and Indianapolis (45)**. The two-studio NYC pilot below also remains active. These are separate jobs in the same website workflow, not a claim of complete US coverage. `us_markets.json` holds 40 published US market entries; only the three IDs in `us_stage.json` are enabled for the expansion.



`us_collector.py` discovers each market's public fitness directory, follows the published search cursor, reconciles unique venue counts, then follows every date-specific schedule cursor for the rolling UTC window. Date enumeration uses the market timezone and includes daylight-saving transitions. Sessions are deduplicated by ClassPass ID; actual venue addresses supply each event's city/state. Canadian and unverified locations and non-fitness appointments are excluded. Source requests are unauthenticated public requests; no ClassPass login or browser cookies are used.



Safety limits: at most 3 expansion markets per workflow, one expansion job at a time, 150 source requests and 6 runner minutes per market per run. No paid plan or billing setting is enabled by this project. Account-wide GitHub usage and Supabase quotas are shared with other workloads; these bounds are not a guarantee of remaining free capacity. US writes stop if the PostgreSQL database reaches 350 MiB, leaving headroom below the usual 500 MB free database allowance. No old data is automatically deleted to stay under that limit.



`setup_us.sql` is an additive one-time migration for `sync_classpass_us`, `classpass_us_runs`, and replay-safe batch receipts in `classpass_us_batches`. Do not rerun an installed migration. A complete source collection happens before US writes begin. Each database batch is atomic and replay-safe; an interrupted multi-batch write may leave verified partial data and a `writing` run. A failed market does not stop other markets or delete records. Production reruns resume completed schedule tasks from a Supabase checkpoint younger than 12 hours. A task is checkpointed only after all of its cursor pages succeed; incomplete discovery and incomplete tasks restart. Artifacts retain coverage summaries for seven days.



`classpass_us_runs.status` describes **that market search footprint**, not nationwide completeness. `stats` includes expected/discovered venues, completed schedule tasks, exclusions, source requests, and source window. A capped country-wide search is labelled partial even when all reachable results are visited. ClassPass's broad United States search can contain Canadian venues and caps reported results at 10,000; it is not enabled in this free stage. A blocked page is never reported as zero or complete.



Manual workflow modes remain `preview` (no database writes), `test` (NYC isolated test only), and `production` (NYC plus the three staged markets). `--sample` limits discovery/tasks for development and cannot write production. Follow source coverage and per-market results in Actions â†’ ClassPass daily US staged sync. Expanding stages requires reviewing runtime, storage and unresolved geography before editing `us_stage.json` and the workflow budget.



## Original NYC pilot



Status: limited production pilot. Runs daily at **10:17 UTC (15:17 Pakistan time)** on GitHub; your computer does not need to be on. Scheduled jobs can be delayed. Check Actions for failures, summaries and JSON artifacts retained for seven days.



This project reads published Event JSON-LD from the current public schedule of two New York studios. It verifies UTC dates, uses the ClassPass session ID as `event_id`, excludes past classes, deduplicates, and caps each run at 10 classes starting within seven days. It does not enumerate all seven days, all NYC studios, prices, credits, live availability or cancellations. A blocked page fails the run; it is never treated as a successful empty collection.



## Files



- `collector.py`: fetch, validate and optionally merge the pilot data.

- `config.json`: public project URL, pilot studios and limits. No secret values.

- `setup_test.sql`: isolated database test table and restricted merge function; no changes to `public.events`.

- `setup_production.sql`: one-time production merge and freshness tracking migration.

- `.github/workflows/daily.yml`: scheduled and manual workflow.



## Local preview



Use Python 3.12. Install `requirements.txt`, run `python -m unittest -v`, then `python collector.py`. Inspect `output/preview.json` and `output/summary.json`.



## GitHub Actions



Open Actions â†’ ClassPass daily US staged sync â†’ Run workflow. Choose `preview` (default, no writes), `test` (NYC isolated table), or `production`. Scheduled runs use production. The workflow fails on access errors or invalid data, and records a readable summary. Pause through the workflow's three-dot menu â†’ Disable workflow. GitHub notification preferences control failure notifications; no custom messages are sent by this project.



The migrations are already installed in the original Supabase project. They are one-time scripts; do not rerun them unchanged. `SUPABASE_SECRET_KEY` is stored in GitHub Actions repository secrets. Never commit it, paste it into chat or use it in a public frontend. Workflow editors can use this key. The collector explicitly selects `sync_classpass_test` or `sync_classpass_events`; both reject invalid pilot data and use invoker rights. New tracking tables have RLS enabled with public/anon/authenticated access revoked. Existing events RLS policies are not replaced.



## Freshness and validation



GitHub preview and authenticated isolated merges passed. Repeated upserts inserted no duplicates; optional-value preservation and invalid-batch rollback were verified in Supabase. Production run results appear in Actions and `classpass_sync_runs`. `classpass_event_freshness.last_seen_at` records when each class was verified. Failures appear in Actions, not as successful database runs. The staged US merge skips unchanged event rows. `classpass_event_freshness.source_changed_at` tracks changes observed by that merge, while `last_seen_at` tracks the original source observation, including after a resumed task. The small legacy NYC pilot still uses its original write-based updated_at behavior.



Missing classes are not inferred to be cancellations. Expired rows remain stored; applications should filter `ends_at > now()` for upcoming classes. A once-daily refresh does not guarantee live booking availability. The separate US staged collector enumerates seven days for three market footprints; nationwide discovery remains incomplete. Each production batch is atomic, capped at ten NYC classes, validates URL/session identity, preserves absent optional values and existing prices/hidden flags, and rejects collisions with other sources.



## Reliability update (2026-10-31)



Repository: Molecule786/sync-a7f3, PRIVATE. This migration does not expand collection beyond the existing three staged US markets or change billing. Countries remain the intended orchestration/reporting boundary; markets are internal source-specific batches. No other countries are enabled yet.



`setup_reliability.sql` is a one-time additive migration, installed after setup_us.sql. It adds service-role-only checkpoints and market-event observations, replaces the US merge to skip unchanged content, and adds a service-only `classpass_event_status` view. No public/anonymous access is granted. Existing event access policies and manual hidden flags are preserved. Source records from other websites are not modified.



### Schedule and recovery



- Full daily refresh: 10:17 UTC / 15:17 Pakistan time, including the legacy NYC pilot.

- Retry check: 11:17 UTC / 16:17 Pakistan time, staged US markets only. Manual `retry` mode uses the same rules. Only recent unfinished retryable snapshots run; completed collections and nonretryable failures are skipped. A cooldown is respected even on a full refresh.

- At most one extra scheduled attempt per day. Failed retry waits for the next daily refresh or a manual retry; this is not an hourly retry loop. GitHub schedules can be delayed or dropped.

- Transient source errors and transport errors get up to three attempts within the job. HTTP 401/403 and source format/validation errors fail visibly without automatic replay. A long Retry-After becomes a saved cooldown.

- Four-minute source budget, 150 source requests per market per attempt, six-minute job limit, one staged market at a time. Setup, checkpoint database calls and final writes also consume job time. Checkpoints survive runner teardown, but an abrupt termination can lose the current task, which then repeats. Discovery currently restarts if interrupted before it completes.

- Completed task snapshots expire after twelve hours. Each resumed run retains its original seven-day window; it does not claim the newly elapsed hours at the far end. The next full daily run creates a fresh window.

- Checkpoints are bounded to eight MiB per internal batch and one current snapshot per enabled market. They are stage-sized, not a solution for one enormous 500k-row snapshot. Future countries must use multiple bounded internal batches. The existing 350 MiB database write guard still applies. Run/batch audit retention needs a policy before large-scale rollout; no automatic history deletion is enabled.



### Completeness and activity



A partial collection exits with a failing job status, even if its verified records were safely merged. Country reporting always says INCOMPLETE for this staged rollout. A successful market footprint is not a successful nationwide crawl.



`classpass_event_status` computes:

- `listed`: upcoming, recently observed, no repeated absence flag. It is NOT proof of booking availability.

- `started` / `ended`: derived from the class timestamps.

- `hidden`: existing manual is_hidden flag.

- `stale`: not observed for 36 hours, or never tracked.

- `unverified_missing`: absent from two distinct complete checked windows, separated by at least six hours. Partial/failed snapshots and snapshots replayed on retries do not add missing checks. A reappearance resets the flag. Cross-market mappings use the least missing count.



Missing observations apply only to previously mapped events inside that market's fully checked future window. They never set cancellation status, delete events, or change is_hidden. Old unmapped rows are marked stale rather than assumed missing. Booking availability is explicitly `unknown`.



The status view is initially backend-only. The actual website UI is not in this repository and has not been changed: it must filter upcoming classes using starts_at > now() and is_hidden=false, and read freshness through an authorized backend before it can show these labels. Direct reads of events do not automatically hide expired records.



### Validation



Run `python -m unittest -v`. `verify_reliability.sql` tests real unchanged-write behavior and fixtures for changed content, partial-scan safety, distinct missing observations, reappearance, expiry, checkpoint roundtrip and private grants, all inside a rolled-back transaction. Never store real credentials in files or logs.


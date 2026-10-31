# FitGlobe ClassPass daily US sync — staged rollout

## Current US expansion

The active free-plan stage collects the **next rolling seven days** for **Missoula (50), Milwaukee (34), and Indianapolis (45)**. The two-studio NYC pilot below also remains active. These are separate jobs in the same website workflow, not a claim of complete US coverage. `us_markets.json` holds 40 published US market entries; only the three IDs in `us_stage.json` are enabled for the expansion.

`us_collector.py` discovers each market's public fitness directory, follows the published search cursor, reconciles unique venue counts, then follows every date-specific schedule cursor for the rolling UTC window. Date enumeration uses the market timezone and includes daylight-saving transitions. Sessions are deduplicated by ClassPass ID; actual venue addresses supply each event's city/state. Canadian and unverified locations and non-fitness appointments are excluded. Source requests are unauthenticated public requests; no ClassPass login or browser cookies are used.

Safety limits: at most 3 expansion markets per workflow, one expansion job at a time, 150 source requests and 6 runner minutes per market per run. No paid plan or billing setting is enabled by this project. Account-wide GitHub usage and Supabase quotas are shared with other workloads; these bounds are not a guarantee of remaining free capacity. US writes stop if the PostgreSQL database reaches 350 MiB, leaving headroom below the usual 500 MB free database allowance. No old data is automatically deleted to stay under that limit.

`setup_us.sql` is an additive one-time migration for `sync_classpass_us`, `classpass_us_runs`, and replay-safe batch receipts in `classpass_us_batches`. Do not rerun an installed migration. A complete source collection happens before US writes begin. Each database batch is atomic and replay-safe; an interrupted multi-batch write may leave verified partial data and a `writing` run. A failed market does not stop other markets or delete records. GitHub reruns recollect the failed market; artifacts retain discovered venues, verified rows and coverage summaries for 7 days.

`classpass_us_runs.status` describes **that market search footprint**, not nationwide completeness. `stats` includes expected/discovered venues, completed schedule tasks, exclusions, source requests, and source window. A capped country-wide search is labelled partial even when all reachable results are visited. ClassPass's broad United States search can contain Canadian venues and caps reported results at 10,000; it is not enabled in this free stage. A blocked page is never reported as zero or complete.

Manual workflow modes remain `preview` (no database writes), `test` (NYC isolated test only), and `production` (NYC plus the three staged markets). `--sample` limits discovery/tasks for development and cannot write production. Follow source coverage and per-market results in Actions → ClassPass daily US staged sync. Expanding stages requires reviewing runtime, storage and unresolved geography before editing `us_stage.json` and the workflow budget.

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

Open Actions → ClassPass daily US staged sync → Run workflow. Choose `preview` (default, no writes), `test` (NYC isolated table), or `production`. Scheduled runs use production. The workflow fails on access errors or invalid data, and records a readable summary. Pause through the workflow's three-dot menu → Disable workflow. GitHub notification preferences control failure notifications; no custom messages are sent by this project.

The migrations are already installed in the original Supabase project. They are one-time scripts; do not rerun them unchanged. `SUPABASE_SECRET_KEY` is stored in GitHub Actions repository secrets. Never commit it, paste it into chat or use it in a public frontend. Workflow editors can use this key. The collector explicitly selects `sync_classpass_test` or `sync_classpass_events`; both reject invalid pilot data and use invoker rights. New tracking tables have RLS enabled with public/anon/authenticated access revoked. Existing events RLS policies are not replaced.

## Freshness and validation

GitHub preview and authenticated isolated merges passed. Repeated upserts inserted no duplicates; optional-value preservation and invalid-batch rollback were verified in Supabase. Production run results appear in Actions and `classpass_sync_runs`. `classpass_event_freshness.last_seen_at` records when each class was verified. Failures appear in Actions, not as successful database runs. The existing production table's `updated_at` trigger records writes; it is not evidence of source content changes.

Missing classes are not inferred to be cancellations. Expired rows remain stored; applications should filter `ends_at > now()` for upcoming classes. A once-daily refresh does not guarantee live booking availability. Broader studio discovery and multi-day enumeration remain future work. Each production batch is atomic, capped at ten NYC classes, validates URL/session identity, preserves absent optional values and existing prices/hidden flags, and rejects collisions with other sources.

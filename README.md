# FitGlobe ClassPass daily sync — pilot

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

Open Actions → ClassPass daily sync → Run workflow. Choose `preview` (default, no writes), `test` (isolated table), or `production` (pilot upsert to events). Scheduled runs use production. The workflow fails on access errors or invalid/empty data, and records a readable summary. Pause through the workflow's three-dot menu → Disable workflow. GitHub notification preferences control failure notifications; no custom messages are sent by this project.

The migrations are already installed in the original Supabase project. They are one-time scripts; do not rerun them unchanged. `SUPABASE_SECRET_KEY` is stored in GitHub Actions repository secrets. Never commit it, paste it into chat or use it in a public frontend. Workflow editors can use this key. The collector explicitly selects `sync_classpass_test` or `sync_classpass_events`; both reject invalid pilot data and use invoker rights. New tracking tables have RLS enabled with public/anon/authenticated access revoked. Existing events RLS policies are not replaced.

## Freshness and validation

GitHub preview and authenticated isolated merges passed. Repeated upserts inserted no duplicates; optional-value preservation and invalid-batch rollback were verified in Supabase. Production run results appear in Actions and `classpass_sync_runs`. `classpass_event_freshness.last_seen_at` records when each class was verified. Failures appear in Actions, not as successful database runs. The existing production table's `updated_at` trigger records writes; it is not evidence of source content changes.

Missing classes are not inferred to be cancellations. Expired rows remain stored; applications should filter `ends_at > now()` for upcoming classes. A once-daily refresh does not guarantee live booking availability. Broader studio discovery and multi-day enumeration remain future work. Each production batch is atomic, capped at ten NYC classes, validates URL/session identity, preserves absent optional values and existing prices/hidden flags, and rejects collisions with other sources.

# FitGlobe ClassPass daily sync — pilot

Status: pilot, manual runs only. Production writes and daily scheduling are not enabled yet.

This project reads published Event JSON-LD from the current public schedule of two New York studios. It verifies UTC dates, uses the ClassPass session ID as `event_id`, excludes past classes, deduplicates, and caps each run at 10 classes starting within seven days. It does not enumerate all seven days, all NYC studios, prices, credits, live availability or cancellations. A blocked page fails the run; it is never treated as a successful empty collection.

## Files

- `collector.py`: fetch, validate and optionally merge the pilot data.
- `config.json`: public project URL, pilot studios and limits. No secret values.
- `setup_test.sql`: isolated database test table and restricted merge function; no changes to `public.events`.
- `.github/workflows/daily.yml`: GitHub Actions manual test workflow.

## Local preview

Use Python 3.12. Install `requirements.txt`, run `python -m unittest -v`, then `python collector.py`. Inspect `output/preview.json` and `output/summary.json`.

## GitHub Actions

Open Actions → ClassPass pilot → Run workflow. Leave `write_test` unchecked for a preview. The run produces a downloadable artifact. The workflow fails on access errors or invalid data, and records a summary.

For an isolated database test, first install `setup_test.sql` in the intended Supabase project. Add `SUPABASE_SECRET_KEY` under repository Settings → Secrets and variables → Actions → New repository secret. Enter the value directly on GitHub; do not commit it or paste it into chat. Then run with `write_test` checked. The only callable write target in this collector is `sync_classpass_test`, which writes to `classpass_events_test`.

## Before enabling production

Verify a GitHub-hosted preview succeeds, run an isolated merge twice and confirm the second run inserts no duplicates. Test that missing fields retain stored values and invalid batches roll back. Prepare a separate production merge function after review. Add freshness/run monitoring and a daily schedule after the pilot passes. Do not infer cancellations from missing classes or delete records on incomplete/blocked crawls. The existing production table's `updated_at` trigger records writes; it is not evidence of source content changes.

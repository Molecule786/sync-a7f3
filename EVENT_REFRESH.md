# Existing event refresh

ACTIVE and OpenSports use separate GitHub workflows and a shared field-preserving parser/database client. Only IDs already present in `public.events` are eligible; there are no venue writes or event insertions. All existing stored countries are included. No claim of national completeness is made.

Schedules: ACTIVE daily at 04:43 UTC (09:43 Pakistan time); OpenSports every six hours at 00:17, 06:17, 12:17 and 18:17 UTC. GitHub schedules are best effort. Manual runs default to preview; production must be selected explicitly. Sample 0 processes every eligible existing event; 1..100 is a bounded validation run.

Each attempt verifies the exact source identity, timezone-aware dates and a nonempty title before proposing changes. Missing values do not erase existing details. Price, currency, category arrays, source, IDs, visibility and registration status are preserved. Concurrent database edits are skipped. An unchanged event is not rewritten. Verification timestamps and country/error counts appear in summaries; `updated_at` records changes, not every verification. Missing pages and blocked access are unverified, never cancellations or zero inventory.

ACTIVE source URLs are resolved through the original public asset-ID-to-page mapping because some existing database URLs point at registration forms. The public source page must still prove the exact asset ID. No registration form is submitted. OpenSports reads public event page state and deliberately ignores attendee data.

Up to 100 existing IDs per internal job, two jobs at a time, eight-minute collection budget and twelve-minute job ceiling. Transient source requests receive up to three attempts. HTTP 403, prolonged cooldowns and unrecognized formats fail visibly. Failed shards can be rerun manually; the next scheduled run rechecks existing eligible events. New event discovery is disabled.

The older ClassPass staged workflows remain disabled and its global refresh remains manual. Their historical README schedule statements do not describe current operation. Current global ClassPass storage guard is 400 MiB; disabled legacy components retain their original guards.

The one-off expired-events cleanup is manual only and has a frozen November 6, 2026 cutoff. It exports authenticated encrypted backup chunks and uploads them before deletion. The private recovery key is retained locally and is never committed or uploaded. Matching valid ended rows and their linked detail/tracking rows are then removed; concurrent edits and malformed ranges are retained. No recurring deletion policy is enabled.

# Mindbody automation

Public studio profiles and scheduled classes exist on Mindbody. This adapter uses the same public studio lookup and schedule endpoints as Explore. It never books classes or accesses customer accounts.

The shared `venues` table stores studios (`venue_id=mindbody:<public-location-id>`). The shared `events` table stores individual scheduled classes (`event_id=mindbody:<site-id>:<location-id>:<class-id>`). Random schedule response UUIDs are not used as identities. No new tables or columns are needed. Small rotation cursors use the existing shared checkpoint table under source `mindbody`.

## Schedule and staged limits

GitHub runs `mindbody.yml` daily at 20:37 UTC, 01:37 Pakistan time the following day. It covers the agreed top-40 GDP scope, including India, using stored studio seeds. It checks up to 100 rotating existing studios per country per run, reads their next seven days of schedules, and inserts at most 50 new classes per country. Existing matching class updates do not consume that insertion quota. Two country jobs run concurrently. Each country has a 12-minute source work budget and a 700-request cap. These are staged coverage limits, not a claim that every studio refreshes daily.

New studio discovery checks the first 100 public results within 25 km of the first rotating studio per country, inserting at most ten verified new fitness studios. Added studios join the rotation on later runs. Countries without saved seeds remain unsearched, not confirmed zero. Nearby discovery and bounded schedule responses do not prove exhaustive national coverage. Every job uploads its counts and gaps as a seven-day summary artifact.

Manual dispatch defaults to a US preview, three studios and ten new classes. Production must be selected explicitly for manual runs. Scheduled runs apply automatically using the existing GitHub Supabase secret; the user's computer can be off.

## Data preservation

The service-only RPC accepts only Mindbody identities and explicitly allowed fields with recent source evidence. It compares the entire stored row before updating and skips concurrent changes. Empty source values cannot erase details. Unchanged fields are not written. Locked studio content is skipped; locked categories are preserved. Flags, IDs, categories and unrelated sources are protected. There are no event or venue deletions in this adapter. The existing retention policy still covers ClassPass, ACTIVE and OpenSports only.

Classes require explicit timezone-bearing times, exact studio/site/location identity, an explicit non-cancelled source flag and a start within the next seven days. Cancelled or missing classes are not interpreted as permission to delete stored records. Introductory or package prices are not substituted for regular drop-in prices. Published descriptions, instructors and valid policies fit the existing description field; unavailable fields stay untouched. New inserts stop at the existing 400 MiB database guard. Access or database failures produce a failed job and a summary; they are never reported as zero matches.

## Verification

`python -m unittest test_mindbody test_event_refresh test_retention -q` passes 19 focused tests. `verify_mindbody.sql` checks protected fields, studio content locks, stable insert/replay and concurrent edit protection inside a rolled-back transaction. Production writes must additionally be checked in the workflow summary and directly in Supabase.

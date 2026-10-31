# Detailed ClassPass collection

The current three staged US market collectors now retain public studio, class and session attributes instead of discarding fields outside the original events schema. The separate small NYC JSON-LD pilot is unchanged. No frontend changes or new public database permissions are introduced.

## Where to inspect the details

In Supabase SQL Editor run:

```sql
select title,instructor_name,duration_minutes,class_activities,class_level,
 retail_price_in_credits,source_availability,availability_observed_at,
 studio_description,studio_images,studio_amenities,studio_rating,studio_rating_count,
 studio_phone,studio_website,how_to_get_there,what_to_bring,when_to_arrive
from public.classpass_detailed_events
order by starts_at limit 20;
```

`classpass_detailed_events` joins the normal events row with these private backend tables:

- `classpass_studios`: one studio per source ID; readable details plus complete latest directory, schedule-studio and detailed-profile attributes. Includes images, public contact/social links, amenities, ratings, arrival/preparation/travel instructions, opening hours, booking windows and requirements when supplied.
- `classpass_classes`: one class template per studio and class ID; full original class attributes plus name, description, activity types, level, ratings and instructions.
- `classpass_session_details`: one row per events.event_id; instructor, duration, livestream flag, observed availability, credits, tags and every other original public session attribute. Venue and class objects are stored separately to avoid repeating large text and image objects on thousands of sessions.

The combined view includes all existing events columns, more than thirty additional named detail fields, and all five original-attribute JSON columns. The JSON columns retain fields that have no dedicated column, including fields added by the source later. They are latest entity snapshots, not an archive of every response or a guarantee of identical entity snapshots at every historical class observation. Images remain URLs; image files are not downloaded.

## Interpretation

- Missing source values remain missing. False, zero and empty lists inside the original JSON remain intact.
- A displayed rating count such as 2500+ remains display text; it is not fabricated into an exact count.
- Credit cost is `retail_price_in_credits`, never a cash price. Existing events.price and currency remain untouched.
- `source_availability` is an unauthenticated source observation, not a guarantee that a particular account can book. It has its own observation timestamp. Reopening a resumed checkpoint does not refresh that timestamp.
- Cancellation provider codes and late-cancellation values are preserved as published; they are not rewritten as human-readable policy promises.
- No classes are automatically hidden, cancelled or deleted.

## Enrichment budgets and recovery

Full public studio profile requests use the remaining source request/time budget after schedules. Cached full profiles are reused for seven days. The same 150 requests and four-minute source budget per market remain. If profiles are still pending because of budget, verified data is saved as partial and the existing retry-only workflow resumes the remaining profile requests without recollecting completed schedule tasks. Access denial or invalid metadata remains a visible nonretryable error. Profile requests have no login, credentials or browser cookies.

Checkpoint version 2 includes deduplicated studio/class/session snapshots and remains capped at eight MiB. Large checkpoints use lossless gzip/base64 transfers with visible status headers. Repeated display fields are rebuilt from original attributes on resume, and profile progress is saved in groups of ten. Complete checkpoints are compacted after the enriched merge succeeds. Very large markets will still require smaller internal batches before rollout. Storage writes retain the 350 MiB guard; raw source JSON consumes additional database space. Detailed metadata is linked to existing IDs, so re-running does not create new event duplicates.

`setup_enrichment.sql` is a one-time migration installed after setup_reliability.sql. Do not rerun an installed migration. The wrapper `sync_classpass_enriched` merges the normal events and details in the same transaction per batch and confirms the enriched session count. All new tables/views/functions have anonymous and authenticated access revoked; existing events policies remain unchanged. Frontend adoption is deferred at the user's request.

Tests: `python -m unittest -v`. `verify_enrichment.sql` checks full original fields, false and zero, credit/price separation, cache, replay, event-scope rejection and private grants in a rolled-back transaction. Existing row counts alone are not evidence that every optional source field is populated; inspect per-field fill rates.

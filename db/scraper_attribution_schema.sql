-- Scraper attribution columns on `opportunities` (Phase 1 of the scraper v2 plan).
--
-- These tie every scraped row back to WHERE it came from, so the reviewer's verdict on
-- that row becomes live feedback on the angle (or hub) that found it. The per-angle funnel
-- in the admin console is a GROUP BY over these columns joined with scraper_seeds — there
-- are no writeback counters to drift, so a verdict change retroactively corrects the angle's
-- score on the next read. See wingman/seed_ledger.py.
--
--   seed_id   -> the scraper_seeds row whose ANGLE found this opportunity (search rows).
--                NULL for hand-added, migrated, or fallback-angle rows (no stable id).
--   found_via -> the hub/index URL this row was harvested from (Phase 4 hub mining).
--                NULL for ordinary search rows, whose provenance is seed_id.
--
-- One-time manual step: run this in the Supabase SQL editor (idempotent). Until it runs the
-- scraper degrades — insert_rows() drops these two keys and re-tries, and the console's seed
-- grid reports `seed_ready: false` instead of erroring (the moderation_reason pattern in
-- ops/core.py is the template). PostgREST 400s an entire write on one unknown column, so the
-- degrade is what keeps a scrape from writing nothing at all before this has been applied.
--
-- CONVENTION (like every schema file here): idempotent ADD COLUMN IF NOT EXISTS, safe to
-- re-run. `opportunities` already exists; this only extends it.

alter table opportunities add column if not exists seed_id   bigint;
alter table opportunities add column if not exists found_via text;

-- The funnel filters on `seed_id is not null` and groups by it, so index it.
create index if not exists opportunities_seed_id_idx on opportunities (seed_id);

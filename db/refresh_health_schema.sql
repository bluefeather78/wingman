-- refresh_health_schema.sql — per-row record of the metadata refresh's page fetch.
--
-- WHY THIS EXISTS. agents/refresh_opportunities.py reads each program's LIVE page (MARQUEE M1)
-- and never invents from model memory, so a page it cannot fetch is SKIPPED — no write, no
-- staleness stamp, retried next run. That is correct, but until these columns existed it was
-- also SILENT: ~80 live-but-unfetchable rows (sites that 403 our client / anti-bot walls, TLS
-- failures, PDF/JS-only shells) were walked and re-skipped on every pass with nothing recorded,
-- no way to find them, and the "awaiting refresh" queue that never emptied. These columns record
-- the skip so the stuck set is queryable, and let the agent quarantine a row after N repeated
-- failures instead of re-walking it forever.
--
--   refresh_fetch_status     the SPECIFIC last failure reason: http-403 / error-URLError /
--                            not-html / empty-or-js / no-url. NULL once the page reads again.
--   refresh_fetch_attempts   CONSECUTIVE fetch failures. Reset to 0 the moment the page reads,
--                            so it counts a persistent block, not a lifetime tally.
--   refresh_fetch_failed_at  when it last failed (NULL once it reads again).
--
-- A successful read clears all three. A row is NOT dead — check_links deliberately keeps a 403
-- live, because a 403 is our HTTP client being blocked, not the page being gone. This is a
-- separate, honest state: "live, but we can't re-read it."
--
-- Run this once in the Supabase SQL editor. Idempotent — safe to run more than once. Until it is
-- run the agent degrades cleanly: it probes for these columns once at startup and, finding them
-- absent, simply behaves as before (no skip record, no quarantine); the console Health tab shows
-- the "un-refreshable" count as "—" rather than a false 0.

alter table opportunities add column if not exists refresh_fetch_status    text;
alter table opportunities add column if not exists refresh_fetch_attempts  integer not null default 0;
alter table opportunities add column if not exists refresh_fetch_failed_at  timestamptz;

-- The quarantine selection filters active rows on refresh_fetch_attempts; a partial index keeps
-- that read cheap without carrying the 0-valued majority.
create index if not exists opportunities_refresh_fetch_attempts_idx
    on opportunities (refresh_fetch_attempts)
    where refresh_fetch_attempts > 0;

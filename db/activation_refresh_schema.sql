-- db/activation_refresh_schema.sql — one-time manual DDL (run in the Supabase SQL editor).
--
-- Adds a single queue marker to `opportunities`:
--
--   activation_refresh_queued_at  timestamptz  NULL
--
-- WHAT IT IS. When an operator ACTIVATES a row in the admin console
-- (ops/core.activate_opportunities), the row goes live with whatever metadata it
-- already had — often a scraper's thin extraction. This column records "activated and
-- not yet run through agents/refresh_opportunities.py", so the console's Core Details tab can
-- list the rows still awaiting a metadata read from their live page.
--
-- WHAT IT IS NOT. This is a one-shot QUEUE marker, not a staleness stamp. It is set on
-- activation and CLEARED the first time agents/refresh_opportunities.py successfully reads the
-- row's page (reason == 'ok'). There is deliberately no "refresh is due again after N
-- days" logic here — staleness is a separate, later decision. Do not repurpose this
-- column for it; a re-check clock needs its own `metadata_refreshed_at`.
--
-- DEGRADATION (why the whole thing still works before this is run). Every reader and
-- writer of this column is guarded:
--   * activate_opportunities enqueues best-effort — a missing column is detected and the
--     column is dropped from the write, so activation (and its moderation stamp) still
--     succeeds.
--   * agents/refresh_opportunities.py drops the column from its SELECT on a 400 and skips the
--     drain, so the agent runs unchanged.
--   * the console's Core Details card shows this setup line instead of a count.
-- So until this file is run the feature is simply OFF, not broken.
--
-- Idempotent: `add column if not exists`, safe to re-run.

alter table public.opportunities
  add column if not exists activation_refresh_queued_at timestamptz;

-- Partial index: the console reads only the (small) set of rows currently queued.
create index if not exists opportunities_activation_refresh_queued_idx
  on public.opportunities (activation_refresh_queued_at)
  where activation_refresh_queued_at is not null;

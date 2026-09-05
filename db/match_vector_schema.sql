-- db/match_vector_schema.sql — one-time manual DDL (run in the Supabase SQL editor).
--
-- Adds the catalog-side embedding columns to `opportunities`, for Phase 5 of
-- docs/plans/OPPORTUNITY_MATCHING_PLAN.md (semantic recall). PostgREST has no DDL endpoint, so nothing
-- in this repo can apply this for you.
--
--   match_vector       jsonb        NULL   -- the embedding itself, as a plain float array
--   match_vector_hash  text         NULL   -- content hash of the 5 fields it was computed from
--   match_vector_computed_at  timestamptz  NULL   -- when it was last (re)computed, for debugging
--
-- WHAT THE HASH IS FOR. `match_vector` is computed from exactly five fields —
-- name + org + summary + subject_tags + type — and is only ever recomputed when at least one
-- of them actually changes. `match_vector_hash` records the hash of those five fields' values
-- at the time the vector was computed, so the write path can cheaply check "did anything the
-- vector depends on actually change" before paying for a new embedding call. This is the same
-- exact-identity freshness pattern `profileDerivedIsFresh` already uses on the profile side —
-- applied here so the embedding can never be stale relative to a schedule, only ever relative
-- to whether nothing has read it yet.
--
-- WHAT THIS IS NOT. Not a vector-search index — deliberately no `pgvector` extension and no
-- `vector` column type. At this catalog size (~1,500 rows), brute-force cosine similarity in
-- plain application code is milliseconds; the infrastructure `pgvector` provides (ANN indexes,
-- the `<=>` operator) solves a problem this catalog doesn't have yet. `match_vector` is a plain
-- `jsonb` array specifically so it can be read with an ordinary `select=`, the same as every
-- other column this repo reads through PostgREST — no RPC function, no extra round-trip shape.
-- Revisit only if the catalog grows an order of magnitude past where brute-force is comfortable.
--
-- WHEN THIS GETS WRITTEN. Per Phase 5's activation-gated hook: whenever a write leaves the
-- row's `is_active` as true — whether it was already true (a routine refresh_opportunities.py
-- pass) or is becoming true right now (the activation endpoint). A row that stays inactive
-- (a fresh scrape insert, a console edit on a pending row) is skipped — it may never activate,
-- and computing its embedding early is often wasted cost. See Phase 5's write-path table for
-- the full breakdown by call site.
--
-- DEGRADATION (why nothing breaks before this is run). Until this file is run, semantic
-- recall (Phase 5) is simply not buildable against real data yet — there is nowhere to write
-- or read a vector from. Any write path that tries to include `match_vector`/
-- `match_vector_hash` in a PATCH against a table that doesn't have them yet gets a PostgREST
-- 400 on the whole request if the column is included unconditionally — so, matching every
-- other schema file in this repo, the write path must detect the missing column, drop it from
-- the payload, and log once, rather than fail the whole write. Today's matching pipeline
-- (`inferSubjects` + keyword scoring) is completely unaffected either way — it does not read
-- these columns and never will, since it is being retired by the same phase that introduces
-- them (see Phase 6).
--
-- Idempotent: `add column if not exists`, safe to re-run.

alter table public.opportunities
  add column if not exists match_vector jsonb,
  add column if not exists match_vector_hash text,
  add column if not exists match_vector_computed_at timestamptz;

-- Recall's real query shape is "give me every active row's vector" (a full-table read into
-- the in-process cache, per Phase 5), not a lookup by vector value — so there is no index on
-- match_vector itself. This partial index only speeds up the one operational query worth
-- optimizing: "which active rows still have no vector yet" (e.g. right after this migration
-- runs, or after a bulk activation).
create index if not exists opportunities_match_vector_missing_idx
  on public.opportunities (id)
  where is_active = true and match_vector is null;

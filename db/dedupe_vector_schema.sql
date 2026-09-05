-- db/dedupe_vector_schema.sql — one-time manual DDL (run in the Supabase SQL editor).
--
-- Adds the catalog-side DEDUPE embedding columns to `opportunities`. This is the duplicate-
-- detection vector (the similarity half of the scraper gate), NOT the recall match_vector — a
-- different embedding, computed from different fields, for a different job. The two live in
-- separate columns and never mix (cosine between them is meaningless).
--
--   dedupe_vector       jsonb        NULL   -- the embedding itself, as a plain float array
--   dedupe_vector_hash  text         NULL   -- content hash of the fields it was computed from
--   dedupe_vector_computed_at  timestamptz  NULL   -- when it was last (re)computed, for debugging
--
-- WHY THIS EXISTS / WHAT MOVED. The dedupe embedding used to live in a repo-root JSONL sidecar
-- (`catalog_embeddings.jsonl`), the "file now, table later" shape the leads queue uses. That file
-- was per-checkout local state: a fresh clone (or a removed worktree) had no index, so the
-- scraper's dedupe HINT went dark and db_health_check read "0% covered" until someone re-ran the
-- paid build. It now lives on the catalog row itself — computed once at ACTIVATION, backfillable
-- ad-hoc (build_catalog_embeddings.py), and read straight out of the catalog the scraper already
-- loads. This is the exact move `match_vector` already made; see db/match_vector_schema.sql.
--
-- WHAT THE HASH IS FOR. `dedupe_vector` is computed from combined_reader.default_representation
-- (name + org + type + summary + eligibility) and is only ever recomputed when that text actually
-- changes. `dedupe_vector_hash` records the hash of that representation at the time the vector was
-- computed, so the write path can cheaply check "did anything the vector depends on change" before
-- paying for a new embedding call. Same exact-identity freshness pattern match_vector uses — so the
-- activation hook and the ad-hoc backfill agree on when a row is stale, and no row re-embeds forever.
--
-- WHAT THIS IS NOT. Not a vector-search index — deliberately no `pgvector`, no `vector` column
-- type. At this catalog size (~1,500 rows) brute-force cosine in plain Python is microseconds, and
-- the dedupe query is "nearest existing row to ONE new candidate", a handful of dot products, not a
-- catalog-wide ANN search. `dedupe_vector` is plain `jsonb` so it reads with an ordinary `select=`,
-- the same as `match_vector`.
--
-- WHEN THIS GETS WRITTEN. At activation (a human makes a scraped/queued row LIVE) and by the ad-hoc
-- backfill. A row that stays inactive is skipped — a pending-review scrape/edit may never activate,
-- so embedding it early is wasted cost. Same activation-gated rule as match_vector.
--
-- DEGRADATION (why nothing breaks before this is run). Until this file is run, the scraper's dedupe
-- HINT is simply empty (the gate degrades to classify + metadata, which is exactly how it behaved
-- with no JSONL index — a hint, never a reject, so no duplicate is admitted that a reviewer could
-- not have caught anyway). The write paths detect the missing column, drop it from the payload, and
-- log once, rather than fail the whole write — matching every other schema file in this repo.
--
-- Idempotent: `add column if not exists`, safe to re-run.

alter table public.opportunities
  add column if not exists dedupe_vector jsonb,
  add column if not exists dedupe_vector_hash text,
  add column if not exists dedupe_vector_computed_at timestamptz;

-- The dedupe read's real query shape is "give me every active row's vector" (a full-table read
-- into the scraper's in-process gate index), not a lookup by vector value — so there is no index on
-- dedupe_vector itself. This partial index only speeds up the one operational query worth
-- optimizing: "which active rows still have no dedupe vector yet" (e.g. right after this migration
-- runs, or after a bulk activation).
create index if not exists opportunities_dedupe_vector_missing_idx
  on public.opportunities (id)
  where is_active = true and dedupe_vector is null;

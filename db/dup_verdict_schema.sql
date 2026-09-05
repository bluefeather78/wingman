-- dup_verdict — the single resolved dedupe verdict per opportunity row.
-- Phase 1 of docs/plans/DEDUPE_SIMPLIFICATION_PLAN.md. One manual step in the Supabase SQL editor
-- (PostgREST has no DDL endpoint, so nothing in this repo can run it).
--
-- Shape (written by dedupe_resolve.py / dup_verdict.Verdict.as_dict):
--   {
--     "confidence":   "certain" | "likely" | "possible",
--     "duplicate_of": "<survivor row id>",
--     "name":  "<survivor name>",
--     "url":   "<survivor url>",
--     "tier":  "proof" | "confident" | "adjudicate" | "hint" | "sibling",  -- raw engine tier
--     "cosine": <float or null>,
--     "reasons": ["cos=0.963", "name=same", "fields=agree", ...],
--     "sibling": false
--   }
-- NULL means "no suspected duplicate" (also the correct value for a row a reviewer has
-- confirmed is NOT a duplicate — see plan §3.4). This column REPLACES the dedupe role of the
-- append-only `dup_candidates` list; `duplicate_of` (the human-CONFIRMED survivor) is separate
-- and unchanged.
--
-- Idempotent: `add column if not exists` is a no-op if it already exists. Nothing reads this
-- column until Phase 2, so running the file early is safe and running it late only delays the
-- shadow-mode writer's PATCH (which degrades to a logged warning, never an error).

alter table opportunities
  add column if not exists dup_verdict jsonb;

-- Partial index so the review console can cheaply list "rows with a suspected duplicate"
-- (the filter that replaces the separate suspected_duplicate tab in Phase 2/5).
create index if not exists opportunities_dup_verdict_idx
  on opportunities ((dup_verdict->>'confidence'))
  where dup_verdict is not null;

-- Moderation state for user-submitted opportunities.
--
-- ONE-TIME MANUAL STEP: paste this into the Supabase SQL editor and run it, exactly like
-- db/subscription_schema.sql and db/user_costs_schema.sql. PostgREST has no DDL endpoint, so
-- nothing in this repo can apply it.
--
-- Until it runs, server.py degrades: the insert is retried with the base columns only, one
-- warning naming this file is logged, and submissions still land at is_active = false.
-- Nothing breaks; the review queue just can't show provenance or duplicate hints.

-- Why a separate column rather than reusing something that already exists:
--   * `state`         is the 2-letter US state code (see scrape_opportunities.py).
--   * `review_status` is check_reviews.py's ORG LEGITIMACY verdict, already shown to
--                     students in the app. Overloading it would put a moderation value
--                     in front of end users.
-- Both names are taken and mean something else. Hence `moderation_status`.
ALTER TABLE opportunities
  ADD COLUMN IF NOT EXISTS moderation_status text,
  ADD COLUMN IF NOT EXISTS submitted_by      text,
  ADD COLUMN IF NOT EXISTS submitted_at      timestamptz,
  ADD COLUMN IF NOT EXISTS reviewed_by       text,
  ADD COLUMN IF NOT EXISTS reviewed_at       timestamptz,
  ADD COLUMN IF NOT EXISTS duplicate_of      text,
  ADD COLUMN IF NOT EXISTS dup_candidates    jsonb,
  ADD COLUMN IF NOT EXISTS quality_flags     jsonb,
  -- The AI extraction returns more than the catalog has columns for (apply_url,
  -- requirements, meta, note). Parking it here keeps it visible to a reviewer without
  -- widening the student-facing catalog schema. Note that apply_url/apply_label/meta/
  -- requirements/description are NOT columns on this table — an earlier version of
  -- _insert_user_opportunity wrote them directly and every insert 400'd as a result.
  ADD COLUMN IF NOT EXISTS submission_payload jsonb,
  -- WHY the human decided (added 2026-08-25, so this file must be RE-RUN — it is
  -- idempotent). Written by the console's Reject flow as "code" or "code: note"
  -- (codes: duplicate, third-party-url, wrong-page, dead-link, not-a-fit, low-quality,
  -- other), auto-filled for the Duplicate button, cleared on Restore. This is labeled
  -- training data for the scraper: grade_scraper_batch.py fixtures read it to map each
  -- rejection to the scraper failure mode that caused it, instead of re-inferring the
  -- reason from row content after the fact. Until the column exists the console still
  -- rejects fine — the reason is dropped with a notice, never the whole verdict.
  ADD COLUMN IF NOT EXISTS moderation_reason text;

-- moderation_status is deliberately NOT a merge of is_active. The two are independent:
--   is_active          = is this visible to students right now?
--   moderation_status  = has a human adjudicated it, and what did they decide?
-- Keeping them separate keeps "approved but temporarily hidden" expressible, and — the
-- reason this column exists at all — lets a REJECTED row stay in the table (so its URL
-- keeps blocking re-submission) while dropping out of the reviewer's queue. With only the
-- boolean, a rejected row sits at is_active=false forever and gets re-triaged every time
-- the queue is opened.
--
--   pending_review      awaiting a human. The only value server.py ever writes on insert.
--   approved            a human accepted it; only now may is_active be flipped true.
--   rejected            a human declined it. Stays in the table as a dedupe tombstone.
--   duplicate           superseded by another row; see duplicate_of.
--   suspected_duplicate flag-in-place: the strict offline dedupe sweep suspects this is a
--                       duplicate but left it is_active=true (a suspected dupe is still a
--                       working opportunity; hiding it on a guess would pull a real program
--                       from students). Surfaces in the console's 'Flagged' queue slice —
--                       the ONE slice that lists live rows — for a human to release
--                       (approve, stays live) or confirm (duplicate, which then deactivates).
-- NOTE: this CHECK is re-run idempotently (DROP + ADD). Re-run this file after adding the
-- suspected_duplicate value, or a write of it 400s.
ALTER TABLE opportunities
  DROP CONSTRAINT IF EXISTS opportunities_moderation_status_check;
ALTER TABLE opportunities
  ADD CONSTRAINT opportunities_moderation_status_check
  CHECK (moderation_status IS NULL OR moderation_status IN
         ('pending_review', 'approved', 'rejected', 'duplicate', 'suspected_duplicate'));

-- Existing rows predate moderation entirely. Leaving them NULL rather than backfilling
-- 'approved' keeps "never went through this workflow" distinguishable from "a human
-- approved this" — the same reasoning as the NULL trial_ends_at case in server.py, where
-- reading NULL as a decided value would have been wrong.

-- Backs the review queue's ordering and the per-user "what have they sent us" lookup.
CREATE INDEX IF NOT EXISTS opportunities_moderation_idx
  ON opportunities (moderation_status, submitted_at DESC)
  WHERE moderation_status IS NOT NULL;

CREATE INDEX IF NOT EXISTS opportunities_submitted_by_idx
  ON opportunities (submitted_by)
  WHERE submitted_by IS NOT NULL;

-- Normalized-URL index. Deliberately NOT UNIQUE.
--
-- A unique index here looks obviously right and is wrong: one URL can legitimately back
-- several distinct opportunities. In the current catalog spicestanford.smapply.io is the
-- application portal for six separate programs (Stanford E-Japan, Sejong Korea Scholars,
-- Stanford E-China, China Scholars, Reischhauer, Scholars Program), and
-- girlswhocode.com/programs/summer-immersion-program backs two. A unique constraint would
-- make those permanently unsubmittable. It also would not build today — 30 groups of rows
-- currently share a normalized URL.
--
-- So uniqueness is enforced on (normalized URL + name) in application code instead, by
-- url_dedupe.find_duplicates(); this index just makes that lookup and the duplicate audit
-- below cheap. It indexes a normalized form because the stored url column is raw by design
-- (100 rows hold case-sensitive paths that must not be folded). It is deliberately a weaker
-- normalization than url_dedupe.match_key() — a Postgres index expression cannot strip
-- tracking parameters — so match_key() stays the stricter check.
CREATE INDEX IF NOT EXISTS opportunities_url_normalized_idx
  ON opportunities (
    regexp_replace(
      regexp_replace(lower(btrim(url)), '^https?://(www\.)?', ''),
      '/+$', ''
    )
  )
  WHERE url IS NOT NULL AND url <> '';

-- Because uniqueness is not enforced by the database, the check-then-insert in
-- _insert_user_opportunity remains two round-trips and two simultaneous submissions of the
-- same URL can still both pass. That residual is accepted: the loser lands in the review
-- queue flagged as a duplicate of the winner, which is exactly where a human would catch it.
--
-- AUDIT: the dedupe this replaces was broken (it compared a lowercased URL against
-- un-normalized stored values), so real duplicates were let through — 'Clinical Summer
-- Internship' and 'Summer Scholars' each appear twice, JSHS three times under three name
-- variants. Find the survivors with the query below, then mark the losers
-- moderation_status='duplicate' with duplicate_of pointing at the row you keep. Read the
-- results by hand: same-URL groups with genuinely DIFFERENT names are the shared-portal
-- case above and must be left alone.
--
--   SELECT regexp_replace(regexp_replace(lower(btrim(url)), '^https?://(www\.)?', ''),
--                         '/+$', '') AS norm,
--          count(*) AS n, array_agg(id) AS ids, array_agg(name) AS names
--   FROM opportunities
--   WHERE url IS NOT NULL AND url <> ''
--   GROUP BY 1 HAVING count(*) > 1
--   ORDER BY 2 DESC;

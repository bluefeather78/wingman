-- Layer-1 SEO program pages: durable per-opportunity page state on `opportunities`.
-- Backs the public /opportunities/<slug> pages, /sitemap.xml, and the admin console's SEO tab.
--
-- ONE-TIME MANUAL STEP: paste this into the Supabase SQL editor and run it. PostgREST has no
-- DDL endpoint, so nothing in this repo can apply it for you.
--
-- Until it is run, the system degrades rather than breaking:
--   * ops/core.evaluate_seo_pages() detects the missing columns and returns a setup notice
--     (the console's SEO tab shows "run db/seo_pages_schema.sql" instead of an error);
--   * /sitemap.xml still answers, listing only the static pages (homepage etc.), because there
--     is no seo_status column to select indexed rows from;
--   * a public /opportunities/<slug> page cannot resolve by slug (seo_slug is unset), but
--     /opportunities/<id> still renders — the page's index/noindex decision is computed LIVE
--     from the row and needs none of these columns.
--
-- The ALTER block at the bottom is NOT redundant with the adds above. If a column is added
-- here it must be added to BOTH halves: `add column if not exists` is a no-op against a table
-- that already has the column in an older shape, and PostgREST 400s an entire upsert on one
-- unknown key — so a single missing column means evaluate_seo_pages() writes NOTHING and the
-- console reads as "no pages" rather than "every write failed". Same trap as
-- db/link_health_schema.sql, db/mailing_list_schema.sql and db/user_activity_schema.sql.

alter table opportunities
  -- The page's stable URL slug, e.g. 'mit-primes'. Assigned once by evaluate_seo_pages() and
  -- then KEPT even if the program's name later changes — a live URL must not move, or its
  -- accrued ranking and any inbound links are lost. Unique across the table (see the index
  -- below); collisions get the row id appended by wingman/seo_pages.assign_unique_slug.
  add column if not exists seo_slug text,

  -- The last evaluation's verdict: 'indexed' (cleared the composite bar — in the sitemap,
  -- crawlable) or 'awaiting_info' (renders, but noindex until it has more data). NULL means the
  -- row has never been evaluated, i.e. no page has been published for it yet. The PAGE itself
  -- recomputes this live on each request, so a page that has gone thin is never served as
  -- indexed even if this column still says so; this column is the durable snapshot the
  -- dashboard counts and the sitemap reads, and it re-syncs on the next evaluation.
  add column if not exists seo_status text,

  -- Display score (0..6), verified-deadline-weighted. Lets the console sort "closest to
  -- publishable" first. Not the gate — the gate is the signal count in wingman/seo_pages.py.
  add column if not exists seo_content_score int,

  -- What the row still needs to be publishable, as a JSON array of short labels
  -- (e.g. ["deadline","application checklist (3+ steps)"]). Drives the console's
  -- "awaiting info" drill-down so an operator sees exactly what to fill in. jsonb, not text[],
  -- to match how the rest of this table stores small lists (important_dates, action_items).
  add column if not exists seo_missing_fields jsonb,

  -- When evaluate_seo_pages() last scored this row.
  add column if not exists seo_evaluated_at timestamptz;

-- seo_slug is the page route's primary lookup key and must be unique. Partial + unique so it
-- costs nothing on the many rows that are not yet evaluated (NULL), and so a duplicate slug is
-- rejected at write time rather than silently serving two programs at one URL.
create unique index if not exists opportunities_seo_slug_key
  on opportunities (seo_slug)
  where seo_slug is not null;

-- The sitemap query is `seo_status = 'indexed'` and the dashboard buckets by seo_status; both
-- are a slice of the table, so a partial index keeps them cheap.
create index if not exists opportunities_seo_status_idx
  on opportunities (seo_status)
  where seo_status is not null;

-- ---------------------------------------------------------------------------------------
-- ALTER block. Re-runnable; add every new column here as well as above. See the header.
-- ---------------------------------------------------------------------------------------
alter table opportunities add column if not exists seo_slug text;
alter table opportunities add column if not exists seo_status text;
alter table opportunities add column if not exists seo_content_score int;
alter table opportunities add column if not exists seo_missing_fields jsonb;
alter table opportunities add column if not exists seo_evaluated_at timestamptz;

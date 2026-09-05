-- Link health columns on `opportunities`, for check_links.py.
--
-- ONE-TIME MANUAL STEP: paste this into the Supabase SQL editor and run it. PostgREST has
-- no DDL endpoint, so nothing in this repo can apply it for you.
--
-- Until it is run, check_links.py still works and still deactivates dead rows — it detects
-- the missing columns, drops them from its PATCH, and says so once. What you lose is the
-- staleness filter (there is no link_checked_at to compare against), so every run re-checks
-- the whole catalog. That is free, so it degrades to "slower", not "broken".
--
-- The ALTER block at the bottom is not redundant with the CREATE-style adds above it. If a
-- column is ever added here, it must be added to BOTH halves: `add column if not exists` is
-- a no-op against a table that already has the column in an older shape, and PostgREST 400s
-- an entire PATCH on one unknown key — so a single missing column means check_links.py
-- records NOTHING, and the console reads as "every link is fine" rather than "every write
-- failed". Same trap as db/mailing_list_schema.sql and db/user_activity_schema.sql.

alter table opportunities
  -- 'live' | 'dead' | 'unverified', mirroring url_validate.LIVE/DEAD/UNVERIFIED.
  add column if not exists link_status text,

  -- What the last check actually saw. An HTTP status as text ('404', '403', '200'), or a
  -- short symbolic reason: 'NXDOMAIN' (hostname does not resolve), 'malformed',
  -- 'SSLCertVerificationError', 'TimeoutError'. Text rather than int precisely because the
  -- non-HTTP reasons are the interesting ones — a reviewer needs to tell "the page is gone"
  -- apart from "our TLS stack could not talk to it", and an int column cannot say that.
  add column if not exists link_status_code text,

  -- When the URL was last checked. Drives the staleness filter, exactly like
  -- last_reviewed_at does for check_reviews.py.
  add column if not exists link_checked_at timestamptz,

  -- When it FIRST went dead, preserved across later checks. Not derivable from
  -- link_checked_at: that is stamped on every pass, so without this column a link broken in
  -- March and one broken this morning look identical, and there is no way to tell a
  -- long-rotted row from a site that is mid-migration and will be back on Monday. Cleared
  -- when the URL is seen live again.
  add column if not exists link_dead_since timestamptz,

  -- The review-queue state for a link finding (added 2026-09-02, when the agent stopped
  -- deactivating on its own). NULL = no open finding; 'pending' = the agent found a problem
  -- and a person needs to look; 'cleared' = a person reviewed it and left the row as-is;
  -- 'deactivated' = a person reviewed it and took the row out of the catalog. The agent only
  -- ever writes 'pending', and only over a NULL — it never overturns a human 'cleared' or
  -- 'deactivated'. This is what the console's Links tab reads: the queue is exactly the rows
  -- at 'pending'. Text, not a bool, precisely so a cleared finding is distinguishable from one
  -- that was never raised.
  add column if not exists link_review_status text;

-- Finding rows with a link finding is the console's most common query against these
-- columns, and it is always a small slice of a large table.
create index if not exists opportunities_link_status_idx
  on opportunities (link_status)
  where link_status is not null;

-- The Links tab's queue query is `link_review_status = 'pending'`, run on every load. A
-- partial index keeps it a slice-of-a-slice, since almost every row is NULL here.
create index if not exists opportunities_link_review_status_idx
  on opportunities (link_review_status)
  where link_review_status is not null;

-- ---------------------------------------------------------------------------------------
-- ALTER block. Re-runnable; add every new column here as well as above. See the header.
-- ---------------------------------------------------------------------------------------
alter table opportunities add column if not exists link_status text;
alter table opportunities add column if not exists link_status_code text;
alter table opportunities add column if not exists link_checked_at timestamptz;
alter table opportunities add column if not exists link_dead_since timestamptz;
alter table opportunities add column if not exists link_review_status text;

-- user_activity — one row per user per UTC day they used the app.
--
-- WHY THIS EXISTS: nothing else in this repo records "user X did something at time T".
-- Every other user fact is either current state (the `users` row and its `data` jsonb) or
-- a cost rollup (`user_costs`). That makes DAU/WAU/MAU and retention uncomputable, and
-- the two obvious substitutes are both wrong:
--
--   * `users.updated_at` is declared `default now()` with NO trigger, and
--     update_user_data() never writes it — it equals created_at on practically every row.
--     A "last active" metric built on it looks plausible and is fiction.
--   * `user_costs` only sees BILLED AI calls. A student who opened the app every day and
--     worked their tracker costs $0 and reads as inactive; in mock mode (no API key) the
--     signal disappears entirely.
--
-- Run this once in the Supabase SQL editor. Until it exists, server.py logs a single
-- warning, touch_user_activity() latches off, and the admin console's Metrics view hides
-- the activity chart and the retention panel. Every state metric there still works —
-- those come from the `users` table and need no migration at all.

-- Grain: (userid, day) — a DAILY ROLLUP, not an event log. Same decision
-- record_interactive_cost() made, and for a stronger reason: this table takes every
-- authenticated request, not just the ones that cost money, so a row per event would grow
-- without bound for data that is only ever read in aggregate.
create table if not exists user_activity (
    userid      text not null,
    day         date not null,
    -- Total authenticated requests from this user on this day. Accumulated in memory and
    -- flushed periodically (see _activity_buffer in server.py), so a process that dies
    -- between flushes loses at most the last flush interval's counts. DAU/WAU/retention
    -- only need the row to EXIST, so they are unaffected by that; only `hits` and
    -- `surfaces` are approximate, and they are colour, not headline figures.
    hits        integer not null default 0,
    -- surface -> hits, e.g. {"login": 1, "data_load": 3, "data_save": 11}. Keeps
    -- "opened the app" (data_load) distinct from "changed something" (data_save) without
    -- a second table.
    surfaces    jsonb   not null default '{}'::jsonb,
    first_at    timestamptz,
    last_at     timestamptz,
    primary key (userid, day)
);

create index if not exists user_activity_day_idx on user_activity (day desc);

-- Same posture as `users` and `user_costs`: RLS enabled with NO policies, so the anon key
-- gets zero access and only server.py's service-role calls can read or write. This is a
-- log of what identified minors did and when; it must never be reachable from the browser.
alter table user_activity enable row level security;


-- ---------------------------------------------------------------------------
-- ALTER block — for a table that already exists in an older shape.
--
-- `create table if not exists` above is a NO-OP against an existing table, and PostgREST
-- rejects an entire insert on one unknown key. A single missing column would therefore
-- mean NOTHING is ever recorded, and the Metrics view would read as "nobody used the app"
-- rather than "every write failed". Add a column above and you must add it here too.
alter table user_activity add column if not exists hits     integer not null default 0;
alter table user_activity add column if not exists surfaces jsonb   not null default '{}'::jsonb;
alter table user_activity add column if not exists first_at timestamptz;
alter table user_activity add column if not exists last_at  timestamptz;

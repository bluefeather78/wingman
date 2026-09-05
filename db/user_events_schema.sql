-- user_events — an APPEND-ONLY event log: one row per thing a student did that reveals a
-- preference (an impression, an open, a save, a track, an apply-click, a dismiss/untrack, a
-- search, a tag-filter). Run this once in the Supabase SQL editor before event capture
-- starts recording.
--
-- WHY THIS EXISTS, and why it is a SEPARATE table from user_activity:
--   * user_activity is a DAILY ROLLUP (one row per user per day) built for DAU/WAU/MAU and
--     retention. It deliberately throws away per-event grain — it cannot say WHICH
--     opportunity a student saved, in what rank it was shown, or that they dismissed one.
--   * The matcher's learn-from-behavior loop needs exactly that grain: a revealed-preference
--     signal (recent saves up, dismisses down) and a "not interested -> re-rank" loop. None
--     of it is reconstructable after the fact — an unlogged click is gone forever, the same
--     reason the metrics daily-snapshot ships before anything reads it. So capture ships
--     EARLY even though the consumer (a per-user preference rollup) comes weeks later.
--
-- Grain: ONE ROW PER EVENT (not a rollup). This table is written from the request path via a
-- buffered background flush (see _events_buffer in app/core.py), a plain batch INSERT with no
-- read-modify-write — an append-only log has no existing row to merge into, so it is strictly
-- cheaper than user_activity's upsert. A process that dies between flushes loses at most one
-- flush interval of events; there is no correctness cost, only a gap in a stream that is only
-- ever read in aggregate.
create table if not exists user_events (
    id             bigint generated always as identity primary key,
    -- The acting account, lowercased (str(userid).strip().lower()), exactly like every other
    -- user-keyed table here. Events with no identified user are NOT recorded — a signed-out
    -- caller cannot be attributed, the same residual the cost attribution reports as
    -- unattributed. NOT a foreign key: users lives in its own table with RLS and this log
    -- must survive a user row being edited/removed without a cascade surprise.
    userid         text not null,
    -- When the event happened, server-stamped on arrival into the buffer (record_user_events
    -- sets it; the default now() is only a fallback for a hand-written insert). Client-supplied
    -- timestamps are not trusted (clock skew, replay); arrival order is all the aggregate reads
    -- this feeds need.
    ts             timestamptz not null default now(),
    -- The preference gradient, weakest -> strongest signal:
    --   'impression'  the row was SHOWN (context carries rank/tier/kind/query) — the weak,
    --                 high-volume denominator every stronger signal is measured against
    --   'open'        the student opened the card / detail
    --   'save'        saved-for-later
    --   'track'       added to the Quest Log (a real commitment)
    --   'apply_click' clicked through to apply / learn more (strongest positive)
    --   'dismiss'     "not interested" (explicit negative) — context carries the reason
    --   'untrack'     removed from the Quest Log (explicit negative)
    --   'search'      ran a search (context.query carries the text)
    --   'tag_filter'  toggled a profile-tag facet (context.tag)
    -- Kept as free text, not an enum: a new action must be a code change here, not a DDL
    -- migration this repo cannot run through PostgREST.
    action         text not null,
    -- The opportunity this event is about, or NULL for events that are not about one row
    -- (search, tag_filter). Text to match opportunities.id; NOT a foreign key, same reason
    -- as userid — a catalog row can be deactivated or re-found without orphaning history.
    opportunity_id text,
    -- Per-action detail: {rank, tier, kind, query, reason, ...}. search.query is the
    -- SENSITIVE field (free text typed by a minor), which is the whole reason this table is
    -- service-role-only and never reachable from the browser.
    context        jsonb not null default '{}'::jsonb
);

-- The two reads this feeds: "this user's recent events, newest first" (the revealed-
-- preference rollup) and "everything about this opportunity" (per-row engagement).
create index if not exists user_events_user_ts_idx on user_events (userid, ts desc);
create index if not exists user_events_opp_idx      on user_events (opportunity_id);
create index if not exists user_events_action_idx   on user_events (action);

-- Same posture as users / user_costs / user_activity: RLS ENABLED with NO policies, so the
-- anon key gets zero access and only app/core.py's service-role calls can read or write. This
-- is a log of what identified minors did, including their search text; it must never be
-- reachable from the browser.
alter table user_events enable row level security;


-- ---------------------------------------------------------------------------
-- ALTER block — for a table that already exists in an older shape.
--
-- `create table if not exists` above is a NO-OP against an existing table, and PostgREST
-- rejects an entire insert on one unknown key. A single missing column would therefore mean
-- NOTHING is ever recorded and the capture would read as "nobody used the app" rather than
-- "every write failed". Add a column above and you must add it here too.
alter table user_events add column if not exists ts             timestamptz not null default now();
alter table user_events add column if not exists action         text not null default '';
alter table user_events add column if not exists opportunity_id text;
alter table user_events add column if not exists context        jsonb not null default '{}'::jsonb;

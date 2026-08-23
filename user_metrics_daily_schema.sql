-- user_metrics_daily — a once-a-day photograph of the activation funnel and plan mix.
--
-- WHY THIS EXISTS: every state metric in the Metrics view is computed from the CURRENT
-- `users` table. The `data` jsonb holds one profile, not a history of one — so
-- "how many users had a meaningful profile on 2026-08-01" is not merely unqueried, it is
-- UNRECOVERABLE. Nothing anywhere stored that fact.
--
-- The only fix is to start writing it down. That makes this table worth running on day
-- one even though nothing reads it for weeks: every day it is not running is a day
-- permanently missing from every trend line this dashboard will ever draw.
--
-- Run this once in the Supabase SQL editor. Until it exists, server.py logs a single
-- warning, snapshot writing latches off, and the console reports snapshots_ready: false
-- and hides trend lines. Current-value tiles are unaffected.

-- Grain: one row per UTC day. Today's row is re-written (upserted) as the day progresses;
-- past days are frozen at whatever they last read. Note the UTC day rolls at 5pm Pacific
-- — the same boundary that made a "today" cost figure read $0.00 every evening, which is
-- why the console leads with the latest day that has data rather than with "today".
create table if not exists user_metrics_daily (
    day          date primary key,
    accounts     integer not null default 0,
    -- Activation-funnel stage key -> count of accounts at or past that stage.
    -- Stored as jsonb rather than a column per stage so adding stage 12 needs no
    -- migration; the stage list lives in FUNNEL_STAGES in server.py.
    funnel       jsonb   not null default '{}'::jsonb,
    -- subscription_status -> count. 'trial' | 'active' | 'beta' | 'canceled' | 'past_due'.
    by_status    jsonb   not null default '{}'::jsonb,
    mrr_usd      numeric(12,2) not null default 0,
    -- NULL, not 0, when user_activity has not been set up yet: zero would be
    -- indistinguishable from a genuinely dead day and would poison the chart forever,
    -- since these rows can never be recomputed.
    dau          integer,
    wau          integer,
    mau          integer,
    captured_at  timestamptz not null default now()
);

create index if not exists user_metrics_daily_day_idx on user_metrics_daily (day desc);

-- RLS on, no policies — service-role only, same as every other operator table here.
alter table user_metrics_daily enable row level security;


-- ---------------------------------------------------------------------------
-- ALTER block — see the note in user_activity_schema.sql. `create table if not exists`
-- does nothing to an existing table, and one unknown key 400s the whole write.
alter table user_metrics_daily add column if not exists accounts    integer not null default 0;
alter table user_metrics_daily add column if not exists funnel      jsonb   not null default '{}'::jsonb;
alter table user_metrics_daily add column if not exists by_status   jsonb   not null default '{}'::jsonb;
alter table user_metrics_daily add column if not exists mrr_usd     numeric(12,2) not null default 0;
alter table user_metrics_daily add column if not exists dau         integer;
alter table user_metrics_daily add column if not exists wau         integer;
alter table user_metrics_daily add column if not exists mau         integer;
alter table user_metrics_daily add column if not exists captured_at timestamptz not null default now();

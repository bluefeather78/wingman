-- agent_runs — one row per offline-agent run: what it processed, what it cost, what broke.
--
-- WHY THIS FILE EXISTS. SECURITY_HARDENING_PLAN.md S1-9 flagged that this table had NO
-- schema file at all — its only written-down definition was a docstring in
-- agents/check_deadlines.py, and that docstring has no `enable row level security` line.
-- So whether the live table has RLS on is not knowable from this repository. Every other
-- table here enables it with no policies.
--
-- CONFIRM THE LIVE STATE in the Supabase dashboard (Table Editor -> agent_runs -> RLS
-- enabled). Writing this file does not turn RLS on by itself — somebody has to run it.
--
-- What is in these rows: which agents ran, when, how many rows they touched, and HOW MUCH
-- EACH RUN COST. No student data, so this is not the sensitive table `conversations` is —
-- but the spend history of the business is not something the browser's anon key should be
-- able to read either, and "no policies" costs nothing since every reader here is the
-- localhost console using the service key.
--
-- Columns are the union of what the agents and ops/core.py actually write. Nothing reads a
-- column that is missing (the console projects rows defensively), so this is safe to run
-- against the table as it stands.
--
-- Run this once in the Supabase SQL editor. Safe to run more than once.

create table if not exists agent_runs (
    id                  bigint generated always as identity primary key,
    agent               text not null,        -- the db_agent literal; see AGENT_CONFIGS_SCHEMA
    mode                text,                 -- 'full' | 'sample' | 'dry-run' | '<x>-commit' | ...
    started_at          timestamptz not null,
    finished_at         timestamptz,
    items_processed     integer default 0,
    items_added         integer default 0,
    items_updated       integer default 0,
    items_deleted       integer default 0,
    emails_subscribed   integer default 0,
    errors              integer default 0,
    cost_usd            numeric,
    total_web_searches  integer default 0,
    silent_search_count integer default 0,
    notes               text
);

alter table agent_runs enable row level security;

create index if not exists agent_runs_agent_started_idx on agent_runs (agent, started_at desc);


-- ---------------------------------------------------------------------------
-- ALTER block — for the table as it exists today, created from the docstring in
-- agents/check_deadlines.py, which predates several of these columns.
-- ---------------------------------------------------------------------------
alter table agent_runs add column if not exists mode                text;
alter table agent_runs add column if not exists finished_at         timestamptz;
alter table agent_runs add column if not exists items_processed     integer default 0;
alter table agent_runs add column if not exists items_added         integer default 0;
alter table agent_runs add column if not exists items_updated       integer default 0;
alter table agent_runs add column if not exists items_deleted       integer default 0;
alter table agent_runs add column if not exists emails_subscribed   integer default 0;
alter table agent_runs add column if not exists errors              integer default 0;
alter table agent_runs add column if not exists cost_usd            numeric;
alter table agent_runs add column if not exists total_web_searches  integer default 0;
alter table agent_runs add column if not exists silent_search_count integer default 0;
alter table agent_runs add column if not exists notes               text;

-- The point of this file.
alter table agent_runs enable row level security;

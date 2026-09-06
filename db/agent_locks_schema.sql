-- agent_locks — the cross-process mutex that stops two catalog agents running at once.
--
-- WHY THIS FILE EXISTS. PRODUCTION_READINESS_PLAN.md Phase 3, audit finding 4.2. Four agents
-- INSERT into `opportunities` (scrape_opportunities, mine_hub_pages, harvest_names,
-- refind_dead_links) and each mints its ids as `ec<max+1>` from an in-memory snapshot taken at
-- run start. Two overlapping runs therefore mint THE SAME ids, the POST fails on the primary
-- key, and before Phase 3 the insert ladder turned that collision into a silently narrower
-- insert or a lost batch.
--
-- Nothing prevented the overlap. The only mutex in the pipeline was gemini_common's
-- `.gemini_web_search.lock`, which (a) is a local file, so it cannot see a run on another
-- machine or checkout, and (b) is acquired at the first SEARCH call — so mine_hub_pages, which
-- never searches, could run alongside the scraper quite happily. ops/core.running_gemini_search_agent
-- only refuses CONSOLE launches of agents flagged `uses_gemini_search`; a hand-run
-- `python -m agents.mine_hub_pages` was never checked at all.
--
-- HOW THE LOCK IS ATOMIC. Acquisition is a plain INSERT. `name` is the primary key, so the
-- second acquirer gets a 23505 unique violation from Postgres itself — there is no
-- read-then-write window to lose. Release is a DELETE guarded on `token`, so a run can only
-- ever release the lock it actually holds (a stale holder that comes back from the dead cannot
-- delete its successor's lock).
--
-- STALE LOCKS. A crashed run leaves its row behind, so every lock carries `expires_at`.
-- wingman/run_lock.py heartbeats it while the run is alive and steals a lock whose
-- `expires_at` has passed. That is why the DELETE-then-INSERT takeover path is safe: an
-- expired lock is by definition not being heartbeaten by anyone.
--
-- IF THIS TABLE DOES NOT EXIST, wingman/run_lock.py falls back to a local FILE lock and warns
-- loudly. That is strictly better than the pre-Phase-3 state (no lock at all for the agents
-- that do not search) and it keeps a fresh checkout runnable, but it only protects one
-- machine. Run this file to get the real thing.
--
-- Run this once in the Supabase SQL editor. Safe to run more than once.

create table if not exists agent_locks (
    name        text primary key,          -- the resource, e.g. 'catalog_insert'
    holder      text not null,             -- "<agent> pid <n> on <host>", for the error message
    token       text not null,             -- random per acquisition; release is guarded on it
    acquired_at timestamptz not null default now(),
    expires_at  timestamptz not null       -- heartbeated forward while the run lives
);

-- Same posture as every other table here: RLS on, no policies. The only reader/writer is an
-- offline agent or the localhost console, both holding the service key.
alter table agent_locks enable row level security;

create index if not exists agent_locks_expires_idx on agent_locks (expires_at);


-- ---------------------------------------------------------------------------
-- Opportunity id minting (the other half of finding 4.2).
-- ---------------------------------------------------------------------------
-- Catalog ids are TEXT, shaped `ec<n>` — not a bigint identity column — so a plain sequence
-- cannot be attached to `opportunities.id` directly. This mints the numeric half instead, and
-- `next_opportunity_id()` formats it. Being a sequence, two concurrent callers can never
-- receive the same number even if they somehow bypass the lock above: nextval is atomic and
-- does not roll back.
--
-- Seeded from the live maximum so it can never re-issue an id that already exists. The
-- 18220 floor matches next_id_generator's historical one.
do $$
declare
    start_at bigint;
begin
    select coalesce(max(nullif(regexp_replace(id, '^ec', ''), '')::bigint), 18220) + 1
      into start_at
      from opportunities
     where id ~ '^ec[0-9]+$';

    if not exists (select 1 from pg_class where relname = 'opportunity_id_seq') then
        execute format('create sequence opportunity_id_seq start with %s', start_at);
    else
        -- Never move the sequence BACKWARDS; only forward past any id inserted while the
        -- fallback minter was still in use.
        if start_at > (select last_value from opportunity_id_seq) then
            execute format('alter sequence opportunity_id_seq restart with %s', start_at);
        end if;
    end if;
end $$;

create or replace function next_opportunity_id(n integer default 1)
returns setof text
language sql
as $$
    select 'ec' || nextval('opportunity_id_seq')::text
      from generate_series(1, greatest(n, 1));
$$;

-- PostgREST exposes this at POST /rest/v1/rpc/next_opportunity_id with {"n": 25}.
-- wingman/run_lock.py:mint_ids calls it and falls back to the in-memory max+1 minter when the
-- function is absent (a checkout where this file has not been run).

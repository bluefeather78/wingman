-- bump_interactive_run / bump_user_cost — the two cost rollups, made idempotent.
--
-- WHY THIS FILE EXISTS. PRODUCTION_READINESS_PLAN.md Phase 4, perf_report finding 11
-- ("_interactive_rollup, _user_costs_rows — per-process id caches over read-modify-write
-- PATCHes: two workers lose each other's increments").
--
-- Both rollups had the same shape in app/core.py:
--
--     row_id = cache.get(key) or (SELECT id ... ) or (INSERT ...)     -- remember the row
--     SELECT counters FROM row WHERE id = row_id                      -- read
--     PATCH row SET counters = read_value + delta                     -- write
--
-- THREE ROUND TRIPS AND A RACE. The read and the write are separate statements, so two
-- writers that interleave between them both read N and both write N+1. One call's cost
-- vanishes. Inside one process a lock hid this; across two workers or two instances there is
-- no lock to take, and the per-process id cache makes it worse rather than better — each
-- worker independently believes it owns the row.
--
-- What is lost is money, not latency. The per-user daily budget and the global circuit
-- breaker (S0-5) read these totals; an undercount is a spend cap that lets more through than
-- it should. (budget.note_spend() fires synchronously and separately, so the CAPS themselves
-- were never on this path — but the console's figures, which is how anybody notices a
-- runaway, were.)
--
-- The fix is INSERT ... ON CONFLICT DO UPDATE SET col = table.col + excluded.col: one
-- statement, atomic, and idempotent in the sense that matters here — applying the same delta
-- twice adds it twice, but two concurrent deltas can never lose one. UPSERT alone would not
-- do: PostgREST's upsert REPLACES a conflicting row, and these counters must ADD, which is
-- the exact reason app/core.py's comment gives for the read-then-PATCH it is replacing.
--
-- Run this once in the Supabase SQL editor. Safe to run more than once.


-- ---------------------------------------------------------------------------
-- 1. agent_runs, for the two interactive rollup agents only.
-- ---------------------------------------------------------------------------
-- agent_runs IS AN APPEND-ONLY HISTORY. One row per run, an agent has run many times, and a
-- plain unique index on (agent, mode) would break every real agent immediately - a scraper
-- has run in 'full' mode dozens of times.
--
-- The interactive rollups are the exception, and they always were: record_interactive_cost()
-- writes agent='interactive_gemini'|'interactive_claude' with mode=<UTC date>, deliberately
-- reusing the run table so the console's charts pick the app's own spend up with no new table
-- (see the comment above INTERACTIVE_AGENTS in app/core.py). For those two agents the grain
-- really is one row per (agent, day), and a PARTIAL unique index says so without touching a
-- single row of anybody else's history.
--
-- The DO block first merges any duplicates that already exist, because the index cannot be
-- created while they do. Duplicates are possible today precisely because of the race this
-- file closes: two workers both finding no row and both inserting one. The merge keeps the
-- OLDEST row (the console's history links to ids) and adds the others' counters into it.
do $$
declare
    dupe record;
begin
    for dupe in
        select agent, mode, min(id) as keep_id, count(*) as n
          from agent_runs
         where agent in ('interactive_gemini', 'interactive_claude')
         group by agent, mode
        having count(*) > 1
    loop
        update agent_runs k
           set items_processed    = coalesce(k.items_processed, 0) + coalesce(s.items_processed, 0),
               cost_usd           = coalesce(k.cost_usd, 0) + coalesce(s.cost_usd, 0),
               total_web_searches = coalesce(k.total_web_searches, 0) + coalesce(s.total_web_searches, 0),
               errors             = coalesce(k.errors, 0) + coalesce(s.errors, 0),
               finished_at        = greatest(k.finished_at, s.finished_at)
          from (select sum(items_processed) as items_processed,
                       sum(cost_usd) as cost_usd,
                       sum(total_web_searches) as total_web_searches,
                       sum(errors) as errors,
                       max(finished_at) as finished_at
                  from agent_runs
                 where agent = dupe.agent and mode = dupe.mode and id <> dupe.keep_id) s
         where k.id = dupe.keep_id;

        delete from agent_runs
         where agent = dupe.agent and mode = dupe.mode and id <> dupe.keep_id;

        raise notice 'merged % duplicate rollup rows for %/%', dupe.n - 1, dupe.agent, dupe.mode;
    end loop;
end $$;

create unique index if not exists agent_runs_interactive_rollup_idx
    on agent_runs (agent, mode)
 where agent in ('interactive_gemini', 'interactive_claude');


create or replace function bump_interactive_run(
    p_agent    text,
    p_mode     text,
    p_calls    integer,
    p_cost     numeric,
    p_searches integer,
    p_notes    text default null
)
returns bigint
language plpgsql
as $$
declare
    run_id bigint;
begin
    -- Refuse anything that is not one of the two rollup agents. Without this the function is
    -- a way to corrupt an ordinary agent's run history: there is no unique index outside the
    -- partial one, so the ON CONFLICT below would never fire and every call would append a
    -- new row to somebody's audit log.
    if p_agent not in ('interactive_gemini', 'interactive_claude') then
        raise exception 'bump_interactive_run is only for the interactive rollup agents, not %',
                        p_agent;
    end if;

    insert into agent_runs (agent, mode, started_at, finished_at, items_processed,
                            cost_usd, total_web_searches, errors, notes)
    values (p_agent, p_mode, now(), now(), coalesce(p_calls, 0),
            coalesce(p_cost, 0), coalesce(p_searches, 0), 0, p_notes)
    on conflict (agent, mode) where agent in ('interactive_gemini', 'interactive_claude')
    do update set
        items_processed    = coalesce(agent_runs.items_processed, 0) + coalesce(excluded.items_processed, 0),
        cost_usd           = coalesce(agent_runs.cost_usd, 0) + coalesce(excluded.cost_usd, 0),
        total_web_searches = coalesce(agent_runs.total_web_searches, 0) + coalesce(excluded.total_web_searches, 0),
        -- Kept current so the row never reads as "interrupted" in the console, exactly as the
        -- old PATCH did.
        finished_at        = now()
    returning id into run_id;

    return run_id;
end;
$$;


-- ---------------------------------------------------------------------------
-- 2. user_costs.
-- ---------------------------------------------------------------------------
-- This one needs no new index: db/user_costs_schema.sql already declares
-- `constraint user_costs_grain unique (userid, day, surface, feature, model)`, which is
-- exactly the conflict target. The read-then-PATCH was never necessary here; it was written
-- that way only because PostgREST's own upsert replaces rather than adds.
create or replace function bump_user_cost(
    p_userid        text,
    p_day           date,
    p_surface       text,
    p_feature       text,
    p_model         text,
    p_calls         integer,
    p_input_tokens  bigint,
    p_output_tokens bigint,
    p_searches      integer,
    p_cost          numeric,
    p_at            timestamptz
)
returns bigint
language plpgsql
as $$
declare
    row_id bigint;
begin
    insert into user_costs (userid, day, surface, feature, model, calls, input_tokens,
                            output_tokens, web_searches, cost_usd, first_at, last_at)
    values (p_userid, p_day, p_surface, p_feature, coalesce(p_model, ''),
            coalesce(p_calls, 0), coalesce(p_input_tokens, 0), coalesce(p_output_tokens, 0),
            coalesce(p_searches, 0), coalesce(p_cost, 0), p_at, p_at)
    on conflict on constraint user_costs_grain
    do update set
        calls         = user_costs.calls + coalesce(excluded.calls, 0),
        input_tokens  = user_costs.input_tokens + coalesce(excluded.input_tokens, 0),
        output_tokens = user_costs.output_tokens + coalesce(excluded.output_tokens, 0),
        web_searches  = user_costs.web_searches + coalesce(excluded.web_searches, 0),
        cost_usd      = user_costs.cost_usd + coalesce(excluded.cost_usd, 0),
        -- first_at is NOT touched on conflict: it means "first call this user made against
        -- this grain today", and overwriting it with a later timestamp would silently turn it
        -- into a second last_at.
        last_at       = greatest(user_costs.last_at, excluded.last_at)
    returning id into row_id;

    return row_id;
end;
$$;

-- PostgREST exposes these at POST /rest/v1/rpc/bump_interactive_run and
-- /rest/v1/rpc/bump_user_cost. app/core.py calls them and falls back to the pre-Phase-4
-- read-then-PATCH when they are absent, so a checkout where this has not been run still
-- attributes cost - with the race, which is what ships today.
--
-- NOT `security definer`, for the same reason as db/user_data_rpc.sql: the only caller holds
-- the service key and already bypasses RLS, and marking these definer would let any anon-key
-- caller inflate an account's recorded spend by calling the RPC directly.

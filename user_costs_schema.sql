-- user_costs — per-user attribution of the app's interactive AI spend.
--
-- WHAT THIS IS NOT: additional spend. Every dollar in this table is already counted in
-- agent_runs' `interactive_gemini` / `interactive_claude` daily rollups (and, for
-- deadline checks, in deadline_check_log). This table is a BREAKDOWN of that same money
-- by who triggered it, not a second ledger. The admin console reads it that way and
-- never adds it to the interactive total — doing so would double-count.
--
-- The gap between sum(user_costs.cost_usd) and the interactive rollup total is real and
-- meaningful: it is spend from calls that arrived with no userid (signed-out visitors,
-- pre-login flows). The console surfaces it as "unattributed" rather than hiding it.
--
-- Grain: one row per (userid, UTC day, surface, feature) — a rollup, not a call log,
-- for the same reason record_interactive_cost() rolls up: a row per call would grow
-- without bound for data that is only ever read in aggregate.
--
-- Run this once in the Supabase SQL editor. Until it exists, server.py logs a single
-- warning and the console's per-user card shows this SQL instead of numbers; nothing
-- else breaks.

create table if not exists user_costs (
    id              bigint generated always as identity primary key,
    userid          text        not null,
    day             date        not null,
    surface         text        not null,   -- 'gemini' | 'claude' | 'deadline_check'
    feature         text        not null,   -- see FEATURE_LABELS in server.py
    calls           integer     not null default 0,
    input_tokens    bigint      not null default 0,
    output_tokens   bigint      not null default 0,
    web_searches    integer     not null default 0,
    cost_usd        numeric(14,6) not null default 0,
    first_at        timestamptz,
    last_at         timestamptz,
    model           text        not null default '',  -- provider model id; '' = pre-breakout row
    constraint user_costs_grain unique (userid, day, surface, feature, model)
);

create index if not exists user_costs_day_idx    on user_costs (day desc);
create index if not exists user_costs_userid_idx on user_costs (userid);

-- Same posture as the `users` table: RLS on with NO policies, so the anon key gets zero
-- access and only server.py's service-role calls can read or write it. Per-user spend is
-- operator data and must never be reachable from the browser app.
alter table user_costs enable row level security;


-- ---------------------------------------------------------------------------
-- Provider / model breakout (added after the initial table).
--
-- `surface` already says which of the app's three call sites spent the money, but it
-- cannot answer "how much of this user's cost is Anthropic vs Google, and on which
-- model?" — the question that matters when a model is repriced or a feature is moved
-- from one provider to the other. Both interactive surfaces pin a model constant in
-- server.py (MESSAGES_MODEL / CLAUDE_MODEL) and deadline checks pin their own, so the
-- exact model id is known at write time; only the storage was missing.
--
-- Provider is NOT a column: it is derived from the model id by provider_for_model() in
-- server.py. Storing it too would let the two disagree after a bad write, and the
-- mapping is a pure function of the id.
--
-- NOT NULL DEFAULT '' rather than nullable, because the grain constraint below includes
-- this column and Postgres treats NULLs as distinct — a nullable column would let the
-- same (user, day, surface, feature) accumulate into unlimited duplicate rows instead
-- of one. '' means "written before this column existed"; the console labels it unknown.
alter table user_costs add column if not exists model text not null default '';

-- Widen the grain to include the model. Same user, same day, same feature can legitimately
-- hit two models (a feature moved mid-day, or a fallback), and those must not be summed
-- into a row whose model label would then be a lie about half the money.
alter table user_costs drop constraint if exists user_costs_grain;
alter table user_costs add  constraint user_costs_grain
    unique (userid, day, surface, feature, model);

create index if not exists user_costs_model_idx on user_costs (model);

-- discovered_leads — the hub/name-harvest work queue, moved off one laptop.
--
-- WHY THIS FILE EXISTS. PRODUCTION_READINESS_PLAN.md Phase 4, agents_report finding 4.20 and
-- operational risk 8: "Local-only state makes a second machine a different pipeline. In
-- particular discovered_leads.jsonl is the ONLY queue for hub mining and name harvesting;
-- there is no table, no backup, and mark_processed truncates-and-rewrites it."
--
-- The file is 208 KB of real, paid-for work. Every lead in it was found by a search run that
-- consulted the page and threw it away — the whole point of wingman/discovered_leads.py is that
-- those pages were already paid for once. Losing the file means paying again to rediscover
-- them, and there is no copy: it is gitignored (correctly — it is state, not source), it is
-- rewritten in place rather than appended when a lead is marked processed, and the rewrite is
-- not atomic, so an interrupted mark_processed can truncate the queue outright.
--
-- The module's own docstring said the file was "deliberately not a table... a migration needs
-- the operator to run DDL by hand, and this has to earn that first; the plan's
-- discovered_leads table stays the mature form." It has earned it.
--
-- THE FILE IS NOT DELETED. wingman/discovered_leads.py falls back to it whenever this table is
-- absent, which is the state of every checkout until this is run, and every function still
-- takes an explicit `path=` for callers that genuinely mean one file (the tests).
--
-- Run this once in the Supabase SQL editor. Safe to run more than once.

create table if not exists discovered_leads (
    -- An identity primary key rather than keying on url_key, so `order=id` — the default in
    -- wingman/supabase_common.supabase_get since Phase 3 — is both valid AND means the right
    -- thing here: insertion order, which is the "oldest first" ordering the JSONL file gave
    -- for free by being append-only. Keying on url_key instead would have made every read of
    -- this table 400 unless the caller remembered order_by=, which is exactly how agent_locks
    -- shipped broken.
    id           bigint generated always as identity primary key,
    -- url_dedupe.match_key(url). THE dedupe key, and the same one both sides of every
    -- comparison use (Phase 3 item 4.5 is the finding about two normalisers disagreeing).
    url_key      text not null unique,
    url          text not null,
    kind         text not null,               -- 'hub' | 'names'; see KIND_* in the module
    -- STATUS_NEW / STATUS_DONE in wingman/discovered_leads.py. STATUS_DONE is the string
    -- 'processed', not 'done' — the file format has always spelled it that way and the two
    -- backends must agree, since a queue half-read from each would re-pay for the overlap.
    status       text not null default 'new',
    -- The lead as the capture wrote it: source, angle, seed_id, scope, title, found_at, and
    -- whatever a later capture adds. Kept whole rather than split into columns so a new field
    -- needs no migration — the consumers read a dict either way.
    lead         jsonb not null default '{}'::jsonb,
    created_at   timestamptz not null default now(),
    processed_at date
);

-- The two reads that matter: "what is queued of this kind" and "have I seen this URL".
create index if not exists discovered_leads_kind_status_idx
    on discovered_leads (kind, status, id);

-- Same posture as every other table here: RLS on, no policies. The only reader/writer is an
-- offline agent or the localhost console, both holding the service key.
alter table discovered_leads enable row level security;


-- ---------------------------------------------------------------------------
-- mark_leads_processed — stamp a batch of leads done in one statement.
-- ---------------------------------------------------------------------------
-- An RPC rather than a PATCH with `url_key=in.(...)`, for one specific reason: a url_key can
-- contain a comma or a parenthesis, and PostgREST's `in.()` list is comma-delimited with its
-- own quoting rules. Building that filter by string concatenation from URLs is the shape of
-- bug this repo already has a rule about (see the _PROMO_CODE_RE comment in app/core.py). An
-- array parameter is passed as JSON and needs no quoting at all.
--
-- Returns how many rows it actually changed, which is what the consumers print.
create or replace function mark_leads_processed(keys text[])
returns integer
language plpgsql
as $$
declare
    touched integer;
begin
    update discovered_leads
       set status = 'processed',
           processed_at = current_date
     where url_key = any(keys)
       and status <> 'processed';
    get diagnostics touched = row_count;
    return touched;
end;
$$;

-- NOT `security definer`, for the same reason as db/user_data_rpc.sql: the only caller holds
-- the service key and already bypasses RLS.

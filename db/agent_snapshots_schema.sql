-- agent_snapshots — the dry-run snapshots, shared instead of stranded on one laptop.
--
-- WHY THIS FILE EXISTS. PRODUCTION_READINESS_PLAN.md Phase 4, agents_report finding 4.20:
-- "State that exists only on the operator's machine (so a second checkout or a Render box sees
-- a different pipeline): ... the 10 snapshot families (commit path) ..."
--
-- A `--dry-run` SKIPS the database writes but STILL CALLS THE PAID API AT FULL COST. That is
-- the whole reason wingman/dryrun_common.py exists: it replays a snapshot instead of paying to
-- run the agent a second time. So a snapshot is not a log — it is money already spent, sitting
-- in a gitignored file, on one machine, with no copy. A laptop that dies, or simply a second
-- checkout, cannot commit any of it.
--
-- WHAT THIS DOES AND DOES NOT CHANGE. The FILE is still the format and still the thing the
-- agents write; nothing in any agent changed. This table is a MIRROR: a local snapshot is
-- published to it, and a snapshot published from another machine can be materialised back to a
-- local file and committed exactly as if it had been produced here. Everything downstream of
-- dryrun_common.resolve() - the URL dedupe key, the run-time stamp read off the FILENAME
-- rather than `now`, the STALE_DAYS refusal, the two families registered as not-committable -
-- is untouched, because a materialised snapshot is byte-identical to the original file.
--
-- That mattering is Phase 3 item 4.5's whole point: a commit must write what the live run
-- would have written. A mirror that re-derived anything would break it.
--
-- IF THIS TABLE DOES NOT EXIST, dryrun_common lists and commits local files exactly as it does
-- today, and says so once. Every checkout is in that state until this is run.
--
-- Run this once in the Supabase SQL editor. Safe to run more than once.

create table if not exists agent_snapshots (
    -- Identity primary key so `order=id` - supabase_get's default since Phase 3 - is valid AND
    -- meaningful. `file` is unique instead: it is the real identity of a snapshot (it carries
    -- the agent and the run stamp) and it is what the console addresses one by.
    id          bigint generated always as identity primary key,
    file        text not null unique,        -- e.g. scrape_review_full_20260905-141233.json
    agent       text not null,               -- the SNAPSHOT_SPECS key: 'scraper', 'deadline', ...
    -- When the RUN happened, parsed from the filename stamp - not when the row was inserted.
    -- dryrun_common uses the same value to stamp freshness columns on a commit and to refuse a
    -- snapshot older than STALE_DAYS, and a row that disagreed with its own filename would
    -- make those two answers differ by machine.
    ran_at      timestamptz,
    -- The snapshot file's JSON, whole and unaltered. Both historical shapes are stored as they
    -- are (a bare list, or {"inserted": [...], "rejected": [...]}) - _load() already reads
    -- both, and normalising here would silently discard the `rejected` half, which exists so a
    -- run's discards can be audited.
    payload     jsonb not null,
    -- Which machine published it. Purely so an operator looking at a snapshot they did not
    -- produce can tell where it came from.
    host        text,
    created_at  timestamptz not null default now()
);

create index if not exists agent_snapshots_agent_ran_idx on agent_snapshots (agent, ran_at desc);

-- Same posture as every other table here: RLS on, no policies. The only reader/writer is the
-- localhost console or an offline tool, both holding the service key. A snapshot holds the
-- unpublished review queue - rows deliberately not yet in the catalog - and must never be
-- reachable with the anon key.
alter table agent_snapshots enable row level security;

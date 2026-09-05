-- auth_handoffs — the short-lived, single-use nonces the Google OAuth flows hand between one
-- request and the next, moved out of process memory so a second worker can see them.
--
-- WHY THIS FILE EXISTS. PRODUCTION_READINESS_PLAN.md Phase 4, perf_report finding 11. Four
-- stores in app/services/google_oauth.py were plain module-level dicts:
--
--   _google_session_tokens     minted by /api/auth/google/callback, spent by /session or /finish
--   _google_calendar_handoffs  minted by /calendar/start-token, spent by /calendar/start (S1-3)
--   _google_calendar_states    the OAuth `state` for the calendar grant
--   _google_login_redirects    which app origin the callback should send the token back to
--
-- Every one of them is written by ONE request and read by a LATER, SEPARATE one. With a single
-- uvicorn worker that is fine. With two, the second request lands on the other worker roughly
-- half the time and finds nothing — so a student sees "This sign-in link has expired. Please
-- try signing in with Google again," forever, on a link that is perfectly valid. It is the
-- single hardest blocker on running `--workers > 1`, because it does not degrade: it breaks
-- sign-in outright, intermittently, in a way that looks like a Google problem.
--
-- WHY A TABLE AND NOT A SIGNED TOKEN. The perf report offered either. A stateless HMAC token
-- would carry its own payload and need no storage at all — but it can be REPLAYED until it
-- expires, and single-use is the whole point of these nonces. S1-3 made the calendar handoff
-- single-use precisely so a URL sitting in browser history or leaking through a Referer header
-- is inert on the second click. A table keeps that property; a signed token would quietly trade
-- it away for convenience.
--
-- HOW SINGLE-USE SURVIVES TWO WORKERS. Consumption is one `DELETE ... Prefer: return=
-- representation`. Postgres serialises the two deletes and only one of them gets a row back, so
-- the loser sees an empty result and reports "expired" — there is no read-then-delete window
-- for two workers to race in. That is the same reason agent_locks acquires with an INSERT.
--
-- THE TOKEN IS STORED AS A SHA-256 HASH, never in the clear. These rows are live credentials
-- for the ~5 minutes they exist, and this table is the only place in the schema that would hold
-- one at rest. Hashing costs nothing (they are only ever compared for equality) and means a
-- database dump, a backup, or a stray console query cannot hand anybody a working sign-in nonce.
-- app/services/handoff_store.py does the hashing; the plaintext never leaves the process.
--
-- IF THIS TABLE DOES NOT EXIST, app/services/handoff_store.py falls back to the in-process
-- dicts and warns once, naming this file. That is exactly today's behaviour, which is correct
-- on one worker — so a checkout where this has not been run still signs people in.
--
-- Run this once in the Supabase SQL editor. Safe to run more than once.

create table if not exists auth_handoffs (
    kind        text not null,          -- which flow: 'google_session', 'calendar_handoff', ...
    token_hash  text not null,          -- sha256 of the nonce; the nonce itself is never stored
    payload     jsonb not null default '{}'::jsonb,
    expires_at  timestamptz not null,
    created_at  timestamptz not null default now(),
    primary key (kind, token_hash)
);

-- Keyed on (kind, token_hash), NOT on `id`. Nothing paginates this table — every read is an
-- exact primary-key match — but if a future reader ever reaches it through
-- wingman/supabase_common.supabase_get, that helper defaults to `order=id` and the read will
-- 400. Pass order_by="token_hash" and add the table to _NON_ID_KEYED in
-- tests/unit/test_ordered_pagination.py at the same time.

-- Sweeping expired rows. handoff_store prunes opportunistically (at most once a minute per
-- process), so this index is what keeps that sweep from scanning the table.
create index if not exists auth_handoffs_expires_idx on auth_handoffs (expires_at);

-- Same posture as every other table here: RLS on, no policies. The only reader/writer is the
-- web service holding the service key. An anon-key caller must never be able to enumerate live
-- sign-in nonces — with RLS on and no policy, it cannot see a single row.
alter table auth_handoffs enable row level security;

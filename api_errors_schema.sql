-- api_errors — an APPEND-ONLY log of server-side API failures: one row per 5xx response or
-- unhandled exception the FastAPI service produced. Run this once in the Supabase SQL editor
-- before error capture starts recording.
--
-- WHY THIS EXISTS, and why it lives in Supabase rather than a local ring buffer:
--   The shipped web service runs on Render; the ops/admin console runs only on localhost and
--   never even imports app/main's routers. An in-memory error buffer on the API process would
--   therefore be invisible to the console (different machine) and gone on every deploy. Writing
--   to a SHARED table is the only way the admin dashboard's "API Errors" tab can show what the
--   PRODUCTION service is actually failing on, live. It is the same reasoning as user_events:
--   an unlogged failure is unrecoverable, so capture ships with the recorder, not the reader.
--
-- Grain: ONE ROW PER ERROR (not a rollup). Written from the request path via a buffered
-- background flush (see _api_errors_buffer in app/core.py) — a plain batch INSERT with no
-- read-modify-write, and fail-open: capture must NEVER be the reason a request fails. A process
-- that dies between flushes loses at most one flush interval of errors, which is acceptable for
-- a stream only ever read in aggregate on a dashboard.
create table if not exists api_errors (
    id          bigint generated always as identity primary key,
    -- When the error happened, server-stamped on arrival into the buffer (record_api_error sets
    -- it; the default now() is only a fallback for a hand-written insert).
    ts          timestamptz not null default now(),
    -- The HTTP method and request path (query string stripped — it can carry PII and is not
    -- needed to group by endpoint). Path is the primary grouping key on the dashboard.
    method      text not null default '',
    path        text not null default '',
    -- The response status that went back to the client: 500 for an unhandled exception, or the
    -- 5xx a route returned deliberately (e.g. json_error(502) when Supabase is unreachable).
    status      integer not null default 0,
    -- The exception class name for an unhandled crash ("KeyError", "TimeoutError"), or a coarse
    -- label for a returned 5xx ("server_error"). Kept as free text, not an enum: a new error
    -- shape must be a code change, not a DDL migration this repo cannot run through PostgREST.
    error_type  text not null default '',
    -- The exception message (str(exc)) or route detail, truncated. NULL for a returned 5xx whose
    -- body the middleware did not read.
    message     text,
    -- The formatted traceback for an unhandled exception, truncated to a few KB. NULL for a
    -- returned 5xx (no exception was raised). This is what makes a crash actionable.
    traceback   text
);

-- The two reads the dashboard makes: "recent errors, newest first" (the detail tab) and
-- "errors for this endpoint" / "by status" (the summary rollups group in Python, but the ts
-- index carries the windowed scan they all start from).
create index if not exists api_errors_ts_idx     on api_errors (ts desc);
create index if not exists api_errors_status_idx on api_errors (status);
create index if not exists api_errors_path_idx   on api_errors (path);

-- Same posture as user_events / user_costs: RLS ENABLED with NO policies, so the anon key gets
-- zero access and only the service-role calls (the API's recorder and the localhost console's
-- reader) can touch it. Tracebacks and messages can incidentally carry request context, so this
-- must never be reachable from the browser.
alter table api_errors enable row level security;


-- ---------------------------------------------------------------------------
-- ALTER block — for a table that already exists in an older shape.
--
-- `create table if not exists` above is a NO-OP against an existing table, and PostgREST rejects
-- an entire insert on one unknown key. A single missing column would therefore mean NOTHING is
-- ever recorded and capture would read as "no errors ever" rather than "every write failed". Add
-- a column above and you must add it here too.
alter table api_errors add column if not exists ts         timestamptz not null default now();
alter table api_errors add column if not exists method     text not null default '';
alter table api_errors add column if not exists path       text not null default '';
alter table api_errors add column if not exists status     integer not null default 0;
alter table api_errors add column if not exists error_type text not null default '';
alter table api_errors add column if not exists message    text;
alter table api_errors add column if not exists traceback  text;

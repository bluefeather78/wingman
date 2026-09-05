-- conversations — the profile-chat Q&A log. SECURITY_HARDENING_PLAN.md S1-9, finding
-- M9-in-the-report.
--
-- WHY THIS FILE EXISTS AT ALL. Until now this table's only definition was a COMMENT in
-- app/core.py. Whoever created it pasted that comment into the SQL editor — and that
-- comment has no `enable row level security` line. Every other user-facing table in this
-- repo enables RLS with no policies; this one may well have shipped without it, and it
-- holds the most sensitive free text in the product: a minor describing themselves, in
-- their own words, duplicated outside the RLS-protected `users` row.
--
-- READ THIS BEFORE ASSUMING THE PROBLEM IS FIXED. Running this file does NOT retroactively
-- secure a table that already exists — well, the ALTER block below does turn RLS on, but
-- only once somebody actually runs it. CONFIRM THE LIVE STATE in the Supabase dashboard
-- (Table Editor -> conversations -> RLS enabled). The same is true of two other tables
-- flagged in S1-9: db/deadline_check_log_schema.sql had no RLS line either (added there
-- now), and `agent_runs` still has no schema file at all — check both in the dashboard.
--
-- client_ip is GONE as of S1-9. The column is not written any more (see
-- app/core.log_conversation) and is dropped below. There is no session concept on the AI
-- proxy routes, so it was only ever a weak correlation key — and "which minor, from which
-- address, said what" is not a thing worth keeping for that.
--
-- Run this once in the Supabase SQL editor. Safe to run more than once.

create table if not exists conversations (
    id             bigint generated always as identity primary key,
    created_at     timestamptz not null default now(),
    userid         text,
    mode           text,   -- 'live' or 'mock'
    -- Both columns are reused: system_prompt holds only the bot's question for this turn,
    -- and user_content only the student's answer to it. See extract_qa_pair.
    system_prompt  text,
    user_content   text
);

-- No policies, deliberately. Writes come from the server with the service key; nothing
-- should ever read this from a browser.
alter table conversations enable row level security;

create index if not exists conversations_created_at_idx on conversations (created_at desc);


-- ---------------------------------------------------------------------------
-- ALTER block — for the table as it exists today, created from the code comment.
-- ---------------------------------------------------------------------------
alter table conversations add column if not exists userid        text;
alter table conversations add column if not exists mode          text;
alter table conversations add column if not exists system_prompt text;
alter table conversations add column if not exists user_content  text;

-- The point of this file. If the table was created from the old comment, this is the
-- statement that closes the finding.
alter table conversations enable row level security;

-- S1-9: stop storing the address. Dropping the column is what makes "we no longer keep it"
-- true of the rows already written, not merely of the next ones.
alter table conversations drop column if exists client_ip;

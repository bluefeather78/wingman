-- db/survey_responses_schema.sql — one-time manual DDL for the initial-impressions survey.
--
-- Paste this whole file into the Supabase SQL editor and run it. PostgREST (which is all
-- app/ can reach) exposes REST reads/writes only, no DDL, so nothing in this repo can run
-- it for you. Same one-time-manual-step pattern as db/mailing_list_schema.sql and
-- db/user_submissions_schema.sql.
--
-- Until this runs: POST /api/survey answers "setup" (see app/services/survey.py) and
-- public/survey.html shows its own error state instead of a thank-you.
--
-- Safe to run more than once — every statement is IF NOT EXISTS.

create table if not exists survey_responses (
    id              bigint generated always as identity primary key,

    -- Present only when the link was opened while signed in and the frontend could read
    -- the stored access token; the survey itself needs no login, so most rows will have
    -- this null. Never a foreign key — a response must still be recorded if the account is
    -- deleted later, and this is feedback, not account data.
    userid          text,

    -- 1-5. First impression is the one required question; the rest are optional so a
    -- respondent who only wants to leave a star rating still counts.
    first_impression smallint not null check (first_impression between 1 and 5),

    -- 0-10, standard NPS phrasing ("how likely are you to recommend Wingman to a friend").
    recommend_score  smallint check (recommend_score between 0 and 10),

    most_useful     text,   -- free text, "what's been most useful so far"
    missing         text,   -- free text, "what's missing or confusing"

    submitted_at    timestamptz not null default now()
);

create index if not exists idx_survey_responses_submitted_at
    on survey_responses(submitted_at);

-- Repair block, for a table created before a column existed here.
alter table survey_responses add column if not exists userid           text;
alter table survey_responses add column if not exists first_impression smallint;
alter table survey_responses add column if not exists recommend_score  smallint;
alter table survey_responses add column if not exists most_useful      text;
alter table survey_responses add column if not exists missing          text;
alter table survey_responses add column if not exists submitted_at     timestamptz not null default now();

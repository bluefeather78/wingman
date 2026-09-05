-- db/mailing_list_schema.sql — one-time manual DDL for the mailing-list signup feature.
--
-- Paste this whole file into the Supabase SQL editor and run it. PostgREST (which is all
-- server.py can reach) exposes REST reads/writes only, no DDL, so nothing in this repo
-- can run it for you. Same one-time-manual-step pattern as db/subscription_schema.sql,
-- db/user_costs_schema.sql and db/user_submissions_schema.sql.
--
-- Until this runs: agents/find_mailing_lists.py exits with an error naming this file, the admin
-- console's Mailing lists tab shows the setup step instead of a queue, and
-- POST /api/opportunities/<id>/subscribe answers "no automated signup available" for
-- every row — i.e. every opportunity degrades to the "Open signup page" handoff, which
-- is the correct behaviour when we have no verified recipe.
--
-- Safe to run more than once — every statement is IF NOT EXISTS.
--
-- Note the ALTER TABLE block at the bottom. `create table if not exists` does NOTHING
-- against a table that already exists with FEWER columns, so on its own this file cannot
-- repair a partially-created table — and PostgREST rejects an entire insert on one
-- unknown key, so a single missing column means the finder writes no rows at all. The
-- ALTERs make the file genuinely idempotent in both directions: fresh install or repair.


-- ---------- opportunity_signups: one signup RECIPE per opportunity ----------
--
-- A recipe is what agents/find_mailing_lists.py discovered about how to join this program's
-- mailing list, in a form server.py can replay deterministically for any user. One row
-- per opportunity: the primary key is the opportunity id, so a re-check with --force
-- replaces the recipe rather than accumulating duplicates.
--
-- `status` is the gate. Discovery only ever writes 'pending_review' or 'none'; only a
-- person in the admin console writes 'verified', and only 'verified' is ever executed
-- against a real student's email address. This mirrors is_active on the scraper's rows,
-- for the same reason: the automated finder does return plausible-looking answers that
-- are wrong, and here the cost of a wrong one is a student's inbox.
create table if not exists opportunity_signups (
    opportunity_id  text primary key references opportunities(id) on delete cascade,

    -- mailchimp | substack | convertkit | mailerlite | none
    -- 'none' is a real, useful answer: it records that we looked and there is nothing
    -- automatable here, so the next pass does not re-fetch the same pages.
    method          text not null default 'none',
    endpoint        text,
    params          jsonb  not null default '{}'::jsonb,   -- e.g. {"u": "...", "id": "..."}
    field_map       jsonb  not null default '{}'::jsonb,   -- {"EMAIL": "$email", ...}
    double_optin    boolean,

    -- Provenance a reviewer needs to judge the recipe without re-doing the research.
    source_url      text,          -- the page the form was actually found on
    form_text       text,          -- the form's visible text, tags stripped
    pages_checked   jsonb not null default '[]'::jsonb,

    -- scope_evidence is the quote that decided attribution — "this form is captioned
    -- 'Join the E-Japan mailing list'" — and is the single most important field on the
    -- row. 73% of the catalog shares a domain with a different opportunity, so "a
    -- newsletter form exists on this page" proves nothing on its own.
    scope_evidence  text,
    reason          text,
    confidence      real,

    status          text not null default 'pending_review',
    reviewed_by     text,
    reviewed_at     timestamptz,
    discovered_at   timestamptz default now(),
    updated_at      timestamptz default now()
);

create index if not exists idx_opportunity_signups_status
    on opportunity_signups(status);


-- ---------- mailing_list_subscriptions: one row per user per opportunity ----------
--
-- What we actually attempted on someone's behalf, and what the provider said back.
--
-- `state` is deliberately never 'subscribed'. Every provider we support uses double
-- opt-in, and because we subscribe the student's own address (not a relay we can read),
-- nothing here can observe the confirmation email being clicked. 'submitted' means the
-- provider accepted the address and almost certainly emailed them a confirmation link —
-- that is the strongest claim the data supports, and the UI must not overstate it.
--
--   submitted           provider accepted it; confirmation is now up to the student
--   already_subscribed  provider says this address is already on the list
--   failed              provider rejected it, or did not answer intelligibly
--   handoff             no verified recipe; the user was sent to the org's own page
create table if not exists mailing_list_subscriptions (
    id              bigint generated always as identity primary key,
    userid          text not null,
    opportunity_id  text not null references opportunities(id) on delete cascade,

    -- The address we actually sent. Stored because the user may edit it away from their
    -- account email at the point of signup, and "which address did you sign up with"
    -- is the first question when a student says the mail never arrived.
    email           text not null,

    state           text not null,
    message         text,             -- what the user was told
    provider        text,             -- method at the time of the attempt
    provider_detail text,             -- raw reply, truncated; for diagnosing a bad recipe
    attempted_at    timestamptz not null default now(),

    -- One attempt per user per opportunity. A repeat tap updates this row rather than
    -- adding another: the honest question is "did this student sign up", not "how many
    -- times did they press the button", and it keeps a stuck button from spamming an org.
    unique (userid, opportunity_id)
);

create index if not exists idx_mailing_list_subscriptions_user
    on mailing_list_subscriptions(userid);


-- ---------- Repair / forward-migration ----------
--
-- Runs after the CREATEs and is what makes this file idempotent against a table that
-- already exists in an older shape. `create table if not exists` is a no-op there, which
-- would leave columns missing — and one missing column makes PostgREST 400 the whole
-- insert, so the finder would write nothing at all and the review queue would read as
-- "the agent found nothing" rather than "every insert failed".
--
-- Add a column here whenever you add one to a CREATE above.
alter table opportunity_signups add column if not exists method         text not null default 'none';
alter table opportunity_signups add column if not exists endpoint       text;
alter table opportunity_signups add column if not exists params         jsonb not null default '{}'::jsonb;
alter table opportunity_signups add column if not exists field_map      jsonb not null default '{}'::jsonb;
alter table opportunity_signups add column if not exists double_optin   boolean;
alter table opportunity_signups add column if not exists source_url     text;
alter table opportunity_signups add column if not exists form_text      text;
alter table opportunity_signups add column if not exists pages_checked  jsonb not null default '[]'::jsonb;
alter table opportunity_signups add column if not exists scope_evidence text;
alter table opportunity_signups add column if not exists reason         text;
alter table opportunity_signups add column if not exists confidence     real;
alter table opportunity_signups add column if not exists status         text not null default 'pending_review';
alter table opportunity_signups add column if not exists reviewed_by    text;
alter table opportunity_signups add column if not exists reviewed_at    timestamptz;
alter table opportunity_signups add column if not exists discovered_at  timestamptz default now();
alter table opportunity_signups add column if not exists updated_at     timestamptz default now();

alter table mailing_list_subscriptions add column if not exists email           text;
alter table mailing_list_subscriptions add column if not exists state           text;
alter table mailing_list_subscriptions add column if not exists message         text;
alter table mailing_list_subscriptions add column if not exists provider        text;
alter table mailing_list_subscriptions add column if not exists provider_detail text;
alter table mailing_list_subscriptions add column if not exists attempted_at    timestamptz not null default now();

-- One attempt per user per opportunity — see the note on the CREATE above. Added
-- separately so a table created before this constraint existed still gets it.
do $$
begin
  alter table mailing_list_subscriptions
    add constraint mailing_list_subscriptions_user_opp_key unique (userid, opportunity_id);
exception
  when duplicate_table then null;   -- constraint already present
  when duplicate_object then null;
end $$;

-- email_schema.sql — one-time manual DDL for the lifecycle-email feature.
--
-- Paste this whole file into the Supabase SQL editor and run it. PostgREST (which is all
-- app/ can reach) exposes REST reads/writes only, no DDL, so nothing in this repo can run
-- it for you. Same one-time-manual-step pattern as subscription_schema.sql,
-- mailing_list_schema.sql and user_costs_schema.sql.
--
-- Until this runs, NO lifecycle email is ever sent. app/services/email.py claims a send
-- row BEFORE it calls Resend (see below), so a missing table means the claim fails, and a
-- failed claim means "do not send" — deliberately, because the alternative is sending
-- with no record of having sent, which is how a daily sweep mails the same student every
-- morning. The console's Emails tab shows the setup step instead of a log, and the trial
-- sweep reports `table_ready: false` rather than a zero that would read as "nobody due".
--
-- Safe to run more than once — every statement is IF NOT EXISTS.
--
-- Note the ALTER TABLE block at the bottom. `create table if not exists` does NOTHING
-- against a table that already exists with FEWER columns, and PostgREST rejects an entire
-- insert on one unknown key — so a single missing column means every claim fails and the
-- feature reads as "switched off" rather than "half-migrated". Add a column to the CREATE
-- and you MUST add it to the ALTER block too.


-- ---------- email_sends: one row per email we have committed to sending ----------
--
-- This is a CLAIM table, not a log. The row is written BEFORE the provider is called and
-- the unique constraint below is what makes a repeated sweep safe: the second attempt
-- loses the insert race, sees the conflict, and skips. A log written after the send
-- cannot do that — the window between "Resend accepted it" and "we recorded it" is
-- exactly where a crash produces a duplicate in a real student's inbox.
--
-- Consequence worth stating plainly: a send that crashes mid-flight leaves a row stuck at
-- state 'sending' and that email is never retried automatically. That is the intended
-- trade — a stuck row is visible in the console and can be cleared by hand, where a
-- duplicate cannot be un-sent.
create table if not exists email_sends (
    id          bigserial primary key,
    userid      text not null,

    -- welcome | trial_ending | goodbye. Kept as free text rather than an enum so adding a
    -- fourth lifecycle email needs no migration; app/services/email.py owns the list.
    kind        text not null,

    -- What makes this send unique WITHIN its kind, and the reason the constraint below is
    -- three columns rather than two. A trial can be extended (a `grant` promo code adds
    -- days to trial_ends_at), so keying trial_ending on (userid, kind) alone would mean a
    -- student who redeems BETAUSER and gets a second trial window never hears from us
    -- again. The key is the trial's end DATE, so a new window is a new send and the same
    -- window can only ever fire once. 'welcome' and 'goodbye' are once-per-account and
    -- use '' — not NULL, because Postgres treats NULLs as distinct in a unique
    -- constraint, which would silently make every insert a fresh row and defeat the
    -- whole table. Same trap user_costs.model documents.
    dedupe_key  text not null default '',

    -- The address actually used. Prefilled from the account, but recorded per send: "which
    -- address did this go to" is the first question when a student says nothing arrived,
    -- and the account's email may have changed since. Same reasoning as
    -- mailing_list_subscriptions.email.
    email       text,
    subject     text,

    -- sending -> sent | failed. 'sending' is the claim; anything still in it after a few
    -- minutes is a crashed send, not one in flight.
    state       text not null default 'sending',

    provider            text not null default 'resend',
    provider_message_id text,        -- Resend's id, so a bounce in their dashboard maps back
    error               text,        -- provider error text on state='failed'

    claimed_at  timestamptz not null default now(),
    sent_at     timestamptz,

    -- One send per (account, kind, window). THE load-bearing line in this file.
    unique (userid, kind, dedupe_key)
);

create index if not exists idx_email_sends_userid ON email_sends(userid);
create index if not exists idx_email_sends_kind_claimed ON email_sends(kind, claimed_at desc);


-- ---------- users: the lifecycle-email opt-out ----------
--
-- One boolean, honoured for ALL THREE lifecycle emails rather than for marketing only.
-- Welcome and trial-ending are defensibly transactional and could legally ignore an
-- opt-out; this deliberately does not. Most of this user base are minors, an unsubscribe
-- link that quietly keeps sending is the exact silent failure the mailing-list feature is
-- measured against, and there is no volume here that makes the distinction worth the
-- trust cost. The link carries an HMAC of the userid (JWT_SECRET), so opting somebody
-- else out by guessing their id is not possible.
alter table users add column if not exists lifecycle_email_optout boolean default false;


-- ---------- Repair block (see the header) ----------
alter table email_sends add column if not exists userid              text;
alter table email_sends add column if not exists kind                text;
alter table email_sends add column if not exists dedupe_key          text not null default '';
alter table email_sends add column if not exists email               text;
alter table email_sends add column if not exists subject             text;
alter table email_sends add column if not exists state               text not null default 'sending';
alter table email_sends add column if not exists provider            text not null default 'resend';
alter table email_sends add column if not exists provider_message_id text;
alter table email_sends add column if not exists error               text;
alter table email_sends add column if not exists claimed_at          timestamptz not null default now();
alter table email_sends add column if not exists sent_at             timestamptz;

-- Added separately so a table created before this constraint existed still gets it. Without
-- it the table accepts duplicates and the feature loses its only protection against
-- re-sending, while still LOOKING like it is recording sends.
do $$
begin
  alter table email_sends
    add constraint email_sends_user_kind_key unique (userid, kind, dedupe_key);
exception
  when duplicate_table then null;   -- constraint already present
  when duplicate_object then null;
end $$;

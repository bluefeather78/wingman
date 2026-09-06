-- account_deletions — a PII-FREE tombstone, one row per self-serve account deletion.
--
-- WHY THIS EXISTS: when a student hard-deletes their account (DATA_DELETION_EXPORT_PLAN.md,
-- P2) the `users` row and every satellite row are gone. This leaves a minimal, non-personal
-- record so we can answer "did you delete my account on date X" and audit that deletions are
-- actually happening — WITHOUT retaining anything that identifies who the account belonged to.
--
-- The userid is stored ONLY as a one-way sha256 hash (record_account_deletion in
-- app/core.py). No email, no name, no raw userid — the hash cannot be reversed to reconstruct
-- who had an account, so this table is not itself a roster of past minors.
--
-- Run this once in the Supabase SQL editor. Until it exists, record_account_deletion() is a
-- silent no-op (a missing tombstone table is NEVER a reason to fail a delete that already
-- happened); deletion works, it just leaves no audit row.

create table if not exists account_deletions (
    -- sha256(lower(userid)) — proof a specific account was deleted, not who it was.
    userid_hash      text primary key,
    deleted_at       timestamptz not null default now(),
    -- Whether the account carried a Stripe subscription at deletion time. Useful for churn
    -- accounting; carries no personal data.
    had_subscription boolean not null default false,
    -- Free text, e.g. "self-serve account deletion". Never put PII here.
    notes            text
);

create index if not exists account_deletions_deleted_at_idx
    on account_deletions (deleted_at desc);

-- Same posture as every user-keyed table here: RLS enabled with NO policies, so the anon key
-- gets zero access and only the service-role calls in app/ can read or write it.
alter table account_deletions enable row level security;


-- ---------------------------------------------------------------------------
-- ALTER block — for a table that already exists in an older shape.
--
-- `create table if not exists` above is a NO-OP against an existing table, and PostgREST
-- rejects an entire insert on one unknown key. Add a column above and you must add it here.
alter table account_deletions add column if not exists deleted_at       timestamptz not null default now();
alter table account_deletions add column if not exists had_subscription boolean not null default false;
alter table account_deletions add column if not exists notes            text;

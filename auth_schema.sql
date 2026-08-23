-- Session auth (Phase 2 — PLAN_2_auth.md): the revocation counter.
--
-- Run this ONCE in the Supabase SQL editor. It adds a single column to the `users` table.
-- Everything about auth works before it runs — the code reads a missing value as 0 — EXCEPT
-- session revocation ("log out everywhere" / account-kill), which is a no-op until the
-- column exists. So this is a "turn revocation on" step, not a "make login work" step.
--
-- token_version is stamped into every access+refresh token at login. /api/auth/refresh
-- compares a refresh token's version against this column and refuses to renew on a
-- mismatch; POST /api/auth/logout-all increments it. Bumping it therefore invalidates every
-- outstanding session for the account within one access-token lifetime.
--
-- NOT NULL DEFAULT 0 is deliberate: existing rows backfill to 0 (matching what the code
-- already assumes for them), and new rows created by create_user() — which does NOT write
-- this column, on purpose, so registration keeps working before this migration runs — get 0
-- from the default. `add column if not exists` makes re-running harmless.

alter table users add column if not exists token_version integer not null default 0;

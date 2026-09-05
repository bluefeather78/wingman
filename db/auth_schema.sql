-- Session auth (Phase 2 — docs/archive/PLAN_2_auth.md): the revocation counter.
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


-- ---------------------------------------------------------------------------
-- Refresh-token rotation with reuse detection (S1-2, finding M2).
--
-- BEFORE THIS: one 30-day refresh token, never rotated. /api/auth/refresh checked `ver`,
-- returned a NEW pair, and left the presented token valid until its own exp. The client's
-- logout() only forgot the token locally — there was no server call at all. So a refresh
-- token copied off a shared school computer, out of a proxy log, or from a compromised
-- device kept minting access tokens FOR 30 DAYS, INCLUDING AFTER THE STUDENT PRESSED
-- "LOG OUT", and nothing anywhere signalled that two parties were refreshing the same
-- lineage.
--
-- AFTER: every refresh token carries a random `jti`, and this column holds the jti that is
-- currently valid for each live device. Refreshing replaces that device's jti with a new
-- one, so the presented token dies the moment it is used. Presenting a SUPERSEDED jti is
-- evidence that two parties hold the same lineage — the thief and the student — and there
-- is no way to tell which one is asking, so the response is to bump token_version and end
-- every session on the account. One forced sign-in beats a live intruder.
--
-- AN ARRAY, not a single value, so a student on a laptop and a phone holds two independent
-- lineages instead of the two devices logging each other out on every refresh. Capped in
-- code (REFRESH_JTI_MAX) — oldest evicted, so device number six signs the first one out
-- rather than the list growing forever.
--
-- DEGRADES: until this runs, the column simply is not there, rotation does not engage, and
-- auth behaves exactly as it did before — see app.core.rotate_refresh_jti, which reports
-- False on a missing column, and the refresh route, which then skips the check. Tokens
-- minted before it runs carry no jti and are honoured once, then rotated in; without that,
-- deploying this would sign out every logged-in student at once.
-- ---------------------------------------------------------------------------
alter table users add column if not exists refresh_jtis text[] not null default '{}';

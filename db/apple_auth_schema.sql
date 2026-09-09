-- db/apple_auth_schema.sql — one-time manual DDL for the Supabase `users` table.
--
-- Paste this whole file into the Supabase SQL editor and run it. PostgREST (which is
-- all the web service can reach) exposes REST reads/writes only, no DDL, so there is no
-- way to run this from the app. Same one-time-manual-step pattern as db/google_auth_schema.sql.
--
-- Until this runs, Sign in with Apple returns a 503 naming this file: create_user() writes
-- apple_id on an Apple signup and Postgres rejects the insert (42703 / PGRST204) if the
-- column doesn't exist. An Apple-only account has no password, so it relies on the same
-- password_hash NULL allowance db/google_auth_schema.sql already added (re-asserted below so
-- this file stands on its own if Apple is enabled before Google's schema was run).
--
-- Safe to run more than once.

ALTER TABLE users ADD COLUMN IF NOT EXISTS apple_id text;

-- Apple-only accounts (like Google-only ones) have no password to hash.
ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL;

-- Partial unique index: only enforces uniqueness where apple_id is actually set, so every
-- existing row (apple_id IS NULL) doesn't collide with the others.
CREATE UNIQUE INDEX IF NOT EXISTS users_apple_id_key ON users(apple_id) WHERE apple_id IS NOT NULL;

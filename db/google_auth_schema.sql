-- db/google_auth_schema.sql — one-time manual DDL for the Supabase `users` table.
--
-- Paste this whole file into the Supabase SQL editor and run it. PostgREST (which is
-- all server.py can reach) exposes REST reads/writes only, no DDL, so there is no way
-- to run this from the app. Same one-time-manual-step pattern as db/subscription_schema.sql.
--
-- Until this runs, Google sign-in returns a 503 naming this file: create_user() writes
-- google_id on a Google signup and Postgres rejects the insert (42703 / PGRST204) if the
-- column doesn't exist, and a Google-only account (no password) fails the NOT NULL
-- constraint on password_hash until that constraint is dropped.
--
-- Safe to run more than once.

ALTER TABLE users ADD COLUMN IF NOT EXISTS google_id text;

-- Password accounts still require password_hash (enforced in handle_register), but a
-- Google-only account has no password to hash — the column must allow NULL for those rows.
ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL;

-- Partial unique index: only enforces uniqueness where google_id is actually set, so
-- every existing password-only row (google_id IS NULL) doesn't collide with the others.
CREATE UNIQUE INDEX IF NOT EXISTS users_google_id_key ON users(google_id) WHERE google_id IS NOT NULL;

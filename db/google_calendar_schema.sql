-- db/google_calendar_schema.sql — one-time manual DDL for the Supabase `users` table.
--
-- Paste this whole file into the Supabase SQL editor and run it. Same one-time-manual-step
-- pattern as db/google_auth_schema.sql and db/subscription_schema.sql — PostgREST exposes no DDL,
-- so server.py cannot run this itself.
--
-- Until this runs, connecting Google Calendar returns a 503 naming this file: the OAuth
-- callback tries to PATCH these columns onto the user's row and Postgres rejects it
-- (42703 / PGRST204) if they don't exist.
--
-- Safe to run more than once.

ALTER TABLE users ADD COLUMN IF NOT EXISTS google_calendar_access_token text;
ALTER TABLE users ADD COLUMN IF NOT EXISTS google_calendar_refresh_token text;
ALTER TABLE users ADD COLUMN IF NOT EXISTS google_calendar_token_expires_at timestamptz;
ALTER TABLE users ADD COLUMN IF NOT EXISTS google_calendar_connected_at timestamptz;
ALTER TABLE users ADD COLUMN IF NOT EXISTS google_calendar_id text;

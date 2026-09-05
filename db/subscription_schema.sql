-- db/subscription_schema.sql — one-time manual DDL for the Supabase `users` table.
--
-- Paste this whole file into the Supabase SQL editor and run it. PostgREST (which is
-- all server.py can reach) exposes REST reads/writes only, no DDL, so there is no way
-- to run this from the app. This is the same one-time-manual-step pattern as
-- db/user_costs_schema.sql and db/scraper_seeds_schema.sql.
--
-- Until this runs, /api/register returns a 503 naming this file: create_user() writes
-- every column below on signup, and Postgres rejects the whole insert with 42703
-- ("column users.<name> does not exist") if any one of them is missing.
--
-- Safe to run more than once — every statement is IF NOT EXISTS.

-- ---------- Subscription / billing ----------
ALTER TABLE users ADD COLUMN IF NOT EXISTS subscription_status text DEFAULT 'trial';
ALTER TABLE users ADD COLUMN IF NOT EXISTS trial_ends_at timestamptz;
ALTER TABLE users ADD COLUMN IF NOT EXISTS subscription_end_at timestamptz;
ALTER TABLE users ADD COLUMN IF NOT EXISTS stripe_customer_id text;
ALTER TABLE users ADD COLUMN IF NOT EXISTS stripe_subscription_id text;
ALTER TABLE users ADD COLUMN IF NOT EXISTS promo_codes_used text[] DEFAULT '{}';

-- ---------- Signup consent (Terms of Use / Privacy Policy / age) ----------
-- is_adult          — true if the user checked "I am 18 or older".
-- parental_consent  — true if an under-18 user confirmed a parent/guardian gave
--                     permission and agrees to the Terms on their behalf.
--                     Terms §2 requires this; registration is refused without it.
-- terms_version     — which version of the documents they accepted, so a future
--                     material change can be detected and re-consent prompted.
--                     Matches TERMS_VERSION in server.py.
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_adult boolean;
ALTER TABLE users ADD COLUMN IF NOT EXISTS parental_consent boolean DEFAULT false;
ALTER TABLE users ADD COLUMN IF NOT EXISTS terms_accepted_at timestamptz;
ALTER TABLE users ADD COLUMN IF NOT EXISTS privacy_accepted_at timestamptz;
ALTER TABLE users ADD COLUMN IF NOT EXISTS terms_version text;

-- Existing rows predate consent capture: they get NULLs, which read as
-- "never accepted". They keep working (the gate only runs at registration), but
-- terms_accepted_at IS NULL is how you find accounts that never saw the documents.
CREATE INDEX IF NOT EXISTS idx_users_subscription_status ON users(subscription_status);

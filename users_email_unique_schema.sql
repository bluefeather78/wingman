-- users_email_unique_schema.sql — one-time manual DDL for the Supabase `users` table.
--
-- Makes email uniqueness a database guarantee. server.py already refuses a duplicate
-- email at registration (handle_register), but that check and the INSERT are two separate
-- round-trips: two people submitting the same address at the same moment can both pass
-- the check and both insert. Only a unique index closes that window.
--
-- User IDs need nothing here — `userid` is already the primary key, and server.py
-- lowercases it before every read and write, so uniqueness there is already
-- case-insensitive and already enforced.
--
-- Run this in the Supabase SQL editor. STEP 1 FIRST — the index cannot be created while
-- duplicates exist, and CREATE INDEX will simply fail with
-- "could not create unique index ... Key (lower(email)) is duplicated".

-- ---------- STEP 1: find existing duplicates ----------
-- Run this on its own first. If it returns rows, resolve them before step 2: delete the
-- accounts that shouldn't exist, or change their email. Keep the one the person actually
-- uses — check `created_at` and whether the row has real data in it.
SELECT lower(email)               AS email,
       count(*)                   AS accounts,
       array_agg(userid ORDER BY created_at) AS userids,
       array_agg(created_at ORDER BY created_at) AS created
FROM users
GROUP BY lower(email)
HAVING count(*) > 1
ORDER BY count(*) DESC;

-- ---------- STEP 2: enforce it ----------
-- Indexes lower(email) rather than email so the constraint is case-insensitive, matching
-- normalize_email() in server.py. Storing lowercased is what server.py does on write, but
-- indexing the expression means the guarantee holds even for a row written some other way
-- (a manual insert in the Supabase table editor, say).
--
-- The index name is referenced by EMAIL_UNIQUE_INDEX in server.py, which uses it to tell
-- an email collision apart from a userid collision — both surface as a 409 and need
-- different messages. Rename it here and you must rename it there.
CREATE UNIQUE INDEX IF NOT EXISTS users_email_lower_key ON users (lower(email));

-- ---------- STEP 3: verify ----------
-- Should list users_email_lower_key.
SELECT indexname, indexdef FROM pg_indexes
WHERE tablename = 'users' AND indexname = 'users_email_lower_key';

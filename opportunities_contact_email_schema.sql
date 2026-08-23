-- Adds a contact email column to the existing `opportunities` table.
--
-- Not a new table, so this is a bare ALTER (not a CREATE + ALTER pair like the other
-- schema files) — the table already exists in Supabase from migrate_to_supabase.py.
-- Run this once in the Supabase SQL editor.
--
-- Nothing in this repo writes this column automatically: none of the five background
-- agents populate it, so it starts NULL on every existing row and is filled in by hand
-- through the admin console's queue edit modal (see EDITABLE_OPPORTUNITY_FIELDS in
-- server.py) or by editing the row directly. PostgREST 400s an entire insert/update on
-- one unknown key, so until this runs, an edit that tries to set contact_email fails
-- like any other missing-column case in this repo.

alter table opportunities add column if not exists contact_email text;

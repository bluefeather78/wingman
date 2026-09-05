-- Remove verbatim user-conversation storage.
--
-- Wingman used to persist profile-chat <question, answer> turns to a `conversations`
-- table (see the now-deleted db/conversations_schema.sql and the removed
-- log_conversation helpers in app/core.py). That storage was removed deliberately as a
-- privacy improvement: the table held the most sensitive free text in the product — a
-- minor describing themselves in their own words — and keeping it was not worth the
-- exposure. Nothing in the code writes conversation text anywhere any more.
--
-- Run this ONCE in the Supabase SQL editor to drop the live table and every row in it.
-- This is DESTRUCTIVE and irreversible — it deletes all stored conversation rows.
-- Safe to run more than once.

drop table if exists conversations;

-- set_user_data — write keys inside users.data without reading the blob first.
--
-- WHY THIS FILE EXISTS. PRODUCTION_READINESS_PLAN.md Phase 4, perf_report finding 10, tracked
-- as security finding L9 since Phase 1. app/core.update_user_data() was a read-modify-write:
--
--     data = get_user_data(userid)      -- SELECT the whole jsonb blob
--     data[key] = value                 -- in Python
--     PATCH users SET data = data       -- send the whole blob back
--
-- Two problems, and the second is the one that loses a student's work.
--
--   1. COST. Three round trips and two transfers of the entire blob per save. A profile
--      synthesis writes four keys, so twelve round trips and eight blob transfers for four
--      values. The blob is every tracked opportunity, every task, and the whole profile.
--
--   2. LOST UPDATES. Two saves that overlap - two tabs, a phone and a laptop, or the app's own
--      background writes racing a foreground one - both read the same blob, each sets its own
--      key on its own copy, and the second PATCH overwrites the first key with the stale value
--      it read. Nothing errors. The student's change is simply gone, and the client's
--      queueSlotWrite serialiser exists only to make that less likely on ONE device; it cannot
--      help across two.
--
-- The fix is to let Postgres do the merge, which makes the whole thing one atomic statement:
-- concurrent writers serialise on the row and every one of them lands.
--
-- WHY `||` AND NOT jsonb_set. The plan's text says jsonb_set, and for one key they are
-- equivalent - but jsonb_set takes a single path, so a multi-key save would need one call per
-- key and be back to N statements. `data || kv` merges every top-level key in one go, which is
-- what makes the multi-key form worth having. Both replace an existing key and keep the rest,
-- which is exactly what `data[key] = value` did.
--
-- NULL. A JSON null value is stored as a JSON null, unchanged from the old behaviour: `||` with
-- {"k": null} sets k to null rather than removing it. Nothing in the app distinguishes "absent"
-- from "null" (see /api/data/load, where an unknown key answers null), so there is no delete
-- form here on purpose - one would be a new capability, not a port.
--
-- IF THIS FUNCTION DOES NOT EXIST, app/core.update_user_data falls back to the read-modify-write
-- above and warns once, naming this file. That keeps a fresh checkout saving data - with the old
-- race, which is what it has today anyway.
--
-- Run this once in the Supabase SQL editor. Safe to run more than once.

create or replace function set_user_data(uid text, kv jsonb)
returns boolean
language plpgsql
as $$
declare
    touched integer;
begin
    update users
       set data = coalesce(data, '{}'::jsonb) || kv
     where userid = uid;
    get diagnostics touched = row_count;
    return touched > 0;
end;
$$;

-- PostgREST exposes this at POST /rest/v1/rpc/set_user_data with {"uid": "...", "kv": {...}}.
-- It returns true when a row matched and false when no such account exists, which is what
-- app/routes/user_data.py turns into its 404 - the same answer the old read-modify-write gave
-- when get_user_data() came back None.
--
-- NOT `security definer`. The only caller holds the service key and already bypasses RLS;
-- marking it definer would additionally let any anon-key caller rewrite any account's data by
-- calling the RPC directly, since PostgREST exposes every function in the schema.

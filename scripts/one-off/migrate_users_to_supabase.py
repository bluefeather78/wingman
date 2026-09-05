#!/usr/bin/env python3
"""One-off migration: loads wingman's existing users_db.json (flat file, keyed
by lowercased userid) into the Supabase `users` table. Safe to re-run — it
upserts on `userid`, so re-running just refreshes existing rows rather than
duplicating them.

Unlike opportunities.json (public catalog data), users_db.json holds account
credentials (SHA-256 password hashes) and per-user app-data blobs, so this
writes with the service_role key to a table that has RLS enabled with NO
public policies — only the service_role key (used here, and by server.py) can
read or write it.

SETUP:
    Add to .env (repo root), if not already present from the opportunities
    migration:
        SUPABASE_URL=https://<project-ref>.supabase.co
        SUPABASE_SERVICE_KEY=<service_role key, from Project Settings -> API>
    Create the `users` table first by running this SQL in the Supabase SQL
    editor:

        create table users (
            userid         text primary key,
            first_name     text not null,
            last_name      text not null,
            email          text not null,
            password_hash  text not null,
            data           jsonb not null default '{}'::jsonb,
            created_at     timestamptz not null default now(),
            updated_at     timestamptz not null default now()
        );
        alter table users enable row level security;
        -- Deliberately no policies: RLS with zero policies means the anon/
        -- authenticated roles get no access at all. Only the service_role
        -- key (used by server.py and this script) can read/write, since it
        -- bypasses RLS entirely.

USAGE:
    python migrate_users_to_supabase.py            # migrate + upsert
    python migrate_users_to_supabase.py --dry-run  # print what would be sent, no writes
"""
import json
import os
import sys
import urllib.error
import urllib.request

USERS_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "users_db.json")


def load_dotenv(path=".env"):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            if key and not os.environ.get(key):
                os.environ[key] = value


def build_rows():
    if not os.path.exists(USERS_JSON):
        print(f"[!] {USERS_JSON} not found — nothing to migrate.")
        return []
    with open(USERS_JSON, "r", encoding="utf-8") as f:
        users = json.load(f)
    rows = []
    for userid, record in users.items():
        rows.append({
            "userid": userid,
            "first_name": record.get("firstName", ""),
            "last_name": record.get("lastName", ""),
            "email": record.get("email", ""),
            "password_hash": record.get("passwordHash", ""),
            "data": record.get("data") or {},
        })
    return rows


def post_batch(supabase_url, service_key, batch):
    req = urllib.request.Request(
        f"{supabase_url}/rest/v1/users?on_conflict=userid",
        data=json.dumps(batch).encode("utf-8"),
        method="POST",
        headers={
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal,resolution=merge-duplicates",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status


def main():
    dry_run = "--dry-run" in sys.argv
    load_dotenv()
    rows = build_rows()
    print(f"[OK] {len(rows)} user(s) found in users_db.json.")

    if dry_run:
        print(f"[DRY RUN] Would upsert {len(rows)} row(s). Sample (password_hash redacted):")
        if rows:
            sample = dict(rows[0])
            sample["password_hash"] = "<redacted>"
            print(json.dumps(sample, indent=2, ensure_ascii=False))
        return

    if not rows:
        return

    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    service_key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not supabase_url or not service_key:
        print("[ERROR] SUPABASE_URL / SUPABASE_SERVICE_KEY not set in .env.")
        sys.exit(1)

    try:
        status = post_batch(supabase_url, service_key, rows)
        print(f"[OK] Upserted {len(rows)} user(s) (status {status})")
    except urllib.error.HTTPError as e:
        print(f"[ERROR] Migration failed: {e.code} {e.read().decode(errors='replace')}")
        sys.exit(1)

    print(f"\n[DONE] Migrated {len(rows)} user(s) into Supabase.")


if __name__ == "__main__":
    main()

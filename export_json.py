#!/usr/bin/env python3
"""Dumps the current Supabase `opportunities` table back to opportunities.json,
in the same 12-field shape the frontend used to fetch directly. Supabase itself
is the runtime source of truth (see server.py's /api/opportunities); this file
is kept git-tracked purely as a human-diffable snapshot for backup/review — run
manually after editing the DB, before committing.

USAGE:
    python export_json.py
"""
import json
import os

from supabase_common import load_dotenv, supabase_get

OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "opportunities.json")
FIELDS = "id,name,org,summary,url,subject_tags,type,price,state,location,intl,season"


def main():
    load_dotenv()
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    anon_key = os.environ.get("SUPABASE_ANON_KEY", "")
    if not supabase_url or not anon_key:
        raise SystemExit("[ERROR] SUPABASE_URL / SUPABASE_ANON_KEY not set in .env.")

    # Paginated (via supabase_common.supabase_get) — a single unpaginated request
    # silently truncates at PostgREST's 1000-row default cap, which undercounted
    # this snapshot (1207 active rows) until this fix.
    data = supabase_get(supabase_url, "opportunities",
                         {"select": FIELDS, "is_active": "eq.true", "order": "id"}, anon_key)

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"[OK] Wrote {len(data)} opportunities to {OUT_PATH}")


if __name__ == "__main__":
    main()

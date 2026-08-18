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
import urllib.parse
import urllib.request

OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "opportunities.json")
FIELDS = "id,name,org,summary,url,subject,type,price,state,location,intl,season"


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


def main():
    load_dotenv()
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    anon_key = os.environ.get("SUPABASE_ANON_KEY", "")
    if not supabase_url or not anon_key:
        raise SystemExit("[ERROR] SUPABASE_URL / SUPABASE_ANON_KEY not set in .env.")

    query = urllib.parse.urlencode({"select": FIELDS, "is_active": "eq.true", "order": "id"})
    req = urllib.request.Request(
        f"{supabase_url}/rest/v1/opportunities?{query}",
        headers={"apikey": anon_key, "Authorization": f"Bearer {anon_key}"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"[OK] Wrote {len(data)} opportunities to {OUT_PATH}")


if __name__ == "__main__":
    main()

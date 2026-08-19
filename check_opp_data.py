#!/usr/bin/env python3
"""Check what's stored for an opportunity in Supabase."""
import json
import os
import urllib.request
import urllib.parse

# Load env
load_path = ".env"
if os.path.exists(load_path):
    with open(load_path, "r", encoding="utf-8") as f:
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

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

# Fetch opportunity
query = urllib.parse.urlencode({
    "select": "id,name,status,deadlines,opens_date,was_estimated,deadline_note,last_checked_at",
    "id": "eq.ec12081"
})

try:
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/opportunities?{query}",
        headers={
            "apikey": SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        opps = json.loads(resp.read())

    if not opps:
        print("[ERROR] Opportunity not found")
        exit(1)

    opp = opps[0]
    print(f"Opportunity: {opp['name']}")
    print(f"Status: {opp.get('status')}")
    print(f"Deadlines: {opp.get('deadlines')}")
    print(f"Opens Date: {opp.get('opens_date')}")
    print(f"Was Estimated: {opp.get('was_estimated')}")
    print(f"Deadline Note: {opp.get('deadline_note')}")
    print(f"Last Checked: {opp.get('last_checked_at')}")

except Exception as e:
    print(f"[ERROR] {e}")
    exit(1)

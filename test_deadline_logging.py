#!/usr/bin/env python3
"""Quick test to verify deadline check logging is working."""
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

if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
    print("[ERROR] SUPABASE_URL or SUPABASE_SERVICE_KEY not set")
    exit(1)

# Query the deadline_check_log table
query = urllib.parse.urlencode({
    "select": "*",
    "order": "checked_at.desc",
    "limit": "5"
})

try:
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/deadline_check_log?{query}",
        headers={
            "apikey": SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        logs = json.loads(resp.read())

    if not logs:
        print("[INFO] No entries in deadline_check_log table yet")
    else:
        print(f"[OK] Found {len(logs)} recent log entries:")
        for entry in logs:
            print(f"  - Opp {entry['opportunity_id']}: {entry['source']} ({entry['checked_at'][:10]})")
except Exception as e:
    print(f"[ERROR] {e}")
    exit(1)

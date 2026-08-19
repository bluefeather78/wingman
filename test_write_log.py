#!/usr/bin/env python3
"""Test writing to deadline_check_log table."""
import datetime
import json
import os
import urllib.request

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

# Try to write a test entry
log_entry = {
    "opportunity_id": "test-123",
    "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "source": "test",
    "status": "running",
    "web_searches": 1,
    "cost_usd": 0.01,
    "was_estimated": False,
    "notes": "Test entry",
}

try:
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/deadline_check_log",
        data=json.dumps(log_entry).encode(),
        method="POST",
        headers={
            "apikey": SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        result = resp.read()
        print(f"[OK] Successfully wrote test entry to deadline_check_log")
        print(f"Response: {result}")
except Exception as e:
    print(f"[ERROR] Failed to write: {e}")
    exit(1)

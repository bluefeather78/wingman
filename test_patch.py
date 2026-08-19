#!/usr/bin/env python3
"""Test patching an opportunity."""
import datetime
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

opp_id = "ec12081"

patch = {
    "status": "unknown",
    "deadlines": [],
    "opens_date": None,
    "was_estimated": False,
    "deadline_note": "Test patch",
    "last_checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
}

query = urllib.parse.urlencode({"id": f"eq.{opp_id}"})

print(f"[*] Patching opportunity {opp_id}...")
print(f"Patch data: {json.dumps(patch, indent=2)}")

try:
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/opportunities?{query}",
        data=json.dumps(patch).encode(),
        method="PATCH",
        headers={
            "apikey": SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        result = resp.read()
    print(f"[OK] Patch succeeded!")
    print(f"Response: {result}")
except Exception as e:
    print(f"[ERROR] Patch failed: {e}")
    import traceback
    traceback.print_exc()
    exit(1)

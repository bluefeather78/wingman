#!/usr/bin/env python3
"""Test the check_deadline_one function directly."""
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
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
    print("[ERROR] SUPABASE_URL or SUPABASE_SERVICE_KEY not set")
    exit(1)

if not ANTHROPIC_API_KEY:
    print("[ERROR] ANTHROPIC_API_KEY not set")
    exit(1)

# Fetch opportunity
query = urllib.parse.urlencode({
    "select": "id,name,org,url,summary,status,deadlines,opens_date,was_estimated,deadline_note,last_checked_at",
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
    print(f"[OK] Found opportunity: {opp['name']}")

    # Test check_deadline_one
    from check_deadlines import check_one as check_deadline_one

    print("[*] Calling check_deadline_one...")
    try:
        info, cost, searches = check_deadline_one(opp, ANTHROPIC_API_KEY)
        print(f"[OK] Check succeeded!")
        print(f"    Status: {info.get('status')}")
        print(f"    Deadlines: {len(info.get('deadlines', []))} found")
        print(f"    Cost: ${cost:.4f}")
        print(f"    Searches: {searches}")
        print(f"    Was estimated: {info.get('was_estimated')}")
        print(f"    Note: {info.get('deadline_note')[:80]}")
    except Exception as e:
        print(f"[ERROR] Check failed: {e}")
        import traceback
        traceback.print_exc()
        exit(1)

except Exception as e:
    print(f"[ERROR] Failed to fetch opportunity: {e}")
    import traceback
    traceback.print_exc()
    exit(1)

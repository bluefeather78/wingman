#!/usr/bin/env python3
"""Test the full deadline check flow like the server does."""
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
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

from check_deadlines import check_one as check_deadline_one, VALID_STATUS as DEADLINE_VALID_STATUS

opp_id = "ec12081"
DEADLINE_FIELDS = "id,name,org,url,summary,status,deadlines,opens_date,was_estimated,deadline_note,last_checked_at"

# Step 1: Get opportunity
print("[1] Fetching opportunity...")
query = urllib.parse.urlencode({"select": DEADLINE_FIELDS, "id": f"eq.{opp_id}"})
try:
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/opportunities?{query}",
        headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        rows = json.loads(resp.read())
    opp = rows[0] if rows else None

    if not opp:
        print("[ERROR] Opportunity not found")
        exit(1)

    print(f"[OK] Found: {opp['name']}")
    print(f"    last_checked_at: {opp.get('last_checked_at')}")
except Exception as e:
    print(f"[ERROR] {e}")
    import traceback
    traceback.print_exc()
    exit(1)

# Step 2: Check if cache is fresh
print("\n[2] Checking cache freshness...")

def deadline_cache_is_fresh(last_checked_at):
    if not last_checked_at:
        return False
    try:
        from datetime import datetime, timezone, timedelta
        checked = datetime.fromisoformat(last_checked_at.replace("Z", "+00:00"))
    except Exception:
        return False
    return datetime.now(timezone.utc) - checked < timedelta(days=7)

is_fresh = deadline_cache_is_fresh(opp.get("last_checked_at"))
print(f"Cache is fresh: {is_fresh}")

if is_fresh:
    print("[*] Would return cached data")
else:
    print("[*] Cache is stale, proceeding with fresh check")

    # Step 3: Call check_deadline_one
    print("\n[3] Calling check_deadline_one...")
    try:
        info, _cost, searches = check_deadline_one(opp, ANTHROPIC_API_KEY)
        print(f"[OK] Check succeeded!")
        print(f"    Status: {info.get('status')}")
        print(f"    Searches: {searches}")
        print(f"    Cost: ${_cost:.4f}")

        # Step 4: Process response
        print("\n[4] Processing response...")
        status = info.get("status") if info.get("status") in DEADLINE_VALID_STATUS else "unknown"
        deadlines = info.get("deadlines") or []
        if not isinstance(deadlines, list):
            deadlines = []
        deadlines = [d for d in deadlines if isinstance(d, dict) and d.get("date_iso")]

        if searches == 0:
            source_flag = "fresh, silent search"
        else:
            source_flag = "fresh, real search"

        patch = {
            "status": status,
            "deadlines": deadlines,
            "opens_date": info.get("opens_date"),
            "was_estimated": bool(info.get("was_estimated")),
            "deadline_note": info.get("deadline_note"),
            "last_checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

        print(f"    Source flag: {source_flag}")
        print(f"    Patch to write: {json.dumps(patch, indent=6)}")

        # Step 5: Try to patch
        print("\n[5] Patching opportunity...")
        query = urllib.parse.urlencode({"id": f"eq.{opp_id}"})
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
            resp.read()
        print("[OK] Patch succeeded!")

        # Step 6: Build response
        print("\n[6] Building response...")
        response = {**patch, "source": source_flag}
        print(f"[OK] Response: {json.dumps(response, default=str, indent=2)}")

    except Exception as e:
        print(f"[ERROR] Exception during check: {e}")
        import traceback
        traceback.print_exc()
        exit(1)

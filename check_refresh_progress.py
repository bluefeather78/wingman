#!/usr/bin/env python3
"""Check which opportunities were updated by the refresh agent.

Lists opportunities recently updated, grouped by update time, with counts.
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from supabase_common import load_dotenv, supabase_get

load_dotenv()
supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
service_key = os.environ.get("SUPABASE_SERVICE_KEY", "")
if not supabase_url or not service_key:
    print("[ERROR] SUPABASE_URL / SUPABASE_SERVICE_KEY not set in .env.")
    sys.exit(1)

# Get updates from last N hours (default: 3 hours)
hours_ago = 3
cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()

print(f"[OK] Fetching opportunities updated in last {hours_ago} hours...")
updated = supabase_get(supabase_url, "opportunities", {
    "select": "id,name,org,updated_at,source",
    "is_active": "eq.true",
    "source": "neq.scraper-national-20260820",
    "updated_at": f"gt.{cutoff}",
    "order": "updated_at.desc"
}, service_key)

all_active = supabase_get(supabase_url, "opportunities", {
    "select": "id",
    "is_active": "eq.true",
    "source": "neq.scraper-national-20260820"
}, service_key)

print(f"[OK] {len(updated)}/{len(all_active)} opportunities updated\n")

if not updated:
    print("No recent updates found.")
    sys.exit(0)

# Group by hour
by_hour = {}
for opp in updated:
    dt = datetime.fromisoformat(opp["updated_at"])
    hour_key = dt.strftime("%Y-%m-%d %H:00")
    if hour_key not in by_hour:
        by_hour[hour_key] = []
    by_hour[hour_key].append(opp)

# Display by hour
for hour_key in sorted(by_hour.keys(), reverse=True):
    opps = by_hour[hour_key]
    print(f"\n[{hour_key}] — {len(opps)} opportunities")
    for opp in opps[:10]:  # Show first 10 per hour
        org = f" ({opp['org']})" if opp.get('org') else ""
        print(f"  • {opp['name'][:70]}{org}")
    if len(opps) > 10:
        print(f"  ... and {len(opps) - 10} more")

# Summary
print(f"\n[SUMMARY]")
print(f"  Updated: {len(updated)}/{len(all_active)} ({100*len(updated)//len(all_active)}%)")
print(f"  Remaining: {len(all_active) - len(updated)}")
if updated:
    first = updated[-1]["updated_at"]  # oldest
    last = updated[0]["updated_at"]    # newest
    print(f"  Time span: {first[:16]} to {last[:16]}")

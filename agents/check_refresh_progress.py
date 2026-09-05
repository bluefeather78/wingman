#!/usr/bin/env python3
"""Check which opportunities were updated by the refresh agent.

Lists opportunities recently updated, grouped by update time, with counts.

WHAT `updated_at` ACTUALLY MEANS HERE, because this tool depends on it entirely:
`opportunities` has an **on-update TRIGGER** that stamps `updated_at` on every write —
verified 2026-08-23 by PATCHing a column back to its own value and watching the timestamp
move. It is NOT, as CLAUDE.md used to say, only stamped explicitly by server.py. So ANY
agent touching a row moves it, and this tool cannot tell one agent's writes from another's.

That went from theoretical to real when `agents/check_links.py` landed: a link-health pass writes
`link_status`/`link_checked_at` to every active row, so the first run after it reported
"1236/1236 opportunities updated" with the refresh agent having touched none of them.

Rows whose `link_checked_at` also falls inside the window are therefore excluded below.
That is a heuristic, not a proof — a row genuinely refreshed AND link-checked in the same
window is dropped with them — so the count it prints is a FLOOR, and it says so.
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from wingman.supabase_common import load_dotenv, supabase_get

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
    "select": "id,name,org,updated_at,source,link_checked_at",
    "is_active": "eq.true",
    "source": "neq.scraper-national-20260820",
    "updated_at": f"gt.{cutoff}",
    "order": "updated_at.desc"
}, service_key)

# Drop rows whose only recent write was a link-health pass — see the module docstring.
# Done client-side rather than as a PostgREST filter so a database without the link_*
# columns (db/link_health_schema.sql not run) still works: the key is simply absent and
# nothing is excluded, which is exactly the old behaviour.
link_touched = [o for o in updated
                if o.get("link_checked_at") and o["link_checked_at"] > cutoff]
if link_touched:
    ids = {o["id"] for o in link_touched}
    updated = [o for o in updated if o["id"] not in ids]
    print(f"[NOTE] Excluded {len(ids)} row(s) whose only recent write looks like a "
          f"agents/check_links.py pass. `updated_at` is trigger-stamped on EVERY write, so this "
          f"count is a floor — a row both refreshed and link-checked in this window is "
          f"excluded too.")

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

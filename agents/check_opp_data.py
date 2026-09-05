#!/usr/bin/env python3
"""Print what's actually stored for one or more opportunities - the deadline columns first.

The companion to wingman/clear_deadline_cache.py: look before you clear. Both were pointing at
column names that no longer exist (`deadlines` was renamed to `important_dates` by
scripts/one-off/merge_opens_date_into_important_dates.py, `opens_date` was folded into it as an "opens"
entry, `deadline_note` is `important_date_note`, and the cache stamp is
`dates_last_checked_at`, never `last_checked_at`). PostgREST 400s the WHOLE select on one
unknown column, so this script could only ever print an error.

USAGE:
    python -m agents.check_opp_data ec12081
    python -m agents.check_opp_data ec12081 us1787532028454524
"""
import argparse
import datetime
import json
import os
import sys

from wingman.supabase_common import load_dotenv, supabase_get

STALE_AFTER_DAYS = 7  # mirrors app/services/deadlines.DEADLINE_STALE_DAYS
FIELDS = ("id,name,org,url,is_active,moderation_status,status,important_dates,"
          "was_estimated,important_date_note,dates_last_checked_at,"
          "link_status,link_checked_at,review_status")


def cache_state(stamp):
    if not stamp:
        return "never checked - next view runs a fresh (paid) check"
    try:
        checked = datetime.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return f"unparseable ({stamp!r}) - treated as stale, so it will re-check"
    age = datetime.datetime.now(datetime.timezone.utc) - checked
    days = age.total_seconds() / 86400
    if age < datetime.timedelta(days=STALE_AFTER_DAYS):
        return f"FRESH - {days:.1f}d old, served from cache for another {STALE_AFTER_DAYS - days:.1f}d"
    return f"stale - {days:.1f}d old, next view runs a fresh (paid) check"


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ids", nargs="+", help="Opportunity id(s) to inspect.")
    args = parser.parse_args()

    load_dotenv()
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        print("[ERROR] SUPABASE_URL / SUPABASE_SERVICE_KEY not set in .env.")
        sys.exit(1)

    try:
        rows = supabase_get(url, "opportunities", {
            "select": FIELDS,
            "id": "in.(%s)" % ",".join(args.ids),
        }, key) or []
    except Exception as e:
        print(f"[ERROR] {e}")
        sys.exit(1)

    by_id = {r["id"]: r for r in rows}
    for opp_id in args.ids:
        opp = by_id.get(opp_id)
        print("=" * 72)
        if not opp:
            print(f"{opp_id}: not found")
            continue
        print(f"{opp['id']}  {opp.get('name')}")
        print(f"  org           {opp.get('org')}")
        print(f"  url           {opp.get('url')}")
        print(f"  active        {opp.get('is_active')}   moderation: {opp.get('moderation_status')}")
        print(f"  link          {opp.get('link_status')} (checked {opp.get('link_checked_at')})")
        print(f"  review        {opp.get('review_status')}")
        print("  -- deadlines --")
        print(f"  status        {opp.get('status')}")
        print(f"  was_estimated {opp.get('was_estimated')}")
        print(f"  note          {opp.get('important_date_note')}")
        print(f"  cache         {cache_state(opp.get('dates_last_checked_at'))}")
        print(f"                {opp.get('dates_last_checked_at')}")
        dates = opp.get("important_dates") or []
        if not dates:
            print("  important_dates: (none)")
        else:
            print(f"  important_dates ({len(dates)}):")
            for d in dates:
                if isinstance(d, dict):
                    print(f"    {d.get('date_iso')}  {d.get('type'):<12} {d.get('label')}")
                else:
                    print(f"    {json.dumps(d)}")


if __name__ == "__main__":
    main()

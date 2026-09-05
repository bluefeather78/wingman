#!/usr/bin/env python3
"""One-off backfill: the `opportunities` table's `deadlines` column has been renamed to
`important_dates` (run the rename below first) and is now meant to hold EVERY pertinent
date for an opportunity (registration opens, deadlines, event start/end, etc.), each tagged
with a "type" — not just narrow deadline milestones. This script folds the now-deprecated
`opens_date` column into `important_dates` as a `{"label": "Registration Opens", "date_iso":
opens_date, "type": "opens"}` entry (for every row that has a non-null opens_date and
doesn't already have an "opens"-type entry at that date), so no data is lost once
`opens_date` is dropped. `opens_date` itself is left untouched (still there, just
redundant) — not part of the regular dev loop; run manually, once.

SETUP:
    Run this SQL once in the Supabase SQL editor BEFORE running this script:
        alter table opportunities rename column deadlines to important_dates;

    .env (repo root) needs SUPABASE_URL / SUPABASE_SERVICE_KEY (service_role, RLS
    bypass — same as migrate_to_supabase.py / merge_description_into_summary.py).

USAGE:
    python merge_opens_date_into_important_dates.py --dry-run   # print a sample diff, no writes
    python merge_opens_date_into_important_dates.py              # apply the backfill

    After running for real, verify a sample of rows, then drop opens_date manually:
        alter table opportunities drop column opens_date;
"""
import argparse
import os
import sys

# This script lives under scripts/one-off/ but imports the repo-root shared libraries below by bare name
# (supabase_common), the way every root script does.
# Running it as `python scripts/one-off/merge_opens_date_into_important_dates.py` puts its OWN directory on sys.path, not the
# repo root, so the root has to be added explicitly.
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from wingman.supabase_common import load_dotenv, supabase_get, supabase_patch


def merge(important_dates, opens_date):
    important_dates = important_dates if isinstance(important_dates, list) else []
    if not opens_date:
        return important_dates
    already_present = any(
        isinstance(d, dict) and d.get("date_iso") == opens_date and d.get("type") == "opens"
        for d in important_dates
    )
    if already_present:
        return important_dates
    return important_dates + [{"label": "Registration Opens", "date_iso": opens_date, "type": "opens"}]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--sample", type=int, default=10,
                         help="How many diff examples to print (dry-run only).")
    args = parser.parse_args()

    load_dotenv()
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    service_key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not supabase_url or not service_key:
        print("[ERROR] SUPABASE_URL / SUPABASE_SERVICE_KEY not set in .env.")
        sys.exit(1)

    print("[OK] Fetching opportunities (id, important_dates, opens_date) from Supabase...")
    try:
        rows = supabase_get(supabase_url, "opportunities",
                             {"select": "id,important_dates,opens_date"}, service_key)
    except Exception as e:
        print(f"[ERROR] Fetch failed — have you run `alter table opportunities rename column "
              f"deadlines to important_dates;` yet? ({e})")
        sys.exit(1)
    print(f"[OK] {len(rows)} rows loaded.")

    to_update = []
    for row in rows:
        rid = row["id"]
        old = row.get("important_dates") or []
        new = merge(row.get("important_dates"), row.get("opens_date"))
        if new != old:
            to_update.append((rid, old, new))

    print(f"\n[SUMMARY] {len(to_update)} row(s) need an important_dates update "
          f"(out of {len(rows)} total).")

    if args.dry_run:
        print(f"\n[DRY RUN] Showing up to {args.sample} example diffs, no writes performed.")
        for rid, old, new in to_update[:args.sample]:
            print(f"\n--- {rid} ---")
            print(f"  OLD: {old}")
            print(f"  NEW: {new}")
        return

    updated = 0
    errors = 0
    for rid, old, new in to_update:
        try:
            supabase_patch(supabase_url, "opportunities", {"id": f"eq.{rid}"},
                            {"important_dates": new, "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}, service_key)
            updated += 1
            if updated % 100 == 0:
                print(f"  ...{updated}/{len(to_update)} updated")
        except Exception as e:
            errors += 1
            print(f"  [ERROR] {rid}: {e}")

    print(f"\n[DONE] Updated {updated} row(s), {errors} error(s).")
    if updated:
        print("[NOTE] Spot-check a few rows, then run `alter table opportunities drop column "
              "opens_date;` in the Supabase SQL editor. Run `python -m agents.export_json` to refresh "
              "the git-tracked opportunities.json snapshot.")


if __name__ == "__main__":
    main()

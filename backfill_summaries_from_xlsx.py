#!/usr/bin/env python3
"""One-off backfill: the legacy `opportunities` rows (source='wingman-seed', minted
from the old opportunities.json) have `summary` hard-truncated to ~200 characters
(and some have mangled-encoding artifacts from that same import). The original,
untruncated, cleanly-encoded text lives in Opportunities.xlsx (the source file that
opportunities.json was originally exported from) in its `Summary` column, keyed by
`id`.

This script reads that XLSX, matches rows to the Supabase `opportunities` table by
`id`, and PATCHes `summary` back to the full XLSX text wherever it differs from
what's currently stored. `description` is untouched (still null on legacy rows —
nothing in the app currently renders it). Not part of the regular dev loop; run
manually, once.

SETUP:
    .env (repo root) needs SUPABASE_URL / SUPABASE_SERVICE_KEY (service_role, RLS
    bypass — same as migrate_to_supabase.py).
    pip install openpyxl pandas (one-time, not a repo dependency).

USAGE:
    python backfill_summaries_from_xlsx.py --dry-run   # print a sample diff, no writes
    python backfill_summaries_from_xlsx.py              # apply the backfill
"""
import argparse
import os
import sys

import pandas as pd

from supabase_common import load_dotenv, supabase_get, supabase_patch

XLSX_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Opportunities.xlsx")


def load_xlsx_summaries():
    df = pd.read_excel(XLSX_PATH, sheet_name="Sheet1", header=1)
    out = {}
    for _, row in df.iterrows():
        rid = str(row["id"]).strip()
        summary = str(row["Summary"]).strip()
        if rid and summary and summary.lower() != "nan":
            out[rid] = summary
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--sample", type=int, default=10,
                         help="How many diff examples to print (dry-run only).")
    args = parser.parse_args()

    if not os.path.exists(XLSX_PATH):
        print(f"[ERROR] {XLSX_PATH} not found.")
        sys.exit(1)

    load_dotenv()
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    service_key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not supabase_url or not service_key:
        print("[ERROR] SUPABASE_URL / SUPABASE_SERVICE_KEY not set in .env.")
        sys.exit(1)

    print("[OK] Reading Opportunities.xlsx...")
    xlsx_summaries = load_xlsx_summaries()
    print(f"[OK] {len(xlsx_summaries)} rows with a Summary loaded from XLSX.")

    print("[OK] Fetching current opportunities table from Supabase...")
    existing = supabase_get(supabase_url, "opportunities", {"select": "id,summary"}, service_key)
    print(f"[OK] {len(existing)} rows loaded from Supabase.")

    to_update = []
    for row in existing:
        rid = row["id"]
        full_summary = xlsx_summaries.get(rid)
        if full_summary is None:
            continue
        if (row.get("summary") or "") != full_summary:
            to_update.append((rid, row.get("summary") or "", full_summary))

    print(f"\n[SUMMARY] {len(to_update)} row(s) need a summary update "
          f"(out of {len(existing)} total, {len(xlsx_summaries)} matched by id in XLSX).")

    if args.dry_run:
        print(f"\n[DRY RUN] Showing up to {args.sample} example diffs, no writes performed.")
        for rid, old, new in to_update[:args.sample]:
            print(f"\n--- {rid} ---")
            print(f"  OLD ({len(old)} chars): {old[:150]}")
            print(f"  NEW ({len(new)} chars): {new[:150]}")
        return

    updated = 0
    errors = 0
    for rid, old, new in to_update:
        try:
            supabase_patch(supabase_url, "opportunities", {"id": f"eq.{rid}"}, {"summary": new, "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}, service_key)
            updated += 1
            if updated % 100 == 0:
                print(f"  ...{updated}/{len(to_update)} updated")
        except Exception as e:
            errors += 1
            print(f"  [ERROR] {rid}: {e}")

    print(f"\n[DONE] Updated {updated} row(s), {errors} error(s).")
    if updated:
        print("[NOTE] Run `python export_json.py` to refresh the git-tracked opportunities.json snapshot.")


if __name__ == "__main__":
    main()

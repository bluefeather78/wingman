#!/usr/bin/env python3
"""One-off backfill: `description` (the longer 2-4 sentence field populated by
agents/scrape_opportunities.py/migrate_to_supabase.py) is never rendered anywhere in the
frontend — only the shorter `summary` field reaches the UI (see script.js's Finder
result cards). This script folds `description` into `summary` (concatenated, so no
detail is lost) for every row that has a non-empty `description`, so all of that
text becomes visible to users. `description` itself is left untouched (still there,
just redundant) — not part of the regular dev loop; run manually, once.

SETUP:
    .env (repo root) needs SUPABASE_URL / SUPABASE_SERVICE_KEY (service_role, RLS
    bypass — same as migrate_to_supabase.py / backfill_summaries_from_xlsx.py).

USAGE:
    python merge_description_into_summary.py --dry-run   # print a sample diff, no writes
    python merge_description_into_summary.py              # apply the backfill
"""
import argparse
import os
import sys

# This script lives under scripts/one-off/ but imports the repo-root shared libraries below by bare name
# (supabase_common), the way every root script does.
# Running it as `python scripts/one-off/merge_description_into_summary.py` puts its OWN directory on sys.path, not the
# repo root, so the root has to be added explicitly.
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from wingman.supabase_common import load_dotenv, supabase_get, supabase_patch


def merge(summary, description):
    summary = (summary or "").strip()
    description = (description or "").strip()
    if not description:
        return summary
    if not summary:
        return description
    if description in summary:
        return summary  # already merged in a prior run
    sep = "" if summary.endswith((".", "!", "?")) else "."
    return f"{summary}{sep} {description}"


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

    print("[OK] Fetching opportunities (id, summary, description) from Supabase...")
    rows = supabase_get(supabase_url, "opportunities", {"select": "id,summary,description"}, service_key)
    print(f"[OK] {len(rows)} rows loaded.")

    to_update = []
    for row in rows:
        rid = row["id"]
        old_summary = row.get("summary") or ""
        new_summary = merge(row.get("summary"), row.get("description"))
        if new_summary != old_summary:
            to_update.append((rid, old_summary, new_summary))

    print(f"\n[SUMMARY] {len(to_update)} row(s) need a summary update "
          f"(out of {len(rows)} total).")

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
        print("[NOTE] Run `python -m agents.export_json` to refresh the git-tracked opportunities.json snapshot.")


if __name__ == "__main__":
    main()

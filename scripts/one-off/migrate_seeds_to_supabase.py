#!/usr/bin/env python3
"""One-off: move agents/scrape_opportunities.py's hardcoded seed lists into the Supabase
`scraper_seeds` table, so the admin console can manage them.

Not part of the regular dev loop — run it once after creating the table, same as
migrate_to_supabase.py / migrate_users_to_supabase.py were run once each.

The literals stay in agents/scrape_opportunities.py afterwards as a fallback for when the table
is empty or unreachable (see seeds_common.load_seeds), so this migration COPIES rather
than moves.

SETUP: create the table first (see wingman/seeds_common.py's docstring for the DDL), then:

    python migrate_seeds_to_supabase.py --dry-run   # show what would be inserted
    python migrate_seeds_to_supabase.py             # actually insert

Safe to re-run: it skips any (mode, angle) pair already present, so it will not create
duplicates or clobber yield totals that seeds have already accumulated.
"""
import argparse
import os
import sys

# This script lives under scripts/one-off/ but imports the repo-root shared libraries below by bare name
# (scrape_opportunities, supabase_common), the way every root script does.
# Running it as `python scripts/one-off/migrate_seeds_to_supabase.py` puts its OWN directory on sys.path, not the
# repo root, so the root has to be added explicitly.
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from agents.scrape_opportunities import NATIONAL_SEEDS, SEATTLE_SEEDS
from wingman.supabase_common import load_dotenv, supabase_get, supabase_post


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be inserted without writing anything.")
    args = parser.parse_args()

    load_dotenv()
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    service_key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not supabase_url or not service_key:
        print("[ERROR] SUPABASE_URL / SUPABASE_SERVICE_KEY not set in .env.")
        sys.exit(1)

    try:
        existing = supabase_get(supabase_url, "scraper_seeds",
                                {"select": "id,mode,angle"}, service_key) or []
    except Exception as e:
        print(f"[ERROR] Could not read scraper_seeds — has the table been created yet?\n  {e}")
        sys.exit(1)

    already = {(r.get("mode"), (r.get("angle") or "").strip()) for r in existing}
    print(f"[OK] scraper_seeds currently holds {len(existing)} row(s).")

    rows = []
    for mode, seeds in (("national", NATIONAL_SEEDS), ("seattle", SEATTLE_SEEDS)):
        for i, angle in enumerate(seeds):
            if (mode, angle.strip()) in already:
                continue
            rows.append({
                "mode": mode,
                # `category` is still not-null in the table but is no longer part of a
                # seed — see wingman/seeds_common.py's docstring. Placeholder only; nothing reads it.
                "category": "unused",
                "angle": angle,
                "is_enabled": True,
                # Preserve the order the lists shipped in — the scraper reads
                # sort_order.asc, so runs keep hitting angles in the familiar sequence.
                "sort_order": i,
            })

    if not rows:
        print("[OK] Nothing to migrate — every hardcoded seed is already in the table.")
        return

    by_mode = {}
    for r in rows:
        by_mode[r["mode"]] = by_mode.get(r["mode"], 0) + 1
    print(f"[OK] {len(rows)} new seed(s) to insert: " +
          ", ".join(f"{m}={n}" for m, n in sorted(by_mode.items())))
    for r in rows:
        print(f"  [{r['mode']}#{r['sort_order']}] {r['angle'][:90]}...")

    if args.dry_run:
        print("[DRY RUN] Nothing written.")
        return

    supabase_post(supabase_url, "scraper_seeds", rows, service_key)
    print(f"[OK] Inserted {len(rows)} seed(s) into scraper_seeds.")


if __name__ == "__main__":
    main()

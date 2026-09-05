#!/usr/bin/env python3
"""Clear the on-demand deadline cache so the next view triggers a fresh, paid check.

Every row carries `dates_last_checked_at`; app/services/deadlines.deadline_cache_is_fresh()
serves the stored status/important_dates untouched while that timestamp is under
DEADLINE_STALE_DAYS old. Nulling it is the ONLY way to force a re-check before the TTL
expires, which matters whenever a row has been left holding a wrong answer.

THE COLUMN IS `dates_last_checked_at`, NOT `last_checked_at`. This script wrote the latter
until 2026-08-24 - a name that only ever existed in agents/check_deadlines.py's DDL comment - so
PostgREST rejected the whole PATCH and clearing the cache silently never worked at all. The
same wrong name is why app/services/deadlines.py carries a shouting comment about it.

THIS COSTS MONEY, INDIRECTLY. It makes no API call itself, but each cleared row will pay for
a two-phase Claude check (~$0.07, measured) the next time any student opens it. Clearing the
whole catalog is therefore a four-figure-cents decision, which is why --all makes you say so.

USAGE:
    python -m wingman.clear_deadline_cache ec12081                 # one row
    python -m wingman.clear_deadline_cache ec12081 us1787532028    # several
    python -m wingman.clear_deadline_cache --stale-only ec12081    # skip rows already due
    python -m wingman.clear_deadline_cache --all --yes-really      # every active row
    python -m wingman.clear_deadline_cache --all --dry-run         # count only, no writes
"""
import argparse
import datetime
import os
import sys

from wingman.supabase_common import load_dotenv, supabase_get, supabase_patch

# Mirrors app/services/deadlines.DEADLINE_STALE_DAYS. Duplicated rather than imported so
# this stays a stdlib-only repo-root script that does not drag in the FastAPI package.
STALE_AFTER_DAYS = 7
CACHE_COLUMN = "dates_last_checked_at"


def is_fresh(stamp):
    """True if this row would still be served from cache (same rule as the web layer)."""
    if not stamp:
        return False
    try:
        checked = datetime.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return False
    now = datetime.datetime.now(datetime.timezone.utc)
    return now - checked < datetime.timedelta(days=STALE_AFTER_DAYS)


def resolve_rows(url, key, ids, want_all):
    """The rows we would clear, as {id, name, dates_last_checked_at}."""
    if want_all:
        # Active rows only: an inactive row is not reachable from the app, so clearing it
        # buys nothing and would just make a reviewer's queue re-pay for a check.
        return supabase_get(url, "opportunities", {
            "select": f"id,name,{CACHE_COLUMN}",
            "is_active": "eq.true",
        }, key) or []
    # Chunked so a long id list cannot blow past PostgREST's URL length limit.
    rows = []
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        rows.extend(supabase_get(url, "opportunities", {
            "select": f"id,name,{CACHE_COLUMN}",
            "id": "in.(%s)" % ",".join(chunk),
        }, key) or [])
    found = {r["id"] for r in rows}
    for missing in [i for i in ids if i not in found]:
        print(f"[WARN] No opportunity with id {missing!r} - skipped.")
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ids", nargs="*", help="Opportunity id(s) to clear.")
    parser.add_argument("--all", action="store_true",
                        help="Clear every ACTIVE row. Requires --yes-really.")
    parser.add_argument("--yes-really", action="store_true",
                        help="Confirm a whole-catalog clear (see the cost note above).")
    parser.add_argument("--stale-only", action="store_true",
                        help="Skip rows whose cache has already expired - they are due anyway.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be cleared and write nothing.")
    args = parser.parse_args()

    if not args.ids and not args.all:
        parser.error("give at least one opportunity id, or --all")
    if args.ids and args.all:
        parser.error("--all clears everything; do not also pass ids")

    load_dotenv()
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        print("[ERROR] SUPABASE_URL / SUPABASE_SERVICE_KEY not set in .env.")
        sys.exit(1)

    try:
        rows = resolve_rows(url, key, args.ids, args.all)
    except Exception as e:
        print(f"[ERROR] Could not read the catalog: {e}")
        sys.exit(1)

    if args.stale_only:
        rows = [r for r in rows if is_fresh(r.get(CACHE_COLUMN))]
    # A row that has never been checked has nothing to clear.
    targets = [r for r in rows if r.get(CACHE_COLUMN)]

    if not targets:
        print("[OK] Nothing to clear - no matching row has a cached deadline check.")
        return

    fresh = sum(1 for r in targets if is_fresh(r.get(CACHE_COLUMN)))
    print(f"[OK] {len(targets)} row(s) carry a cached check; {fresh} of them are still "
          f"inside the {STALE_AFTER_DAYS}-day window and would otherwise not be re-checked.")
    print(f"     Clearing them queues ~${0.07 * len(targets):.2f} of checks, paid as "
          f"students open each one.")

    if args.dry_run:
        for r in targets[:20]:
            print(f"     would clear {r['id']}  {str(r.get('name'))[:60]}")
        if len(targets) > 20:
            print(f"     ... and {len(targets) - 20} more")
        print("[DRY RUN] No writes performed.")
        return

    if args.all and not args.yes_really:
        print("[ABORT] --all needs --yes-really. Re-run with --dry-run first if unsure.")
        sys.exit(1)

    cleared, errors = 0, 0
    for r in targets:
        try:
            # ONLY the cache stamp. status/important_dates are deliberately left in place:
            # a stale answer beats a blank card while the fresh check is pending, and
            # deadline_write_decision() needs the existing dates to decide whether a
            # later empty result may overwrite them.
            supabase_patch(url, "opportunities", {"id": f"eq.{r['id']}"},
                           {CACHE_COLUMN: None}, key)
            cleared += 1
        except Exception as e:
            errors += 1
            print(f"[ERROR] {r['id']}: {e}")

    print(f"[OK] Cleared {cleared} row(s), {errors} error(s). "
          f"The next view of each runs a fresh check.")


if __name__ == "__main__":
    main()

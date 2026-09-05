#!/usr/bin/env python3
"""Standalone pass to backfill opportunities.contact_email across the catalog.

See wingman/contact_email_common.py for how a row is resolved (regex-first, one cheap model call
only when a page has more than one plausible address). This script is the selection/
logging/writing wrapper around that shared logic — the same shape as the other five
background agents, agent_runs.agent = "contact_email_finder" — for a one-off or periodic
full-catalog pass.

For an ordinary metadata refresh, you don't need to run this separately:
agents/refresh_opportunities.py calls contact_email_common.resolve_contact_email() per row on its
own, so contact_email gets the same treatment as every other field on a normal pass. Run
this script on its own for the initial backfill, or later to force a re-check
(--force) without paying for a full metadata refresh alongside it.

SETUP:
    .env needs SUPABASE_URL, SUPABASE_SERVICE_KEY. GEMINI_API_KEY is optional — without
    it, every row that resolves for free (0 or 1 candidate email) still gets checked; only
    the rare multi-candidate row is left unresolved instead of erroring. (The disambiguation
    call runs on Gemini as of the 2026-08-29 M9 provider swap — see wingman/contact_email_common.py.)
    Run db/opportunities_contact_email_schema.sql once in the Supabase SQL editor first.

USAGE:
    python -m agents.find_contact_emails --preview --limit 10   # free: what would it touch
    python -m agents.find_contact_emails --limit 10             # a pilot pass
    python -m agents.find_contact_emails --all                  # every active row missing contact_email
    python -m agents.find_contact_emails --ids ec17081,ab12cd3  # named rows, e.g. a re-check
    python -m agents.find_contact_emails --force                # re-check rows that already have one
    python -m agents.find_contact_emails --dry-run --all         # calls the API, writes nothing
"""
import argparse
import datetime
import json
import os
import sys
import urllib.error

from wingman.agent_common import add_agent_args, apply_timing, emit_preview, snapshot_stamp
from wingman.contact_email_common import resolve_contact_email
from wingman.supabase_common import load_dotenv, supabase_get, supabase_insert_one, supabase_patch
from wingman import REPO_ROOT   # the repo root, defined once (see wingman/__init__.py)

DB_AGENT = "contact_email_finder"


def select_rows(supabase_url, service_key, args):
    """Which opportunities this run would process.

    Default is "active rows with no contact_email yet" — a plain re-run is idempotent and
    doesn't re-fetch pages already resolved. --force re-checks rows that already have one.
    """
    active = supabase_get(supabase_url, "opportunities", {
        "select": "id,name,org,url,contact_email",
        "is_active": "eq.true",
    }, service_key)

    if args.ids:
        wanted = {i.strip() for i in args.ids.split(",") if i.strip()}
        return [o for o in active if o["id"] in wanted], "ids"

    if not args.force:
        active = [o for o in active if not o.get("contact_email")]

    mode = "force" if args.force else "new"
    if args.limit:
        # Deterministic, not random: a pilot you can re-run and compare against needs the
        # same rows twice. Catalog order is stable.
        active = active[:args.limit]
        mode += f"-limit{args.limit}"
    return active, mode


def main():
    parser = argparse.ArgumentParser()
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--all", action="store_true",
                       help="Check every active row missing contact_email (the default).")
    scope.add_argument("--ids", help="Comma-separated opportunity ids to check, ignoring "
                                     "whether they already have a contact_email.")
    parser.add_argument("--limit", type=int,
                        help="Stop after N rows. This is how a pilot pass is run.")
    parser.add_argument("--force", action="store_true",
                        help="Re-check rows that already have a contact_email and overwrite it.")
    parser.add_argument("--dry-run", action="store_true",
                        help="No writes (opportunities or agent_runs) — still fetches pages "
                             "and still calls the API at full cost, but dumps results to a "
                             "local JSON snapshot instead.")
    add_agent_args(parser, default_timeout=60, default_min_delay=5)
    args = parser.parse_args()
    apply_timing(args, gemini=True)  # M9: the disambiguation call is Gemini, so use its limiter

    load_dotenv()
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    service_key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    if not supabase_url or not service_key:
        print("[ERROR] SUPABASE_URL / SUPABASE_SERVICE_KEY not set in .env.")
        sys.exit(1)
    if not gemini_key:
        print("[WARN] GEMINI_API_KEY not set — multi-candidate pages will be left "
              "unresolved. Rows with 0 or 1 candidate email still resolve for free.")

    print("[OK] Fetching active catalog from Supabase...")
    items, mode = select_rows(supabase_url, service_key, args)
    print(f"[OK] {len(items)} row(s) to check ({mode}).")

    if args.preview:
        emit_preview(len(items), "rows", [o.get("name", "?") for o in items], mode=mode)
        return

    if not items:
        print("[OK] Nothing to do.")
        return

    # A dry run is logged too, with a -dryrun mode suffix: it skips DATABASE writes, not
    # API calls, so its (small) spend must not be invisible.
    run_mode = mode + ("-dryrun" if args.dry_run else "")
    run_row = supabase_insert_one(supabase_url, "agent_runs", {
        "agent": DB_AGENT,
        "mode": run_mode,
        "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }, service_key)
    run_id = run_row["id"] if run_row else None

    total_cost, found, none_rows, errors, model_calls = 0.0, 0, 0, 0, 0
    snapshot = []

    for i, opp in enumerate(items):
        print(f"[{i + 1}/{len(items)}] {opp['name'][:55]}...", end=" ")
        try:
            email, cost, used_model, fetched = resolve_contact_email(opp, gemini_key)
            total_cost += cost
            model_calls += 1 if used_model else 0
            entry = {"id": opp["id"], "name": opp["name"], "url": opp["url"],
                     "pages_checked": fetched, "cost_usd": round(cost, 4)}
            if email:
                found += 1
                entry["changes"] = {"contact_email": email}
                print(f"{email} ({'model' if used_model else 'regex'}), ${cost:.4f}")
                if not args.dry_run:
                    supabase_patch(supabase_url, "opportunities", {"id": f"eq.{opp['id']}"},
                                   {"contact_email": email,
                                    "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()},
                                   service_key)
            else:
                none_rows += 1
                entry["changes"] = {}
                print(f"no email found, ${cost:.4f}")
            snapshot.append(entry)
        except urllib.error.HTTPError as e:
            errors += 1
            print(f"[ERROR] HTTP {e.code}")
        except Exception as e:
            errors += 1
            print(f"[ERROR] {e}")

    print(f"\n[SUMMARY] checked: {len(items)}, emails found: {found}, none found: {none_rows}, "
          f"errors: {errors}, model calls: {model_calls}/{len(items)}, cost: ${total_cost:.4f}")

    if args.dry_run:
        stamp = snapshot_stamp()
        path = os.path.join(REPO_ROOT,
                            f"find_contact_emails_dry_run_{stamp}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2, ensure_ascii=False)
        print(f"[OK] Wrote dry-run snapshot: {path}")
        print("[DRY RUN] No writes performed.")

    if run_id is not None:
        supabase_patch(supabase_url, "agent_runs", {"id": f"eq.{run_id}"}, {
            "finished_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "items_processed": len(items),
            "items_updated": 0 if args.dry_run else found,
            "errors": errors,
            "cost_usd": round(total_cost, 4),
            "notes": f"model_calls={model_calls}"
                    + (f", would_have_found={found}" if args.dry_run else ""),
        }, service_key)
        print(f"[OK] Logged agent_runs id={run_id}.")


if __name__ == "__main__":
    main()

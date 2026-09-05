#!/usr/bin/env python3
"""Lifecycle-email sweeps — the local/manual runner.

In production these sweeps are triggered by POST /api/email/sweep on the deployed service
(see .github/workflows/lifecycle-emails.yml). This script is the same code path run
locally: for previewing who is due, for a manual catch-up if the scheduler was down, and
for reading the outcome without opening the console.

    python -m wingman.send_lifecycle_emails --preview               # who is due (both kinds). Sends nothing.
    python -m wingman.send_lifecycle_emails --dry-run               # same, with the full detail list
    python -m wingman.send_lifecycle_emails --kind deadline --preview
    python -m wingman.send_lifecycle_emails                         # send (both kinds)

Two kinds run here: the trial-ending reminder and the deadline-alert digest. --kind picks
one (default: all).

Unlike the six catalog agents, ALL THREE TIERS HERE ARE FREE — there is no model in this
path. What --preview protects is not money, it is a student's inbox.

Running this while the scheduler also runs is safe. The email_sends claim is what makes a
second pass a no-op, not the operator's restraint — that is the entire point of writing the
row before calling the provider (see app/services/email.py).

Sits at the repo root with the other offline scripts because that is where this repo keeps
things a person runs by hand, and it imports app/ the same way ops/ does.
"""
import argparse
import json
import sys

from app.config import RESEND_API_KEY, EMAIL_FROM, TRIAL_REMINDER_DAYS
from app.services import email as email_service


def _report_setup_or_error(label, result):
    """Print the not-ok explanation for one sweep. Returns nothing."""
    if result.get("table_ready") is False:
        print(f"[{label}] NOT SET UP: run {result.get('setup_sql_file')} in the "
              f"Supabase SQL editor.")
    else:
        print(f"[{label}] FAILED: {result.get('error')}")


def _print_trial(result, dry):
    if not result.get("ok"):
        _report_setup_or_error("trial", result)
        return
    print(f"[trial] window: next {result['window_days']} day(s)   due: {result['due']}")
    if dry:
        for entry in result.get("details", []):
            print(f"  would send -> {entry['userid']:<24} "
                  f"{entry.get('email') or '(no email)':<34} "
                  f"trial ends {entry.get('trial_ends_at')}")
        return
    print(f"[trial] sent {result['sent']}   skipped {result['skipped']}   "
          f"failed {result['failed']}   mock {result['mock']}")
    for entry in result.get("details", []):
        if entry.get("state") == "failed":
            print(f"  FAILED {entry['userid']}: {entry.get('reason')}")


def _print_deadline(result, dry):
    if not result.get("ok"):
        _report_setup_or_error("deadline", result)
        return
    print(f"[deadline] accounts with due deadlines: {result['accounts_with_due']}")
    if dry:
        for entry in result.get("details", []):
            print(f"  would send -> {entry['userid']:<24} "
                  f"{entry.get('email') or '(no email)':<34} {entry.get('units')} deadline(s)")
            for d in entry.get("deadlines", []):
                est = " (est.)" if d.get("estimated") is not False else ""
                print(f"      {d['date']}  T-{d['days_left']:<3} {d['name']}{est}")
        return
    print(f"[deadline] sent {result['sent']}   skipped {result['skipped']}   "
          f"failed {result['failed']}   mock {result['mock']}   "
          f"(units alerted {result['units_alerted']}, "
          f"already sent {result['units_already_sent']})")
    for entry in result.get("details", []):
        if entry.get("state") == "failed":
            print(f"  FAILED {entry['userid']}: {entry.get('reason')}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kind", choices=["trial", "deadline", "all"], default="all",
                    help="Which sweep(s) to run (default: all).")
    ap.add_argument("--preview", action="store_true",
                    help="Resolve who is due and exit. Sends nothing.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Like --preview, but prints the full per-account detail list.")
    ap.add_argument("--days", type=int, default=None,
                    help=f"Trial reminder window in days (default {TRIAL_REMINDER_DAYS}).")
    ap.add_argument("--limit", type=int, default=500,
                    help="Maximum accounts to consider in one pass.")
    ap.add_argument("--json", action="store_true", help="Print the raw result as JSON.")
    args = ap.parse_args()

    dry = args.preview or args.dry_run
    mode = ("MOCK (no RESEND_API_KEY — nothing will be sent)" if not RESEND_API_KEY
            else f"LIVE as {EMAIL_FROM}")
    print(f"[sweep] mode: {mode}   kind: {args.kind}")

    results = {}
    if args.kind in ("all", "trial"):
        results["trial"] = email_service.run_trial_sweep(
            days=args.days, dry_run=dry, limit=args.limit)
    if args.kind in ("all", "deadline"):
        results["deadline"] = email_service.run_deadline_alert_sweep(
            dry_run=dry, limit=args.limit)

    if args.json:
        print(json.dumps(results, indent=2, default=str))
        return 0 if all(r.get("ok") for r in results.values()) else 1

    if "trial" in results:
        _print_trial(results["trial"], dry)
    if "deadline" in results:
        _print_deadline(results["deadline"], dry)

    if dry:
        print("[sweep] preview only — nothing was sent.")
    return 0 if all(r.get("ok") for r in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())

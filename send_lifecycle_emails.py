#!/usr/bin/env python3
"""Trial-ending reminder sweep — the local/manual runner.

In production this sweep is triggered by POST /api/email/sweep on the deployed service
(see .github/workflows/lifecycle-emails.yml). This script is the same code path run
locally: for previewing who is due, for a manual catch-up if the scheduler was down, and
for reading the outcome without opening the console.

    python send_lifecycle_emails.py --preview     # who is due. Sends nothing. Free.
    python send_lifecycle_emails.py --dry-run     # same, with the full detail list
    python send_lifecycle_emails.py               # send

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


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--preview", action="store_true",
                    help="Resolve who is due and exit. Sends nothing.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Like --preview, but prints the full per-account detail list.")
    ap.add_argument("--days", type=int, default=None,
                    help=f"Reminder window in days (default {TRIAL_REMINDER_DAYS}).")
    ap.add_argument("--limit", type=int, default=500,
                    help="Maximum accounts to consider in one pass.")
    ap.add_argument("--json", action="store_true", help="Print the raw result as JSON.")
    args = ap.parse_args()

    dry = args.preview or args.dry_run
    mode = "MOCK (no RESEND_API_KEY — nothing will be sent)" if not RESEND_API_KEY else f"LIVE as {EMAIL_FROM}"
    print(f"[sweep] mode: {mode}")

    result = email_service.run_trial_sweep(days=args.days, dry_run=dry, limit=args.limit)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0 if result.get("ok") else 1

    if not result.get("ok"):
        # A missing table is a setup step, not a crash — say which one it is.
        if result.get("table_ready") is False:
            print(f"[sweep] NOT SET UP: run {result.get('setup_sql_file')} in the "
                  f"Supabase SQL editor.")
        else:
            print(f"[sweep] FAILED: {result.get('error')}")
        return 1

    print(f"[sweep] window: next {result['window_days']} day(s)   due: {result['due']}")
    if dry:
        for entry in result.get("details", []):
            print(f"  would send -> {entry['userid']:<24} {entry.get('email') or '(no email)':<34} "
                  f"trial ends {entry.get('trial_ends_at')}")
        print("[sweep] preview only — nothing was sent.")
        return 0

    print(f"[sweep] sent {result['sent']}   skipped {result['skipped']}   "
          f"failed {result['failed']}   mock {result['mock']}")
    # 'skipped' is usually the healthy case (already claimed by an earlier pass today);
    # failures are the ones worth naming, since nothing else surfaces them locally.
    for entry in result.get("details", []):
        if entry.get("state") == "failed":
            print(f"  FAILED {entry['userid']}: {entry.get('reason')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

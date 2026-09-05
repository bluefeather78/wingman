#!/usr/bin/env python3
"""Wrap every remaining bare-SHA-256 password row in argon2.

SECURITY_HARDENING_PLAN.md S1-11, finding L1.

THE PROBLEM. Rows that have not logged in since Phase 2 still hold the bare client
SHA-256 in users.password_hash. The client sends sha256(password) unsalted as its
`passwordHash` and the server compares it to that stored value directly — so the stored
value IS the credential on the wire. Anyone who reads the users table can sign in as those
accounts with no cracking at all: paste the column into the login request and it matches.

THE FIX, and why it is this shape. argon2(sha256hex) verifies through the EXISTING code
path with no change anywhere: app.auth.passwords.verify_password already tries
_ph.verify(stored, client_hash) for any non-legacy value, and hash_password() is exactly
_ph.hash(client_hash). So wrapping a legacy row is invisible to the user, needs no
re-entry of a password, and cannot lock anyone out — the same SHA-256 the browser sends
still verifies, it just no longer sits in the database in replayable form.

The login-time upgrade already does this one account at a time (verify_password returns
needs_upgrade=True on a legacy row). This script is for the accounts that never log in
again, which are precisely the ones nobody would notice staying vulnerable.

FREE — no API calls, no network beyond Supabase. Reads and writes users.password_hash only.

    python scripts/one-off/wrap_legacy_password_hashes.py            # preview, no writes
    python scripts/one-off/wrap_legacy_password_hashes.py --commit   # apply

Idempotent: an argon2 row is skipped, so re-running does nothing. Safe to run repeatedly,
and worth re-running after a while to catch rows that were dormant the first time.
"""
import argparse
import os
import sys

# This script lives under scripts/one-off/ but imports the repo-root packages by bare name,
# the way every script here does. Running it as `python scripts/one-off/x.py` puts its OWN
# directory on sys.path, not the repo root, so the root has to be added explicitly.
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from app.auth.passwords import hash_password, is_legacy_hash, verify_password
from wingman.supabase_common import load_dotenv, supabase_get, supabase_patch


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--commit", action="store_true",
                    help="Actually write. Without it this only reports what it would do.")
    ap.add_argument("--limit", type=int, default=None,
                    help="Stop after this many rows (for a cautious first pass).")
    args = ap.parse_args()

    load_dotenv(os.path.join(ROOT, ".env"))
    url = os.environ.get("SUPABASE_URL", "")
    # The SERVICE key, not the anon key: password_hash is behind RLS and must stay there.
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        print("[ERROR] SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in .env.")
        return 1

    # userid + password_hash only. There is no reason for this script to hold anybody's
    # email, name or app data in memory (S1-15's rule, applied here too).
    rows = supabase_get(url, "users", {"select": "userid,password_hash"}, key) or []
    legacy = [r for r in rows if is_legacy_hash(r.get("password_hash"))]
    if args.limit:
        legacy = legacy[:args.limit]

    print(f"[INFO] {len(rows)} accounts; {len(legacy)} still hold a bare SHA-256.")
    if not legacy:
        print("[OK] Nothing to do.")
        return 0
    if not args.commit:
        print("[PREVIEW] Re-run with --commit to wrap them. No writes made.")
        return 0

    wrapped, failed = 0, 0
    for row in legacy:
        userid = row["userid"]
        stored = row["password_hash"]
        new_hash = hash_password(stored)
        # Prove the wrap verifies BEFORE writing it. A row that would not verify is a row
        # whose owner is locked out, and there is no way back — the plaintext is gone and
        # the SHA-256 is what we are about to overwrite.
        ok, _ = verify_password(new_hash, stored)
        if not ok:
            print(f"[ERROR] Wrap did not verify for {userid}; left unchanged.")
            failed += 1
            continue
        try:
            supabase_patch(url, "users", {"userid": f"eq.{userid}"},
                           {"password_hash": new_hash}, key)
            wrapped += 1
        except Exception as e:                                     # noqa: BLE001
            print(f"[ERROR] Could not write {userid}: {e}")
            failed += 1

    print(f"[OK] Wrapped {wrapped}; {failed} failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Strip already-stored `gender` from every saved student profile.

Wingman stopped storing gender (the Gender basics tile was removed, and the profile
extraction prompt no longer asks for it — see the newfeature branch). New writes never
carry it. This one-off removes it from profiles saved BEFORE that change, so "we do not
store gender" is true of the rows already written, not merely of the next ones.

WHERE IT LIVES. Each users row has a `data` jsonb. The student profile is stored under the
`student-profile` key as a JSON object (unlike hs-tracker-data / hs-tracker-saved, which are
stored as JSON strings). Gender sat at:

    data -> 'student-profile' -> 'basics' -> 'fields' -> 'gender'

The script also removes a defensive `basics.gender` (a flatter legacy shape) if present, and
handles the case where `student-profile` was stored as a JSON string rather than an object.

NOT TOUCHED: the synthesized profile PROSE (`student-profile.synthesized`) is free text the
model wrote from what the student said; if a student volunteered their gender it may appear
there. That is unstructured and is not what "stored gender" means here — it is left alone.

FREE — no API calls, no model, no cost. Reads and writes the users.data jsonb only.

    python scripts/one-off/purge_stored_gender.py            # preview, no writes
    python scripts/one-off/purge_stored_gender.py --commit   # apply

Idempotent: a profile with no stored gender is skipped, so re-running does nothing.
"""
import argparse
import json
import os
import sys

# This script lives under scripts/one-off/ but imports the repo-root packages by bare name,
# the way every script here does. Running it as `python scripts/one-off/x.py` puts its OWN
# directory on sys.path, not the repo root, so the root has to be added explicitly.
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from wingman.supabase_common import load_dotenv, supabase_get, supabase_patch

PROFILE_KEY = "student-profile"


def _strip_gender(profile):
    """Remove gender from one profile record in place. Returns True if anything changed."""
    if not isinstance(profile, dict):
        return False
    changed = False
    # The real location: basics.fields.gender
    basics = profile.get("basics")
    if isinstance(basics, dict):
        fields = basics.get("fields")
        if isinstance(fields, dict) and "gender" in fields:
            del fields["gender"]
            changed = True
        # Defensive: a flatter legacy shape where basics itself held the value.
        if "gender" in basics:
            del basics["gender"]
            changed = True
    return changed


def _purge_row_data(data):
    """Strip gender from a user's whole `data` blob. Returns True if anything changed.

    Handles student-profile stored either as an object or (defensively) as a JSON string,
    re-encoding in whatever form it came in so nothing else about the row shape changes.
    """
    if not isinstance(data, dict) or PROFILE_KEY not in data:
        return False
    sp = data[PROFILE_KEY]

    if isinstance(sp, str):
        try:
            parsed = json.loads(sp)
        except (ValueError, TypeError):
            return False
        if _strip_gender(parsed):
            data[PROFILE_KEY] = json.dumps(parsed)
            return True
        return False

    return _strip_gender(sp)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--commit", action="store_true",
                    help="Actually write. Without it this only reports what it would do.")
    ap.add_argument("--limit", type=int, default=None,
                    help="Stop after this many changed rows (for a cautious first pass).")
    args = ap.parse_args()

    load_dotenv(os.path.join(ROOT, ".env"))
    url = os.environ.get("SUPABASE_URL", "")
    # The SERVICE key, not the anon key: the users table is behind RLS with no policies.
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        print("[ERROR] SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in .env.")
        return 1

    rows = supabase_get(url, "users", {"select": "userid,data"}, key,
                        order_by="userid") or []
    print(f"[INFO] {len(rows)} accounts scanned.")

    to_fix = []
    for row in rows:
        data = row.get("data")
        # Work on a copy so a no-op row is never rewritten.
        if _purge_row_data(data):
            to_fix.append((row["userid"], data))

    if args.limit:
        to_fix = to_fix[:args.limit]

    print(f"[INFO] {len(to_fix)} profiles still carry a stored gender.")
    if not to_fix:
        print("[OK] Nothing to do.")
        return 0
    if not args.commit:
        for userid, _ in to_fix:
            print(f"[PREVIEW] would strip gender from {userid}")
        print("[PREVIEW] Re-run with --commit to write. No changes made.")
        return 0

    fixed, failed = 0, 0
    for userid, data in to_fix:
        try:
            supabase_patch(url, "users", {"userid": f"eq.{userid}"}, {"data": data}, key)
            fixed += 1
        except Exception as e:                                     # noqa: BLE001
            print(f"[ERROR] Could not write {userid}: {e}")
            failed += 1

    print(f"[OK] Stripped gender from {fixed} profiles; {failed} failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

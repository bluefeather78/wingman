#!/usr/bin/env python3
"""Create / sign in a throwaway TEST account, and print its access + refresh tokens.

Why this exists: the whole app lives behind `(app)/_layout.tsx`'s auth gate, so
verifying anything on Fresh Finds or the Quest Log means being signed in. Typing a
password into the login form by hand every time is slow, and hard-coding one into a
command records it in shell history (the same trap the API-key rule in CLAUDE.md
exists for). So the credential is generated once, kept in a gitignored file next to
`.env`, and replayed through the app's OWN /api/register + /api/login endpoints —
nothing here touches Supabase directly or knows anything the client doesn't.

The client contract is `passwordHash = SHA-256(password)` (see the register handler in
app/routes/account.py); the server stores argon2 of that. This mirrors it exactly, so a
test account is an ordinary account in every respect and exercises the real auth path.

Usage:
    python dev_test_account.py              # create if needed, print tokens as JSON
    python dev_test_account.py --tokens-only
    python dev_test_account.py --base http://127.0.0.1:8000

Paste the printed snippet into the browser console on the web app's origin to sign in
without the form. THIS IS A DEV UTILITY — the account it makes is a real row in the
users table, so use it against a local server, and delete the row when done.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
import urllib.error
import urllib.request

CRED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".test-account.json")
DEFAULT_BASE = "http://127.0.0.1:8000"


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_or_make_credentials() -> dict:
    """One stable identity per machine, so a test account is not recreated every run."""
    if os.path.exists(CRED_FILE):
        with open(CRED_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    suffix = secrets.token_hex(3)
    creds = {
        "userid": f"devtest_{suffix}",
        # A real address is never needed and must never be one: this account gets signed
        # up to nothing, and a typo'd real address would send mail to a stranger.
        "email": f"devtest_{suffix}@example.invalid",
        "password": secrets.token_urlsafe(18),
        "firstName": "Dev",
        "lastName": "Tester",
        "location": "Seattle, WA",
    }
    with open(CRED_FILE, "w", encoding="utf-8") as fh:
        json.dump(creds, fh, indent=2)
    print(f"[new] wrote credentials to {CRED_FILE}", file=sys.stderr)
    return creds


def post(base: str, path: str, payload: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        base.rstrip("/") + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"error": raw}


def sign_in(base: str, creds: dict) -> dict:
    """Log in; register first if the account does not exist yet.

    Login is tried FIRST rather than register-then-fallback: register is rate limited far
    more tightly than login, and burning that limiter on an account that already exists
    would lock the tool out of its own account for minutes.
    """
    pw_hash = sha256_hex(creds["password"])
    status, body = post(base, "/api/login", {"userid": creds["userid"], "passwordHash": pw_hash})
    if status == 200:
        return body
    if status != 404:
        raise SystemExit(f"login failed ({status}): {body.get('error', body)}")

    print("[new] account does not exist yet — registering", file=sys.stderr)
    status, body = post(base, "/api/register", {
        "firstName": creds["firstName"],
        "lastName": creds["lastName"],
        "email": creds["email"],
        "userid": creds["userid"],
        "passwordHash": pw_hash,
        "location": creds["location"],
        # The consent gate is re-checked server-side and refuses the account otherwise.
        # A test account is an adult account so the parental-permission path stays
        # untouched — that branch is about real minors and is not ours to simulate.
        "isAdult": True,
        "parentalConsent": False,
        "acceptedTerms": True,
    })
    if status != 200:
        raise SystemExit(f"register failed ({status}): {body.get('error', body)}")
    return body


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default=os.environ.get("WINGMAN_API_BASE", DEFAULT_BASE),
                    help=f"API origin (default {DEFAULT_BASE})")
    ap.add_argument("--tokens-only", action="store_true",
                    help="print only {accessToken, refreshToken} as JSON")
    args = ap.parse_args()

    creds = load_or_make_credentials()
    body = sign_in(args.base, creds)
    # issue_tokens() names these `token` / `refresh_token` (app/auth/tokens.py) — not the
    # camelCase the rest of the login payload uses. Read both so a rename can't silently
    # return None here.
    access = body.get("token") or body.get("accessToken")
    refresh = body.get("refresh_token") or body.get("refreshToken")
    if not access:
        raise SystemExit(f"signed in but no access token in response: {list(body)}")

    if args.tokens_only:
        print(json.dumps({"accessToken": access, "refreshToken": refresh}))
        return

    print(json.dumps({
        "userid": creds["userid"],
        "email": creds["email"],
        "accessToken": access,
        "refreshToken": refresh,
    }, indent=2))
    # tokenStore.ts writes these two keys on web; setting them by hand is a sign-in.
    print("\n// paste in the browser console on the web app's origin, then reload:",
          file=sys.stderr)
    print(f"localStorage.setItem('wingman.access_token', {json.dumps(access)});\n"
          f"localStorage.setItem('wingman.refresh_token', {json.dumps(refresh or '')});",
          file=sys.stderr)


if __name__ == "__main__":
    main()

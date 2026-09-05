"""Account routes: register and login. Translated from server.py's handle_register /
handle_login (docs/archive/PLAN_1_decompose.md). Paths and JSON shapes unchanged.
"""
import json
import urllib.error

from fastapi import APIRouter, Request, Depends

from app.config import EMAIL_RE
from app.core import (
    get_user_account, get_user_by_email, create_user, MissingUserColumns, DuplicateEmail,
    _check_signup_consent, ensure_trial_started, touch_user_activity,
    update_password_hash, normalize_email, pseudonym,
)
from app.deps import (json_body, json_response, json_error, client_ip, login_response,
                      opaque_error, DB_UNAVAILABLE)
from app.auth import hash_password, verify_password, AuthConfigError
from app.auth.passwords import is_valid_client_hash
from app.auth.ratelimit import (login_limiter, login_ip_limiter, register_limiter,
                                register_email_limiter)
from app.services.email import send_lifecycle_email_async

router = APIRouter()

# The single answer to every sign-in failure (S1-7, finding M7). Named rather than repeated
# so the two call sites cannot drift back apart — which is exactly how they got here.
#
# Residual, recorded rather than hidden: a missing account skips the argon2 verify, so the
# two paths still differ by roughly the hash time. Closing that means running a dummy verify
# on every miss, and argon2 is configured at 64 MiB / 1-3s per call here — so the "fix"
# would hand an attacker a far better DoS lever than the oracle it removes. The rate
# limiter above is what bounds this in the meantime; the argon2 parameters are Phase 2.
LOGIN_FAILED = "Incorrect user ID or password."


@router.post("/api/register")
def handle_register(request: Request, body: dict = Depends(json_body)):
    if not register_limiter.allow(client_ip(request)):
        return json_error(429, "Too many sign-up attempts. Please wait a few minutes "
                               "and try again.")
    first_name = (body.get("firstName") or "").strip()
    last_name = (body.get("lastName") or "").strip()
    email = (body.get("email") or "").strip()
    userid = (body.get("userid") or "").strip()
    password_hash = body.get("passwordHash") or ""
    # Location is no longer collected at sign-up — it is captured in-app on the student
    # profile. Still read from the body (older clients may send it) and default to empty.
    location = (body.get("location") or "").strip()
    if not all([first_name, last_name, email, userid, password_hash]):
        return json_error(400, "Missing required fields.")

    # Consent gate, re-checked server-side (the browser control is a convenience).
    is_adult = bool(body.get("isAdult"))
    parental_consent = bool(body.get("parentalConsent"))
    accepted_terms = bool(body.get("acceptedTerms"))
    consent_error = _check_signup_consent(is_adult, parental_consent, accepted_terms)
    if consent_error:
        return json_error(400, consent_error)

    if not EMAIL_RE.match(email):
        return json_error(400, "Please enter a valid email address.")

    # S1-11, finding L1: the client contract is sha256(password) as 64 lowercase hex chars,
    # and nothing checked it. A caller sending a one-character "hash" got an account whose
    # password-equivalent secret was one character — the browser's own hashing was the only
    # thing preventing it, and a server must not depend on its client for that.
    if not is_valid_client_hash(password_hash):
        return json_error(400, "Your browser could not prepare that password. Reload the "
                               "page and try again.")

    # S1-7, finding M7: the "already exists with that email" 409 below is an enumeration
    # oracle, and this population is largely minors — a list of "these addresses have
    # accounts" is itself sensitive. Bounded per ADDRESS rather than per IP, so a script
    # cannot walk a list from one machine. Checked after the format check so a malformed
    # address does not consume a real one's budget, and before the lookup so the probe does
    # not even reach Supabase.
    if not register_email_limiter.allow(normalize_email(email)):
        return json_error(429, "Too many sign-up attempts for that email address. "
                               "Please wait and try again.")

    key = userid.lower()
    try:
        if get_user_account(key):
            return json_error(409, "That user ID is already taken.")
        existing = get_user_by_email(email)
    except Exception as e:
        return opaque_error(502, DB_UNAVAILABLE, e, op="account.db")
    if existing:
        return json_error(409, "An account already exists with that email "
                               "address. Sign in instead, or use a different "
                               "email.")

    # Store argon2(client SHA-256), not the bare client hash. The client contract is
    # unchanged — it still sends passwordHash — but the value at rest is no longer
    # password-equivalent. See app/auth/passwords.py.
    stored_hash = hash_password(password_hash)
    try:
        create_user(key, first_name, last_name, email, stored_hash, location,
                    is_adult=is_adult, parental_consent=parental_consent)
    except MissingUserColumns:
        return json_error(503, "Accounts are temporarily unavailable: the "
                               "database is missing the subscription and "
                               "consent columns. Run db/subscription_schema.sql "
                               "in the Supabase SQL editor, then try again.")
    except DuplicateEmail:
        return json_error(409, "An account already exists with that email "
                               "address. Sign in instead, or use a different "
                               "email.")
    except urllib.error.HTTPError as e:
        if e.code == 409:
            return json_error(409, "That user ID is already taken.")
        return opaque_error(502, DB_UNAVAILABLE, e, op="account.db")
    except Exception as e:
        return opaque_error(502, DB_UNAVAILABLE, e, op="account.db")

    # Auto-login the new account: the client goes straight to showApp() after register, so
    # it needs a token immediately or every gated call would 401. Fetch the fresh row and
    # return the same signed-in payload login does (identity + access/refresh tokens).
    record = get_user_account(key)

    # Welcome email, fired and forgotten. Async because registration must not block on — or
    # fail because of — a mail provider: the account exists either way, and an email that
    # never arrives is a far smaller problem than a signup that 502s. Deduped by the
    # email_sends claim, so a retried registration cannot produce two.
    send_lifecycle_email_async(key, "welcome", record=record)
    try:
        return json_response(200, login_response(record))
    except AuthConfigError as e:
        # Not str(e): that message names JWT_SECRET and where to set it, which is
        # operational detail a signed-out caller has no business reading (S1-13, L5).
        return opaque_error(503, "Sign-in is temporarily unavailable. Please try again "
                                 "shortly.", e, op="auth.config")


@router.post("/api/login")
def handle_login(request: Request, body: dict = Depends(json_body)):
    userid = (body.get("userid") or "").strip()
    password_hash = body.get("passwordHash") or ""
    key = userid.lower()
    # Two buckets (S0-7, finding H3). The narrow (IP, userid) one is what stops a caller
    # locking OTHER people out — the single IP-keyed bucket this replaces was, as deployed,
    # one bucket for the entire user base. The loose per-IP backstop is what still blunts an
    # address rotating userids, which the narrow key alone would allow forever.
    ip = client_ip(request)
    if not login_limiter.allow(f"{ip}|{key}") or not login_ip_limiter.allow(ip):
        return json_error(429, "Too many sign-in attempts. Please wait a few minutes "
                               "and try again.")
    # A malformed passwordHash cannot match any stored value, so answer the ordinary
    # failure without a Supabase read (S1-11). LOGIN_FAILED, not a distinct 400: telling a
    # caller their input was the wrong SHAPE is one bit more than they need, and this path
    # is reachable by anyone.
    if not is_valid_client_hash(password_hash):
        return json_error(401, LOGIN_FAILED)
    try:
        record = get_user_account(key)
    except Exception as e:
        return opaque_error(502, DB_UNAVAILABLE, e, op="account.db")
    # ONE message for both failures (S1-7, finding M7). This used to answer
    # 404 "No account found with that user ID." here and 401 "Incorrect password." below,
    # which enumerates valid userids for free — and the two are told apart by the STATUS as
    # much as the text, so both have to converge, not just the wording.
    if not record:
        return json_error(401, LOGIN_FAILED)

    # Verify against the stored hash. verify_password handles both argon2 rows and legacy
    # bare-SHA-256 rows; a legacy row that matches is transparently upgraded to argon2 here,
    # so accounts migrate one login at a time with no lockout and no client change.
    ok, needs_upgrade = verify_password(record.get("password_hash"), password_hash)
    if not ok:
        return json_error(401, LOGIN_FAILED)
    if needs_upgrade:
        try:
            update_password_hash(key, hash_password(password_hash))
        except Exception as e:
            # Non-fatal: the login still succeeds on the legacy hash; we just retry the
            # upgrade next time rather than failing a valid sign-in over it.
            print(f"[WARN] Could not upgrade password hash for user "
                  f"{pseudonym(key)}: {e}")

    record = ensure_trial_started(key, record)
    touch_user_activity(key, "login")
    try:
        return json_response(200, login_response(record))
    except AuthConfigError as e:
        # Not str(e): that message names JWT_SECRET and where to set it, which is
        # operational detail a signed-out caller has no business reading (S1-13, L5).
        return opaque_error(503, "Sign-in is temporarily unavailable. Please try again "
                                 "shortly.", e, op="auth.config")

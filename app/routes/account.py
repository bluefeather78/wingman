"""Account routes: register and login. Translated from server.py's handle_register /
handle_login (PLAN_1_decompose.md). Paths and JSON shapes unchanged.
"""
import json
import urllib.error

from fastapi import APIRouter, Request

from app.config import EMAIL_RE
from app.core import (
    get_user, get_user_by_email, create_user, MissingUserColumns, DuplicateEmail,
    _check_signup_consent, ensure_trial_started, touch_user_activity,
    update_password_hash,
)
from app.deps import read_json_body, json_response, json_error, client_ip, login_response
from app.auth import hash_password, verify_password, AuthConfigError
from app.auth.ratelimit import login_limiter, register_limiter

router = APIRouter()


@router.post("/api/register")
async def handle_register(request: Request):
    if not register_limiter.allow(client_ip(request)):
        return json_error(429, "Too many sign-up attempts. Please wait a few minutes "
                               "and try again.")
    body = await read_json_body(request)
    first_name = (body.get("firstName") or "").strip()
    last_name = (body.get("lastName") or "").strip()
    email = (body.get("email") or "").strip()
    userid = (body.get("userid") or "").strip()
    password_hash = body.get("passwordHash") or ""
    location = (body.get("location") or "").strip()
    if not all([first_name, last_name, email, userid, password_hash, location]):
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

    key = userid.lower()
    try:
        if get_user(key):
            return json_error(409, "That user ID is already taken.")
        existing = get_user_by_email(email)
    except Exception as e:
        return json_error(502, f"Could not reach Supabase: {e}")
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
                               "consent columns. Run subscription_schema.sql "
                               "in the Supabase SQL editor, then try again.")
    except DuplicateEmail:
        return json_error(409, "An account already exists with that email "
                               "address. Sign in instead, or use a different "
                               "email.")
    except urllib.error.HTTPError as e:
        if e.code == 409:
            return json_error(409, "That user ID is already taken.")
        return json_error(502, f"Could not reach Supabase: {e}")
    except Exception as e:
        return json_error(502, f"Could not reach Supabase: {e}")

    # Auto-login the new account: the client goes straight to showApp() after register, so
    # it needs a token immediately or every gated call would 401. Fetch the fresh row and
    # return the same signed-in payload login does (identity + access/refresh tokens).
    record = get_user(key)
    try:
        return json_response(200, login_response(record))
    except AuthConfigError as e:
        return json_error(503, str(e))


@router.post("/api/login")
async def handle_login(request: Request):
    if not login_limiter.allow(client_ip(request)):
        return json_error(429, "Too many sign-in attempts. Please wait a few minutes "
                               "and try again.")
    body = await read_json_body(request)
    userid = (body.get("userid") or "").strip()
    password_hash = body.get("passwordHash") or ""
    key = userid.lower()
    try:
        record = get_user(key)
    except Exception as e:
        return json_error(502, f"Could not reach Supabase: {e}")
    if not record:
        return json_error(404, "No account found with that user ID.")

    # Verify against the stored hash. verify_password handles both argon2 rows and legacy
    # bare-SHA-256 rows; a legacy row that matches is transparently upgraded to argon2 here,
    # so accounts migrate one login at a time with no lockout and no client change.
    ok, needs_upgrade = verify_password(record.get("password_hash"), password_hash)
    if not ok:
        return json_error(401, "Incorrect password.")
    if needs_upgrade:
        try:
            update_password_hash(key, hash_password(password_hash))
        except Exception as e:
            # Non-fatal: the login still succeeds on the legacy hash; we just retry the
            # upgrade next time rather than failing a valid sign-in over it.
            print(f"[WARN] Could not upgrade password hash for {key}: {e}")

    record = ensure_trial_started(key, record)
    touch_user_activity(key, "login")
    try:
        return json_response(200, login_response(record))
    except AuthConfigError as e:
        return json_error(503, str(e))

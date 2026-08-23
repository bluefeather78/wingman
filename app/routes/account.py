"""Account routes: register and login. Translated from server.py's handle_register /
handle_login (PLAN_1_decompose.md). Paths and JSON shapes unchanged.
"""
import json
import urllib.error

from fastapi import APIRouter, Request

from app.config import EMAIL_RE
from app.core import (
    get_user, get_user_by_email, create_user, MissingUserColumns, DuplicateEmail,
    _check_signup_consent, ensure_trial_started, touch_user_activity, _login_payload,
)
from app.deps import read_json_body, json_response, json_error

router = APIRouter()


@router.post("/api/register")
async def handle_register(request: Request):
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

    try:
        create_user(key, first_name, last_name, email, password_hash, location,
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
    return json_response(200, {"ok": True})


@router.post("/api/login")
async def handle_login(request: Request):
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
    if record.get("password_hash") != password_hash:
        return json_error(401, "Incorrect password.")
    record = ensure_trial_started(key, record)
    touch_user_activity(key, "login")
    return json_response(200, _login_payload(record))

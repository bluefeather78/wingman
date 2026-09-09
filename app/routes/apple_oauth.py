"""Sign in with Apple — native iOS flow (App Store Guideline 4.8).

Login is Google-only everywhere else, and Apple requires Sign in with Apple whenever an app
offers only a third-party social login. The native flow (expo-apple-authentication) returns a
signed identity-token JWT ON DEVICE, so unlike Google's browser redirect there is no code
exchange and no one-time-nonce handoff (S0-9): the app POSTs the identity token to
POST /api/auth/apple/native and we verify it here, then return the same bearer session every
other login path returns (deps.login_response).

Account resolution mirrors app/routes/google_oauth.py exactly:
    find by apple_id  ->  link by *verified* email  ->  else create  ->  login_response.

Signup consent: a brand-new account still needs the same consent handle_google_finish
collects. Rather than a second endpoint, this one is idempotent on the identity token — called
for an unknown Apple account WITHOUT consent it returns {pending: true, ...} so the app can
show the consent screen, then the app re-POSTs the SAME identity token together with the
consent booleans and we create the account. The token is short-lived and re-verified on every
call, so the second POST is not trusted any more cheaply than the first.
"""
import threading
import urllib.parse

import jwt
from jwt import PyJWKClient, PyJWKClientError
from jwt.exceptions import PyJWKClientConnectionError

from fastapi import APIRouter, Depends

from app.config import APPLE_CLIENT_ID, APPLE_ISSUER, APPLE_JWKS_URL
from app.core import (
    get_user, get_user_account, get_user_by_email, get_user_by_apple_id,
    create_user, _check_signup_consent, _unique_userid_from_email, _users_request,
    MissingUserColumns, DuplicateEmail,
)
from app.services.email import send_lifecycle_email_async
from app.deps import (json_body, json_response, json_error, login_response,
                      opaque_error, DB_UNAVAILABLE)
from app.auth.tokens import AuthConfigError

router = APIRouter()


# One JWKS client for the process. PyJWKClient caches Apple's signing keys (they rotate rarely)
# and selects the right one by the token's `kid`, refetching when a token presents an unknown
# kid. Built lazily under a lock so a construction race can't create several.
_jwks_client = None
_jwks_lock = threading.Lock()


def _apple_jwks_client():
    global _jwks_client
    if _jwks_client is None:
        with _jwks_lock:
            if _jwks_client is None:
                _jwks_client = PyJWKClient(APPLE_JWKS_URL, cache_keys=True)
    return _jwks_client


class _AppleTokenError(Exception):
    """The identity token is missing, expired, forged, or for another audience — a 401."""


class _AppleKeysUnavailable(Exception):
    """Apple's JWKS endpoint could not be reached — transient, so a 503, not a 401."""


def _verify_identity_token(identity_token):
    """Verify an Apple identity-token JWT and return its claims dict.

    RS256, so PyJWT needs `cryptography` (see requirements.txt). jwt.decode enforces the
    signature, `aud` (our bundle id), `iss` (appleid.apple.com) and expiry; `require` makes a
    token missing any of those claims a failure rather than a silent pass.
    """
    if not identity_token:
        raise _AppleTokenError("Missing identity token.")
    try:
        signing_key = _apple_jwks_client().get_signing_key_from_jwt(identity_token)
    except PyJWKClientConnectionError as e:
        # Could not fetch Apple's keys — nothing about the token is wrong that we know of.
        raise _AppleKeysUnavailable(str(e)) from e
    except PyJWKClientError as e:
        # Keys fetched, but none matches this token's kid (or the JWKS was unparseable): a key
        # we cannot resolve is a token we cannot trust.
        raise _AppleTokenError(f"Unresolvable signing key: {e}") from e
    try:
        return jwt.decode(
            identity_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=APPLE_CLIENT_ID,
            issuer=APPLE_ISSUER,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as e:
        raise _AppleTokenError(str(e)) from e


def _session(userid):
    """login_response for an existing account, as an HTTP response (mirrors google session)."""
    account = get_user_account(userid)
    if not account:
        return json_error(404, "No account found.")
    try:
        return json_response(200, login_response(account))
    except AuthConfigError as e:
        # Not str(e): that message names JWT_SECRET and where to set it — operational detail a
        # signed-out caller has no business reading (S1-13, L5).
        return opaque_error(503, "Sign-in is temporarily unavailable. Please try again "
                                 "shortly.", e, op="auth.config")


@router.post("/api/auth/apple/native")
def handle_apple_native(body: dict = Depends(json_body)):
    identity_token = (body.get("identity_token") or body.get("identityToken") or "").strip()
    try:
        claims = _verify_identity_token(identity_token)
    except _AppleKeysUnavailable as e:
        return opaque_error(503, "Sign in with Apple is temporarily unavailable. Please try "
                                 "again shortly.", e, op="apple.jwks")
    except _AppleTokenError as e:
        print(f"[WARN] Apple identity token rejected: {e}")
        return json_error(401, "We could not verify your Apple sign-in. Please try again.")

    apple_sub = claims.get("sub") or ""
    if not apple_sub:
        return json_error(401, "Apple did not return a usable identity. Please try again.")
    email = (claims.get("email") or "").strip()
    # email_verified arrives as a bool or the string "true" — parity with the Google H1 gate
    # (a strict `is True` would refuse a token that spelled it "true"). Apple's Hide-My-Email
    # relay addresses are verified, so they link and create exactly like a real address.
    _verified = claims.get("email_verified")
    email_verified = _verified is True or str(_verified).strip().lower() == "true"
    # Apple sends the name only on the FIRST authorization, and never inside the token — the
    # app forwards it from the credential when present, so it is optional here.
    first_name = (body.get("first_name") or body.get("firstName") or "").strip()
    last_name = (body.get("last_name") or body.get("lastName") or "").strip()

    # Resolve the account: linked Apple id first, then link to a match on a VERIFIED email
    # (never on an unverified one — S0-8/H1: an unverified address is an unproven claim).
    try:
        record = get_user_by_apple_id(apple_sub)
        if not record and email and email_verified:
            by_email = get_user_by_email(email)
            if by_email:
                query_patch = "?" + urllib.parse.urlencode({"userid": f"eq.{by_email['userid']}"})
                _users_request("PATCH", query_patch, data={"apple_id": apple_sub})
                record = by_email
    except Exception as e:
        return opaque_error(502, DB_UNAVAILABLE, e, op="apple.db")

    # Known (or just-linked) account -> straight to a session.
    if record:
        try:
            return _session(record["userid"])
        except Exception as e:
            return opaque_error(502, DB_UNAVAILABLE, e, op="apple.db")

    # New Apple account. Signup consent is required, exactly as handle_google_finish enforces.
    # If the app has not collected it yet, tell it this is a signup so it can show the consent
    # screen and re-POST the same identity token with the consent booleans.
    has_consent_fields = any(k in body for k in ("isAdult", "acceptedTerms", "parentalConsent"))
    if not has_consent_fields:
        return json_response(200, {
            "ok": True,
            "pending": True,
            "email": email,
            "firstName": first_name,
            "lastName": last_name,
        })

    # Consent present -> create. Without a verified email there is no address to key the
    # account on, so we cannot proceed (the app should re-run sign-in and share the email).
    if not email or not email_verified:
        return json_error(400, "Apple did not share a verified email, so we can't create your "
                               "account. Sign in with Apple again and choose to share your "
                               "email.")
    is_adult = bool(body.get("isAdult"))
    parental_consent = bool(body.get("parentalConsent"))
    accepted_terms = bool(body.get("acceptedTerms"))
    consent_error = _check_signup_consent(is_adult, parental_consent, accepted_terms)
    if consent_error:
        return json_error(400, consent_error)

    # Guard the race/replay: a double-submit or a second device may have created the account
    # between the pending response above and now. If it exists, log in rather than error.
    try:
        existing = get_user_by_apple_id(apple_sub)
        if not existing:
            by_email = get_user_by_email(email)
            if by_email:
                query_patch = "?" + urllib.parse.urlencode({"userid": f"eq.{by_email['userid']}"})
                _users_request("PATCH", query_patch, data={"apple_id": apple_sub})
                existing = by_email
        if existing:
            return _session(existing["userid"])
    except Exception as e:
        return opaque_error(502, DB_UNAVAILABLE, e, op="apple.db")

    userid = _unique_userid_from_email(email)
    try:
        create_user(userid, first_name, last_name, email, None, is_adult=is_adult,
                    parental_consent=parental_consent, apple_id=apple_sub)
    except MissingUserColumns:
        return json_error(503, "Accounts are temporarily unavailable: the database is missing "
                               "required columns. Run db/subscription_schema.sql and "
                               "db/apple_auth_schema.sql in the Supabase SQL editor, then try "
                               "again.")
    except DuplicateEmail:
        # Raced: created elsewhere with this email between the check above and the insert. Log
        # in rather than dead-ending the student on their very first sign-in.
        try:
            by_email = get_user_by_email(email)
            if by_email:
                return _session(by_email["userid"])
        except Exception as e:
            return opaque_error(502, DB_UNAVAILABLE, e, op="apple.db")
        return json_error(409, "An account already exists with that email address. Please "
                               "sign in instead.")
    except Exception as e:
        return opaque_error(502, DB_UNAVAILABLE, e, op="apple.db")

    record = get_user(userid)
    # An Apple signup is still a signup — same welcome email as the form and Google paths. The
    # email_sends claim inside send_lifecycle_email_async means this can't double up.
    send_lifecycle_email_async(userid, "welcome", record=record)
    try:
        return json_response(200, login_response(record))
    except AuthConfigError as e:
        return opaque_error(503, "Sign-in is temporarily unavailable. Please try again "
                                 "shortly.", e, op="auth.config")

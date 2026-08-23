"""Mint and verify the access + refresh JWTs (PLAN_2_auth.md).

Two token types, both HS256-signed with JWT_SECRET:

  access  (short, ACCESS_TOKEN_TTL_SECONDS)  — verified statelessly on every gated
          request. No database read: that is the point of a JWT, and it is safe because
          the token is short-lived.
  refresh (long, REFRESH_TOKEN_TTL_SECONDS)  — presented only to /api/auth/refresh, where
          the caller's `ver` claim is checked against the account's current
          users.token_version. Bumping that column rejects every outstanding refresh
          token, so revocation ("log out everywhere", account kill) takes effect within
          one access-token lifetime without a per-request DB hit.

Fail closed: if JWT_SECRET is unset we raise AuthConfigError rather than signing with a
guessable key. Routes translate that into a 503.
"""
import datetime

import jwt

from app.config import (
    JWT_SECRET, JWT_ALGORITHM,
    ACCESS_TOKEN_TTL_SECONDS, REFRESH_TOKEN_TTL_SECONDS,
)


class AuthError(Exception):
    """A token was missing, malformed, expired, or of the wrong type — a 401."""


class AuthConfigError(Exception):
    """JWT_SECRET is not configured — the server can't sign or verify, a 503.

    Kept distinct from AuthError so a deployment misconfiguration surfaces as "temporarily
    unavailable" rather than as "your login is invalid", which would send every user to the
    sign-in screen over a server-side problem.
    """


def _require_secret():
    if not JWT_SECRET:
        raise AuthConfigError(
            "JWT_SECRET is not set. Auth is disabled until it is configured in the "
            "environment (.env locally, a Render env var in production).")


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _encode(claims):
    _require_secret()
    return jwt.encode(claims, JWT_SECRET, algorithm=JWT_ALGORITHM)


def issue_tokens(userid, token_version=0):
    """Mint a fresh access+refresh pair for a verified identity.

    Called from the one login-response builder (app.deps.login_response), so password and
    Google logins converge on the same tokens. `token_version` is stamped into both so a
    later bump can invalidate the refresh token; the access token carries it only for
    symmetry (it is never re-checked against the DB — it just expires).
    """
    userid = str(userid).strip().lower()
    ver = int(token_version or 0)
    now = _now()
    access = _encode({
        "sub": userid, "type": "access", "ver": ver,
        "iat": now, "exp": now + datetime.timedelta(seconds=ACCESS_TOKEN_TTL_SECONDS),
    })
    refresh = _encode({
        "sub": userid, "type": "refresh", "ver": ver,
        "iat": now, "exp": now + datetime.timedelta(seconds=REFRESH_TOKEN_TTL_SECONDS),
    })
    return {
        "token": access,
        "refresh_token": refresh,
        "token_type": "Bearer",
        "expires_in": ACCESS_TOKEN_TTL_SECONDS,
    }


def _decode(token, expected_type):
    _require_secret()
    if not token:
        raise AuthError("Missing token.")
    try:
        claims = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise AuthError("Token has expired.")
    except jwt.InvalidTokenError as e:
        raise AuthError(f"Invalid token: {e}")
    if claims.get("type") != expected_type:
        # An access token presented where a refresh is required (or vice versa) must be
        # rejected — otherwise a long-lived refresh token would work as an access token and
        # defeat the short access lifetime the whole revocation story depends on.
        raise AuthError("Wrong token type.")
    sub = (claims.get("sub") or "").strip().lower()
    if not sub:
        raise AuthError("Token carries no subject.")
    return claims


def verify_access_token(token):
    """Return the userid for a valid access token, or raise AuthError. No DB access."""
    return _decode(token, "access")["sub"]


def verify_refresh_token(token):
    """Return (userid, ver) for a valid refresh token, or raise AuthError.

    The caller (the /api/auth/refresh route) is responsible for comparing `ver` against the
    account's current token_version — that DB check is what makes revocation real, and it
    lives at the route, not here, so this module stays free of app.core.
    """
    claims = _decode(token, "refresh")
    return claims["sub"], int(claims.get("ver") or 0)

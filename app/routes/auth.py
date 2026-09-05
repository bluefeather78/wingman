"""Session-token routes (docs/archive/PLAN_2_auth.md): refresh and revoke-all.

  POST /api/auth/refresh     {refresh_token} -> a fresh access+refresh pair (+ identity).
                             This is the ONLY place users.token_version is checked, so it
                             is where revocation actually bites: a token whose `ver` no
                             longer matches the account is refused and the user must sign in
                             again.
  POST /api/auth/logout-all  (bearer) -> bump users.token_version, invalidating every
                             outstanding refresh token for the account. "Log out everywhere"
                             / account-kill. Access tokens already out there still work until
                             they expire (<= one access-token lifetime), by design.

Login/register live in account.py; these two are the token lifecycle around them.
"""
from fastapi import APIRouter, Depends

from app.core import get_user_account, bump_token_version
from app.deps import json_body, json_response, json_error, login_response
from app.auth import (
    get_current_user, AuthedUser, verify_refresh_token, AuthError, AuthConfigError,
)

router = APIRouter()


@router.post("/api/auth/refresh")
def handle_refresh(body: dict = Depends(json_body)):
    token = body.get("refresh_token") or ""
    try:
        userid, ver = verify_refresh_token(token)
    except AuthConfigError:
        return json_error(503, "Authentication is temporarily unavailable.")
    except AuthError:
        return json_error(401, "Your session has expired. Please sign in again.")

    try:
        record = get_user_account(userid)
    except Exception as e:
        return json_error(502, f"Could not reach Supabase: {e}")
    if not record:
        return json_error(401, "Your session has expired. Please sign in again.")

    # The revocation check: a refresh token minted before a token_version bump no longer
    # matches, so it cannot be renewed. Missing column ⇒ both sides are 0 and this passes,
    # which is the intended pre-migration behaviour (revocation simply inert until then).
    current_version = int(record.get("token_version") or 0)
    if ver != current_version:
        return json_error(401, "Your session has ended. Please sign in again.")

    try:
        return json_response(200, login_response(record))
    except AuthConfigError as e:
        return json_error(503, str(e))


@router.post("/api/auth/logout-all")
def handle_logout_all(user: AuthedUser = Depends(get_current_user)):
    new_version = bump_token_version(user.id)
    if new_version is None:
        # Column not present (db/auth_schema.sql not run) or the account vanished — nothing to
        # revoke. Report it honestly rather than claiming sessions were ended.
        return json_response(200, {"ok": True, "revoked": False})
    return json_response(200, {"ok": True, "revoked": True})

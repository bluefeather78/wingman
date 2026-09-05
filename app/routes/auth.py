"""Session-token routes (docs/archive/PLAN_2_auth.md): refresh and revoke-all.

  POST /api/auth/refresh     {refresh_token} -> a fresh access+refresh pair (+ identity).
                             This is the ONLY place users.token_version is checked, so it
                             is where revocation actually bites: a token whose `ver` no
                             longer matches the account is refused and the user must sign in
                             again. As of S1-2 it also ROTATES: the presented token's jti is
                             replaced, so that token dies the instant it is used.
  POST /api/auth/logout      {refresh_token} -> drop THIS device's lineage. The client's
                             logout() used to be purely local, so the token it "forgot" kept
                             working for the rest of its 30 days.
  POST /api/auth/logout-all  (bearer) -> bump users.token_version, invalidating every
                             outstanding refresh token for the account. "Log out everywhere"
                             / account-kill. Access tokens already out there still work until
                             they expire (<= one access-token lifetime), by design.

Login/register live in account.py; these three are the token lifecycle around them.
"""
from fastapi import APIRouter, Depends

from app.core import (get_user_account, bump_token_version, forget_refresh_jti,
                      refresh_grace_successor, select_user, pseudonym)
from app.deps import (json_body, json_response, json_error, login_response,
                      opaque_error, DB_UNAVAILABLE)
from app.auth import (
    get_current_user, AuthedUser, verify_refresh_token, AuthError, AuthConfigError,
)

router = APIRouter()


SESSION_ENDED = "Your session has ended. Please sign in again."
SESSION_EXPIRED = "Your session has expired. Please sign in again."


@router.post("/api/auth/refresh")
def handle_refresh(body: dict = Depends(json_body)):
    token = body.get("refresh_token") or ""
    try:
        userid, ver, jti = verify_refresh_token(token)
    except AuthConfigError:
        return json_error(503, "Authentication is temporarily unavailable.")
    except AuthError:
        return json_error(401, SESSION_EXPIRED)

    try:
        record = get_user_account(userid)
    except Exception as e:
        return opaque_error(502, DB_UNAVAILABLE, e, op="auth.db")
    if not record:
        return json_error(401, SESSION_EXPIRED)

    # The revocation check: a refresh token minted before a token_version bump no longer
    # matches, so it cannot be renewed. Missing column ⇒ both sides are 0 and this passes,
    # which is the intended pre-migration behaviour (revocation simply inert until then).
    current_version = int(record.get("token_version") or 0)
    if ver != current_version:
        return json_error(401, SESSION_ENDED)

    # ---- Rotation + reuse detection (S1-2, finding M2) ----
    #
    # Before this, /api/auth/refresh returned a NEW pair and left the presented token valid
    # until its own 30-day exp — so a token lifted off a shared school computer, out of a
    # proxy log, or from a compromised device kept minting access tokens for a month,
    # INCLUDING after the student pressed "log out", with nothing signalling that two
    # parties were refreshing the same lineage.
    replaces = jti
    if jti is not None:
        known = _live_jtis(userid)
        if known is None:
            # db/auth_schema.sql's refresh_jtis column is not there. Rotation is off and
            # this behaves exactly as it did before, rather than refusing every token.
            replaces = None
        elif jti not in known:
            successor = refresh_grace_successor(jti)
            if successor is not None:
                # We rotated this jti away moments ago. The realistic cause is a dropped
                # response on a bad connection, not a thief — the client is retrying the
                # very same call. Hand back the lineage it should already have had.
                replaces = successor
            elif known:
                # A SUPERSEDED jti on an account that has live lineages. Two parties hold
                # the same token and there is no way to tell which one is asking, so end
                # every session on the account. One forced sign-in beats a live intruder.
                print(f"[SECURITY] Refresh-token reuse detected for user "
                      f"{pseudonym(userid)}; revoking all sessions.")
                try:
                    bump_token_version(userid)
                    forget_refresh_jti(userid, jti)
                except Exception as e:                            # noqa: BLE001
                    print(f"[WARN] Could not revoke after reuse: {type(e).__name__}")
                return json_error(401, SESSION_ENDED)
            else:
                # No lineages recorded at all: this account has not refreshed since the
                # migration ran. Adopt it rather than accuse it.
                replaces = None

    try:
        return json_response(200, login_response(record, replaces_jti=replaces))
    except AuthConfigError as e:
        # Not str(e): that message names JWT_SECRET and where to set it, which is
        # operational detail a signed-out caller has no business reading (S1-13, L5).
        return opaque_error(503, "Sign-in is temporarily unavailable. Please try again "
                                 "shortly.", e, op="auth.config")


def _live_jtis(userid):
    """The account's currently-valid refresh jtis, or None if the column is not migrated in.

    None and [] mean different things here and the distinction is the whole degrade story:
    None is "rotation is off", [] is "rotation is on and this account has no live lineage".
    """
    try:
        record = select_user(userid, "userid,refresh_jtis")
    except Exception:                                             # noqa: BLE001
        return None
    if not record or "refresh_jtis" not in record:
        return None
    return [j for j in (record.get("refresh_jtis") or []) if j]


@router.post("/api/auth/logout")
def handle_logout(body: dict = Depends(json_body)):
    """End THIS device's session server-side (S1-2, finding M2).

    The client's logout() only called forgetSession() locally, so the token it "forgot"
    kept minting access tokens for the rest of its 30 days — which is precisely the shared
    school computer case.

    Deliberately always answers 200. A logout that reports failure is a logout the student
    will assume did not happen, and the local session is gone either way; the honest signal
    is `revoked`, which says whether the server-side lineage was actually dropped.
    """
    token = body.get("refresh_token") or ""
    try:
        userid, _ver, jti = verify_refresh_token(token)
    except (AuthError, AuthConfigError):
        # An expired or unreadable token is already unusable. Nothing to revoke, and this
        # must not be a way to probe which tokens are valid.
        return json_response(200, {"ok": True, "revoked": False})
    try:
        revoked = bool(forget_refresh_jti(userid, jti))
    except Exception as e:                                        # noqa: BLE001
        print(f"[WARN] Could not drop refresh lineage: {type(e).__name__}")
        revoked = False
    return json_response(200, {"ok": True, "revoked": revoked})


@router.post("/api/auth/logout-all")
def handle_logout_all(user: AuthedUser = Depends(get_current_user)):
    new_version = bump_token_version(user.id)
    if new_version is None:
        # Column not present (db/auth_schema.sql not run) or the account vanished — nothing to
        # revoke. Report it honestly rather than claiming sessions were ended.
        return json_response(200, {"ok": True, "revoked": False})
    return json_response(200, {"ok": True, "revoked": True})

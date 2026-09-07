"""Self-serve data rights: export (and, later, delete) everything about ONE account.

DATA_DELETION_EXPORT_PLAN.md. P0 ships the export half; deletion (P2) lands here too.

Both endpoints hang off get_current_user, NOT require_subscription: a lapsed account must
still be able to take a copy of, and delete, its own data — a paywall you can't export or
delete through is a data-hostage situation, and the app stores reject it. This is the same
reasoning that keeps /api/subscription/* ungated (see test_subscription_gate.py's UNGATED).

Identity is ALWAYS user.id from the verified token. There is no userid parameter anywhere —
that is the whole "can't touch another user's data" guarantee (DATA_DELETION_EXPORT_PLAN.md
§1); an attacker has nothing to point at someone else with.
"""
import datetime
import json

from fastapi import APIRouter, Depends, Response

from app.core import touch_user_activity, get_user_account, pseudonym
from app.deps import (json_body, json_response, json_error, opaque_error, DB_UNAVAILABLE)
from app.auth import get_current_user, AuthedUser, verify_password
from app.auth.passwords import is_valid_client_hash
from app.auth.ratelimit import account_export_limiter, account_delete_limiter
from app.services.account_data import assemble_export, erase_account, StripeCancelError
from app.services.google_oauth import take_delete_reauth_proof

router = APIRouter()


# Plain `def`, not `async def`: this does blocking Supabase IO (get_user + the satellite
# reads), which FastAPI runs in a threadpool for a sync handler but ON the event loop for an
# async one — the app-open-latency trap (docs/CLAUDE-app.md). It awaits nothing in its body.
@router.post("/api/account/export")
def handle_account_export(user: AuthedUser = Depends(get_current_user)):
    """Download everything Wingman holds about the signed-in account, as one JSON file.

    404 if the account no longer exists (a token outliving its row); 429 if this account has
    exported too many times this hour. The body is delivered as an attachment so the browser
    saves it rather than rendering it.
    """
    userid = user.id
    if not account_export_limiter.allow(userid):
        resp = json_error(429, "You've requested your data a few times just now. Please "
                               "wait a little while and try again.")
        resp.headers["Retry-After"] = str(account_export_limiter.retry_after(userid))
        return resp
    touch_user_activity(userid, "account_export")
    try:
        export = assemble_export(userid)
    except Exception as e:
        return opaque_error(502, DB_UNAVAILABLE, e, op="account.export")
    if export is None:
        return json_error(404, "No account found.")
    # default=str so any stray datetime (rather than an ISO string from PostgREST) still
    # serializes instead of 500-ing the download.
    body = json.dumps(export, default=str, indent=2).encode("utf-8")
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d")
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition":
                 f'attachment; filename="wingman-my-data-{stamp}.json"'},
    )


# Immediate hard delete, guarded by a PASSWORD re-auth (DECISION #2): a valid session is not
# enough — the caller must re-prove the password, so a borrowed/stolen session cannot nuke an
# account. Plain `def` (blocking Supabase/Stripe/Google IO), get_current_user only (NOT
# require_subscription — a lapsed account must be able to delete its data, and the stores
# require it). No userid parameter: identity is the token's, exactly as for export.
@router.post("/api/account/delete")
def handle_account_delete(body: dict = Depends(json_body),
                          user: AuthedUser = Depends(get_current_user)):
    """Permanently erase the signed-in account and everything Wingman holds about it.

    Re-auth is required and comes one of two ways:
      * `{passwordHash}` — the same client-side SHA-256 login sends, re-verified server-side.
      * `{reauthToken}` — a single-use proof that the account just re-authenticated via Google
        (for Google-only accounts with no password). Minted only after the Google callback
        confirmed the SAME Google account, and bound to this userid.

    403 on a wrong password or an invalid/mismatched re-auth token; 400 `reauth=google_required`
    for a Google-only account that sent neither; 502 (nothing deleted) if a live subscription
    cannot be cancelled.
    """
    userid = user.id
    if not account_delete_limiter.allow(userid):
        resp = json_error(429, "Too many attempts. Please wait a few minutes and try again.")
        resp.headers["Retry-After"] = str(account_delete_limiter.retry_after(userid))
        return resp

    reauth_token = body.get("reauthToken")
    if reauth_token:
        # Google-only path: the proof is single-use, minted by the Google callback only after
        # it verified the SAME google_id, and bound to a userid. It must be THIS user's — a
        # proof minted for someone else is refused even with a valid session.
        proof_userid = take_delete_reauth_proof(reauth_token)
        if not proof_userid or proof_userid != userid:
            return json_error(403, "We could not confirm that with Google. Please try again.")
        # Verified via Google — fall through to the erase, no password needed.
    else:
        # Password path. Re-auth against the STORED hash; get_user_account carries password_hash.
        try:
            record = get_user_account(userid)
        except Exception as e:
            return opaque_error(502, DB_UNAVAILABLE, e, op="account.delete.lookup")
        if not record:
            return json_error(404, "No account found.")
        stored_hash = record.get("password_hash")
        if not stored_hash:
            # Google-only account and no re-auth token: tell the client to run the Google
            # confirmation flow (POST /api/account/reauth/google/start). Refuse rather than
            # weaken the guard to "a valid session is enough".
            return json_response(400, {
                "error": "This account signs in with Google. Please confirm with Google to "
                         "delete it.",
                "reauth": "google_required"})
        # A failed re-auth answers 403, NOT 401. 401 is reserved for "not authenticated" (an
        # expired/absent access token, raised by get_current_user) — and the client refreshes
        # and retries on a 401. A wrong password returned as 401 would send the client into a
        # refresh-and-retry loop and then log the student out over a typo; 403 ("authenticated,
        # but this action is refused") is the honest, non-looping answer.
        password_hash = body.get("passwordHash") or ""
        if not is_valid_client_hash(password_hash):
            return json_error(403, "Incorrect password.")
        ok, _needs_upgrade = verify_password(stored_hash, password_hash)
        if not ok:
            return json_error(403, "Incorrect password.")

    # Re-auth passed — erase. StripeCancelError means a live subscription could not be
    # cancelled, so NOTHING was deleted and the student can retry with their data intact.
    try:
        report = erase_account(userid)
    except StripeCancelError as e:
        return opaque_error(
            502, "We couldn't cancel your subscription, so nothing was deleted. Please try "
                 "again in a moment.", e, op="account.delete.stripe")
    except Exception as e:
        return opaque_error(502, DB_UNAVAILABLE, e, op="account.delete")
    if report is None:
        return json_error(404, "No account found.")
    print(f"[delete] account {pseudonym(userid)} deleted via self-serve")
    return json_response(200, {"ok": True, "deleted": True})

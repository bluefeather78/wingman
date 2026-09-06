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

from app.core import touch_user_activity
from app.deps import json_error, opaque_error, DB_UNAVAILABLE
from app.auth import get_current_user, AuthedUser
from app.auth.ratelimit import account_export_limiter
from app.services.account_data import assemble_export

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

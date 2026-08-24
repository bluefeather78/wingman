"""Lifecycle-email routes on the SHIPPED service: the daily sweep trigger and the
unsubscribe link.

Both live here rather than in ops/ for the same reason, and it is the reason this feature
is shaped the way it is: ops/ is localhost-gated and deliberately never mounted on Render,
so nothing in the admin console can be what actually sends the trial reminder — it would
only fire on days somebody's laptop happened to be on. The console owns review, preview and
the send log; these two endpoints own the two things that must work in production with
nobody watching.

  POST /api/email/sweep        (X-Cron-Secret) -> send today's trial-ending reminders
  GET  /api/email/unsubscribe  (?u=&t=)        -> opt this account out of lifecycle email

Neither is authenticated by a session token, and neither could be: the sweep is called by a
scheduler with no user, and the unsubscribe link is opened from a mailbox by somebody who
may well be signed out or on another device. They carry their own credentials instead — a
shared secret and a per-user HMAC.
"""
import hmac

from fastapi import APIRouter, Request, Depends

from app.config import EMAIL_CRON_SECRET
from app.deps import json_body, json_response, json_error
from app.services import email as email_service

router = APIRouter()


@router.post("/api/email/sweep")
def handle_email_sweep(request: Request, body: dict = Depends(json_body)):
    """Send the trial-ending reminders that are due. Idempotent by construction — the
    email_sends claim is what makes a second call in the same day a no-op, so a scheduler
    that retries, or fires twice, cannot double-mail anybody.

    The secret goes in a HEADER, never a query string: a URL with the credential in it is
    recorded by every proxy and access log between the scheduler and here.
    """
    if not EMAIL_CRON_SECRET:
        # Fails CLOSED, like JWT_SECRET. An unset secret must not mean "no check" on an
        # internet-reachable endpoint that sends mail to real people.
        return json_error(503, "Email sweep is not configured: set EMAIL_CRON_SECRET.")

    supplied = request.headers.get("X-Cron-Secret") or ""
    if not hmac.compare_digest(supplied, EMAIL_CRON_SECRET):
        return json_error(403, "Forbidden.")

    result = email_service.run_trial_sweep(
        days=body.get("days"),
        dry_run=bool(body.get("dry_run")),
    )
    # The per-user detail list carries addresses, so it is dropped unless explicitly asked
    # for. A scheduler's run log is not a place a roster of minors' emails belongs.
    if not body.get("verbose"):
        result.pop("details", None)
    return json_response(200, result, default=str)


@router.get("/api/email/unsubscribe")
def handle_unsubscribe(request: Request):
    """One-click opt-out, honoured for all three lifecycle emails.

    Answers HTML rather than JSON — this is opened in a browser by a person, and a raw
    JSON blob reads as the link having failed, which is the one impression an unsubscribe
    link must never give.
    """
    userid = (request.query_params.get("u") or "").strip()
    token = (request.query_params.get("t") or "").strip()

    if not userid or not email_service.verify_unsubscribe_token(userid, token):
        return _page("That link isn't valid",
                     "It may have been truncated by your email client. Reply to any "
                     "Wingman email and we'll take you off the list by hand.",
                     status=400)

    if not email_service.set_optout(userid, True):
        # Never claim it worked when it didn't — an unsubscribe that silently fails is how
        # a sender ends up mailing somebody who explicitly asked to stop.
        return _page("We couldn't record that",
                     "Something went wrong on our end and you are still subscribed. "
                     "Reply to any Wingman email and we'll do it manually.",
                     status=502)

    return _page("You're unsubscribed",
                 "You won't get any more account emails from Highschool Wingman. Your "
                 "account and everything in your Quest Log are untouched.")


def _page(heading, message, status=200):
    from fastapi.responses import HTMLResponse
    from app.config import EMAIL_APP_URL
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{heading}</title></head>
<body style="margin:0;background:#FBF8F3;font-family:Helvetica,Arial,sans-serif">
<div style="max-width:480px;margin:12vh auto;padding:32px 28px;background:#fff;
     border:2px solid #1D4E89;border-radius:14px">
  <h1 style="margin:0 0 12px;font-size:22px;color:#1A2540">{heading}</h1>
  <p style="margin:0 0 20px;font-size:15px;line-height:24px;color:#4A6685">{message}</p>
  <a href="{EMAIL_APP_URL}" style="font-size:14px;color:#1D4E89">Back to Highschool Wingman</a>
</div></body></html>"""
    return HTMLResponse(content=html, status_code=status)

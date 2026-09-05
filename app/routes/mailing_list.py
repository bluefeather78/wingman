"""Mailing-list routes: button state, this user's past signups, and the subscribe
action. Translated from server.py's handle_mailing_list_* / handle_mailing_list_subscribe
(docs/archive/PLAN_1_decompose.md).
"""
from fastapi import APIRouter, Request, Depends

from app.core import touch_user_activity
from app.deps import (json_body, json_response,
                      require_subscription, optional_subscribed_user)
from app.auth import AuthedUser
from app.services.mailing_list import (
    get_signup_availability, list_user_subscriptions, subscribe_user_to_list,
)

router = APIRouter()


@router.get("/api/mailing-list/status")
def handle_mailing_list_status(request: Request, user: AuthedUser = Depends(optional_subscribed_user)):
    """GET /api/mailing-list/status?ids=a,b,c — button state for a screenful. Soft auth:
    the per-user "already submitted" flag needs identity, but a signed-out caller still
    gets generic availability, so this never blocks. Identity comes from the token, not a
    query userid. A caller who identifies as a LAPSED account is blocked (402) — there is
    no account for a signed-out caller to have lapsed, which is why this stays soft."""
    ids = [i for i in (request.query_params.get("ids", "") or "").split(",") if i.strip()]
    result = get_signup_availability(user.id if user else None, ids)
    return json_response(200, result, default=str)


@router.get("/api/mailing-list/subscriptions")
def handle_mailing_list_subscriptions(request: Request, user: AuthedUser = Depends(require_subscription)):
    """GET /api/mailing-list/subscriptions — what we sent on this user's behalf. This is
    the caller's own personal signup history, so it is token-gated and scoped to user.id."""
    result = list_user_subscriptions(user.id)
    return json_response(200, result, default=str)


@router.post("/api/opportunities/{opp_id}/subscribe")
def handle_mailing_list_subscribe(opp_id: str, body: dict = Depends(json_body),
                                  user: AuthedUser = Depends(require_subscription)):
    """POST /api/opportunities/<id>/subscribe — {email, consent}.

    Sends a student's name and address to a third party, so account standing, consent,
    and recipe verification are all re-checked server-side, not merely in the UI. Identity
    is token-derived — the signup goes out for the authenticated user, never a body userid.
    """
    userid = user.id
    touch_user_activity(userid, "mailing_list")
    status, payload = subscribe_user_to_list(
        userid, opp_id, body.get("email"), bool(body.get("consent")))
    return json_response(status, payload, default=str)

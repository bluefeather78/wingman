"""Mailing-list routes: button state, this user's past signups, and the subscribe
action. Translated from server.py's handle_mailing_list_* / handle_mailing_list_subscribe
(PLAN_1_decompose.md).
"""
from fastapi import APIRouter, Request

from app.core import touch_user_activity
from app.deps import read_json_body, json_response, json_error, subscription_block_reason
from app.services.mailing_list import (
    get_signup_availability, list_user_subscriptions, subscribe_user_to_list,
)

router = APIRouter()


@router.get("/api/mailing-list/status")
def handle_mailing_list_status(request: Request):
    """GET /api/mailing-list/status?userid=&ids=a,b,c — button state for a screenful.
    Not subscription-gated: this only decides which label a button shows and costs
    nothing. The gate belongs on the action."""
    ids = [i for i in (request.query_params.get("ids", "") or "").split(",") if i.strip()]
    result = get_signup_availability(request.query_params.get("userid"), ids)
    return json_response(200, result, default=str)


@router.get("/api/mailing-list/subscriptions")
def handle_mailing_list_subscriptions(request: Request):
    """GET /api/mailing-list/subscriptions?userid= — what we sent on this user's behalf."""
    result = list_user_subscriptions(request.query_params.get("userid"))
    return json_response(200, result, default=str)


@router.post("/api/opportunities/{opp_id}/subscribe")
async def handle_mailing_list_subscribe(opp_id: str, request: Request):
    """POST /api/opportunities/<id>/subscribe — {userid, email, consent}.

    Sends a student's name and address to a third party, so account standing, consent,
    and recipe verification are all re-checked server-side, not merely in the UI.
    """
    body = await read_json_body(request)
    userid = (body.get("userid") or "").strip()
    reason = subscription_block_reason(userid)
    if reason:
        return json_error(402, reason)
    touch_user_activity(userid, "mailing_list")
    status, payload = subscribe_user_to_list(
        userid, opp_id, body.get("email"), bool(body.get("consent")))
    return json_response(status, payload, default=str)

"""Subscription routes (dormant — Stripe unconfigured, trial gate left open per
docs/archive/PLAN_1_decompose.md). Translated from server.py's handle_subscription_* handlers.
"""
import datetime

from fastapi import APIRouter, Request, Depends

from app.core import (
    get_user_account, ensure_trial_started, subscription_state, touch_user_activity,
    update_subscription, redeem_promo_conditional,
)
from app.deps import (json_body, json_response, json_error,
                      opaque_error, DB_UNAVAILABLE)
from app.services.email import send_lifecycle_email_async
from app.auth import get_current_user, get_optional_user, AuthedUser
from wingman.subscription_common import (
    get_or_create_customer, create_checkout_session, cancel_subscription,
    validate_promo_code, promo_kind, extend_from, GRANTABLE_STATUSES,
)

router = APIRouter()


@router.post("/api/subscription/status")
def handle_subscription_status(user: AuthedUser = Depends(get_current_user)):
    userid = user.id
    try:
        record = get_user_account(userid)
    except Exception as e:
        return opaque_error(502, DB_UNAVAILABLE, e, op="subscription.db")
    if not record:
        return json_error(404, "User not found.")
    touch_user_activity(userid, "subscription_status")
    return json_response(200, subscription_state(ensure_trial_started(userid, record)))


@router.post("/api/subscription/checkout")
def handle_subscription_checkout(body: dict = Depends(json_body),
                                 user: AuthedUser = Depends(get_current_user)):
    userid = user.id
    email = (body.get("email") or "").strip()
    promo_code = (body.get("promo_code") or "").strip()
    success_url = (body.get("success_url") or "").strip()
    cancel_url = (body.get("cancel_url") or "").strip()

    if not all([email, success_url, cancel_url]):
        return json_error(400, "Missing required fields: email, success_url, cancel_url.")

    try:
        record = get_user_account(userid)
    except Exception as e:
        return opaque_error(502, DB_UNAVAILABLE, e, op="subscription.db")
    if not record:
        return json_error(404, "User not found.")

    try:
        customer_id, error = get_or_create_customer(
            userid, email, f"{record.get('first_name', '')} {record.get('last_name', '')}")
        if error:
            return opaque_error(502, "We could not start checkout just now. "
                                     "Please try again.",
                                RuntimeError(error), op="subscription.customer")

        session_id, checkout_url, error = create_checkout_session(
            customer_id, email, success_url, cancel_url, promo_code)
        if error:
            return opaque_error(502, "We could not start checkout just now. "
                                     "Please try again.",
                                RuntimeError(error), op="subscription.checkout")
        if not checkout_url:
            return json_error(502, "Stripe did not return a checkout URL.")

        update_subscription(userid, {"stripe_customer_id": customer_id})

        return json_response(200, {"session_id": session_id, "checkout_url": checkout_url})
    except Exception as e:
        return opaque_error(502, "Something went wrong with your subscription. "
                                 "Please try again.", e, op="subscription.run")


@router.post("/api/subscription/cancel")
def handle_subscription_cancel(user: AuthedUser = Depends(get_current_user)):
    userid = user.id
    try:
        record = get_user_account(userid)
    except Exception as e:
        return opaque_error(502, DB_UNAVAILABLE, e, op="subscription.db")
    if not record:
        return json_error(404, "User not found.")

    stripe_subscription_id = record.get("stripe_subscription_id")
    if not stripe_subscription_id:
        return json_error(400, "No active Stripe subscription to cancel.")

    try:
        result, error = cancel_subscription(stripe_subscription_id)
        if error:
            return opaque_error(502, "We could not cancel your subscription just now. "
                                     "Please try again.",
                                RuntimeError(error), op="subscription.cancel")

        period_end = (result or {}).get("current_period_end")
        updates = {"subscription_status": "canceled"}
        if period_end:
            updates["subscription_end_at"] = datetime.datetime.fromtimestamp(
                period_end, datetime.timezone.utc).isoformat()
        update_subscription(userid, updates)

        # Cancellation confirmation. Sent from the record we already have, merged with the
        # updates just written rather than re-read: the email's most important sentence is
        # the date access ends, and get_user_account() here could still return the
        # pre-PATCH row. Purely transactional — no win-back offer, deliberately; see
        # email_templates._goodbye.
        send_lifecycle_email_async(userid, "goodbye", record={**record, **updates})

        return json_response(200, {
            "ok": True,
            "message": "Subscription canceled",
            "subscription_end_at": updates.get("subscription_end_at"),
        })
    except Exception as e:
        return opaque_error(502, "Something went wrong with your subscription. "
                                 "Please try again.", e, op="subscription.run")


@router.post("/api/subscription/redeem-promo")
def handle_redeem_promo(body: dict = Depends(json_body),
                        user: AuthedUser = Depends(get_current_user)):
    userid = user.id
    code = (body.get("promo_code") or "").strip().upper()
    if not code:
        return json_error(400, "Missing promo_code.")

    promo_data, error = validate_promo_code(code)
    if error:
        return json_error(400, error)
    if promo_kind(promo_data) != "grant":
        return json_error(400, "That code is applied at checkout, not here.")

    status = promo_data.get("status")
    grant_days = promo_data.get("grant_days")
    if status not in GRANTABLE_STATUSES or not grant_days:
        return json_error(500, "That promo code is misconfigured.")

    try:
        record = get_user_account(userid)
    except Exception as e:
        return opaque_error(502, DB_UNAVAILABLE, e, op="subscription.db")
    if not record:
        return json_error(404, "User not found.")

    used = list(record.get("promo_codes_used") or [])
    if code in used:
        return json_error(400, "You have already used this promo code.")

    if (record.get("subscription_status") or "trial") == "active":
        return json_error(400, "Your subscription is already active — save "
                               "this code for later.")

    current_end = (record.get("subscription_end_at")
                   if (record.get("subscription_status") or "") == "beta"
                   else record.get("trial_ends_at"))
    new_end = extend_from(current_end, grant_days)

    # The check above is advisory only — it answers a nicer error for the ordinary
    # "I already redeemed this" case. The check that MATTERS is inside the PATCH:
    # redeem_promo_conditional carries `used` into the WHERE clause, so N parallel
    # redeems of the same code cannot each pass a stale read and compound the grant.
    # SECURITY_HARDENING_PLAN.md S1-6, finding M6.
    try:
        won = redeem_promo_conditional(userid, code, used, {
            "subscription_status": status,
            "subscription_end_at": new_end,
            "promo_codes_used": used + [code],
        })
    except Exception as e:
        return opaque_error(502, DB_UNAVAILABLE, e, op="subscription.db")

    if not won:
        # Zero rows matched: somebody else redeemed on this account between our read and
        # our write. Re-read to say which of the two it was, and never re-attempt — a
        # retry loop here is the exploit with extra steps.
        try:
            record = get_user_account(userid) or record
        except Exception:
            pass
        if code in list(record.get("promo_codes_used") or []):
            return json_error(400, "You have already used this promo code.")
        return json_error(409, "Your subscription just changed — reload and try again.")

    try:
        record = get_user_account(userid)
    except Exception as e:
        return opaque_error(502, DB_UNAVAILABLE, e, op="subscription.db")

    return json_response(200, {
        "ok": True,
        "applied": code,
        "description": promo_data.get("description"),
        "subscription": subscription_state(record),
    })


@router.post("/api/subscription/validate-promo")
def handle_validate_promo(body: dict = Depends(json_body),
                          user: AuthedUser = Depends(get_optional_user)):
    # Not gated: this only reads a promo code's shape and (if signed in) whether this
    # account already used it. Soft auth — a signed-out caller still gets validity/kind.
    promo_code = (body.get("promo_code") or "").strip()
    userid = user.id if user else ""

    if not promo_code:
        return json_error(400, "Missing promo_code.")

    promo_data, error = validate_promo_code(promo_code)
    if error:
        return json_error(400, error)

    if userid:
        try:
            record = get_user_account(userid)
            if record:
                used_codes = record.get("promo_codes_used") or []
                if promo_code.upper() in used_codes:
                    return json_error(400, "You have already used this promo code.")
        except Exception:
            pass

    return json_response(200, {
        "valid": True,
        "kind": promo_kind(promo_data),
        "description": promo_data.get("description"),
        "discount_months": promo_data.get("discount_months"),
        "discount_percent": promo_data.get("discount_percent"),
    })

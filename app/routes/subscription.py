"""Subscription routes: status, checkout, cancel, promo, and the Stripe webhook.

Two-tier model — there is no trial and no lockout; the webhook writes subscription_status
back onto the account (active/canceled/past_due), which is what flips ai_tier to paid.
"""
import datetime
import json

from fastapi import APIRouter, Request, Depends

from app.core import (
    get_user_account, subscription_state, ai_tier, touch_user_activity,
    update_subscription, redeem_promo_conditional, get_userid_by_stripe_customer,
)
from app.services import budget
from app.deps import (json_body, json_response, json_error,
                      opaque_error, DB_UNAVAILABLE, capped_raw_body)
from app.services.email import send_lifecycle_email_async
from app.auth import get_current_user, get_optional_user, AuthedUser
from wingman.subscription_common import (
    get_or_create_customer, create_checkout_session, cancel_subscription,
    validate_promo_code, promo_kind, extend_from, note_promo_redemption,
    verify_stripe_webhook_signature, GRANTABLE_STATUSES,
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
    # Carry the tier + a Free-tier allowance snapshot so the client can render the meter and
    # the tier badge from the status call the app already makes, without a second request.
    # feature=None reads the dollar/action state without decrementing anything.
    state = {**subscription_state(record), "ai_tier": ai_tier(record)}
    state["allowance"] = budget.ai_allowance_state(userid, feature=None)
    return json_response(200, state)


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

        # current_period_end moved onto subscription items in Stripe 2025-03-31.basil+ — read
        # it via _sub_period_end, not off the top level, or a cancel records no end date and
        # access is revoked immediately instead of at period end. See _sub_period_end.
        period_end = _sub_period_end(result or {})
        updates = {"subscription_status": "canceled"}
        end_iso = _period_end_iso(period_end)
        if end_iso:
            updates["subscription_end_at"] = end_iso
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

    # The global usage counter, AFTER the grant is safely written. Best-effort and
    # deliberately not part of the transaction: the per-user "already used" guard is the
    # conditional PATCH above, and failing to bump a counter must never fail a redemption
    # the student has already earned. S1-10.
    note_promo_redemption(code, promo_data)

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


# ---------------------------------------------------------------------------
# Stripe webhook
# ---------------------------------------------------------------------------
# The one endpoint Stripe itself calls. It is UNAUTHENTICATED by design — Stripe sends no
# bearer token — so its entire trust comes from the signature check below, exactly as
# verify_stripe_webhook_signature enforces. Without this endpoint, a Checkout Session
# charges the card but nothing ever writes `subscription_status = 'active'` /
# `stripe_subscription_id` back onto the row, so a paying user would stay behind the paywall.
#
# It reads the RAW body (bytes) via a dependency and stays a plain `def`: the signature is
# computed over the exact bytes Stripe sent, so it must not go through JSON parsing first,
# and the Supabase writes below are blocking, so FastAPI must run this in the threadpool
# (see the note in app/deps.py). The cap mirrors the AI proxies' capped_raw_body — a Stripe
# event is a few KB; 512 KB is generous headroom that still refuses a junk flood.
_STRIPE_WEBHOOK_MAX_BYTES = 512 * 1024
_stripe_raw_body = capped_raw_body(_STRIPE_WEBHOOK_MAX_BYTES)


def _period_end_iso(period_end):
    """A Stripe unix `current_period_end` as our ISO-8601 UTC string, or None."""
    if not period_end:
        return None
    try:
        return datetime.datetime.fromtimestamp(
            int(period_end), datetime.timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _sub_period_end(sub):
    """The unix `current_period_end` for a Stripe subscription object, or None.

    Stripe API version 2025-03-31.basil (and every version after, including the
    2026-08-26.dahlia this account runs on) REMOVED `current_period_end` from the
    Subscription object and moved it onto the subscription ITEMS
    (`items.data[].current_period_end`). Reading only the top-level field silently
    returned None on those versions, so a cancel-at-period-end never recorded the real
    end date and `subscription_state()` then revoked a paying user's access immediately —
    the opposite of the cancel-at-period-end promise. Read the item first, fall back to the
    legacy top-level field so older API versions (and any single-value payloads) still work.
    """
    if not isinstance(sub, dict):
        return None
    for item in ((sub.get("items") or {}).get("data") or []):
        pe = item.get("current_period_end")
        if pe:
            return pe
    return sub.get("current_period_end")


def _invoice_period_end(invoice):
    """The unix period end (next renewal) for a paid subscription invoice, or None.

    invoice.payment_succeeded is the event that reliably fires on the FIRST charge (and every
    renewal), so it — not the Subscription events — is what populates the "Renews {date}" line
    on an active row. Each invoice line carries `period.end`; take the latest across lines
    (a proration invoice can carry more than one), falling back to the invoice-level
    `period_end`. Unlike the Subscription object, invoice lines kept `period` through the
    basil/dahlia field move, so this needs no items-vs-top-level dance.
    """
    if not isinstance(invoice, dict):
        return None
    ends = [(line.get("period") or {}).get("end")
            for line in ((invoice.get("lines") or {}).get("data") or [])]
    ends = [e for e in ends if e]
    if ends:
        return max(ends)
    return invoice.get("period_end")


def _updates_from_subscription(sub):
    """The users-row updates implied by a Stripe subscription object.

    Maps Stripe's status onto our own (see subscription_state): active/trialing → active,
    past_due → past_due, canceled/unpaid/incomplete_expired → canceled. A status we do not
    recognise (incomplete, paused, anything new) leaves the row untouched — a status we
    cannot confidently map must not silently grant or revoke access.

    Mirrors the app's cancel-at-period-end model (see cancel_subscription): a subscription
    still active at Stripe but flagged `cancel_at_period_end` is written 'canceled' with the
    period-end date, so subscription_state() keeps access until that date — they paid for it.

    `subscription_end_at` is written for an ACTIVE sub too (the current period end), because the
    subscription screen renders it as the "Renews {date}" line; the field is dual-purpose
    (renewal date while active, access-ends date once canceled). It is display-only for an
    active row — subscription_state() grants an active account access unconditionally.
    """
    status = sub.get("status")
    updates = {}
    sub_id = sub.get("id")
    if sub_id:
        updates["stripe_subscription_id"] = sub_id
    end_iso = _period_end_iso(_sub_period_end(sub))
    if status in ("active", "trialing"):
        if sub.get("cancel_at_period_end"):
            updates["subscription_status"] = "canceled"
            if end_iso:
                updates["subscription_end_at"] = end_iso
        else:
            updates["subscription_status"] = "active"
            if end_iso:
                updates["subscription_end_at"] = end_iso
    elif status == "past_due":
        updates["subscription_status"] = "past_due"
    elif status in ("canceled", "unpaid", "incomplete_expired"):
        updates["subscription_status"] = "canceled"
        if end_iso:
            updates["subscription_end_at"] = end_iso
    return updates


def _apply_updates_for_customer(customer_id, updates):
    """Write `updates` to the account owning this Stripe customer id.

    A no-op when there is nothing to write or the customer maps to no account — a stray or
    test event referencing a customer we never stored is acknowledged, not an error.
    """
    if not updates:
        return
    userid = get_userid_by_stripe_customer(customer_id)
    if userid:
        update_subscription(userid, updates)


@router.post("/api/webhook/stripe")
def handle_stripe_webhook(request: Request, payload: bytes = Depends(_stripe_raw_body)):
    # The signature IS the authentication. verify_stripe_webhook_signature returns False on
    # a bad signature AND when STRIPE_WEBHOOK_SECRET is unset — both mean "do not trust this
    # body", so an unconfigured secret fails closed rather than processing forged events.
    signature = request.headers.get("stripe-signature", "")
    if not verify_stripe_webhook_signature(payload, signature):
        return json_error(400, "Invalid or missing Stripe signature.")

    try:
        event = json.loads(payload.decode())
    except Exception:
        return json_error(400, "Malformed webhook body.")

    event_type = event.get("type") or ""
    obj = (event.get("data") or {}).get("object") or {}

    try:
        if event_type == "checkout.session.completed":
            # Checkout finished. Mark the customer active and record the subscription id; the
            # customer.subscription.* events that follow keep the status precise from here on.
            updates = {"subscription_status": "active"}
            sub_id = obj.get("subscription")
            if sub_id:
                updates["stripe_subscription_id"] = sub_id
            customer_id = obj.get("customer")
            _apply_updates_for_customer(customer_id, updates)
            # Welcome-to-paid email. checkout.session.completed is exactly the free -> paid
            # moment (renewals arrive as invoice.payment_succeeded, not this), so it fires here
            # and not on the subscription.updated stream. Deduped on the subscription id, so
            # repeated deliveries of this event send once and a genuine re-subscribe (new id)
            # correctly earns a fresh welcome. Async + never-raises, like every lifecycle send.
            userid = get_userid_by_stripe_customer(customer_id)
            if userid:
                send_lifecycle_email_async(userid, "subscribed",
                                           dedupe_key=(sub_id or obj.get("id") or ""))

        elif event_type in ("customer.subscription.created",
                            "customer.subscription.updated",
                            "customer.subscription.deleted"):
            # For these, obj IS the subscription object.
            _apply_updates_for_customer(obj.get("customer"),
                                        _updates_from_subscription(obj))

        elif event_type == "invoice.payment_succeeded":
            # The first charge and every renewal cleared — confirm access AND record the new
            # period end, which is what the subscription screen shows as "Renews {date}". This
            # is the event that reliably carries the period end on the initial subscribe (the
            # checkout.session object does not, and customer.subscription.created is not among
            # the endpoint's selected events).
            updates = {"subscription_status": "active"}
            end_iso = _period_end_iso(_invoice_period_end(obj))
            if end_iso:
                updates["subscription_end_at"] = end_iso
            _apply_updates_for_customer(obj.get("customer"), updates)

        elif event_type == "invoice.payment_failed":
            _apply_updates_for_customer(obj.get("customer"),
                                        {"subscription_status": "past_due"})

        # Any other event type is acknowledged (200) and ignored.
    except Exception as e:
        # A non-2xx makes Stripe retry with backoff, which is the right behaviour for a
        # transient Supabase failure. opaque_error logs the detail and returns only a ref.
        return opaque_error(502, "Could not process the webhook just now.",
                            e, op="subscription.webhook")

    return json_response(200, {"received": True})

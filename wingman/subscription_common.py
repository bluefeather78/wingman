#!/usr/bin/env python3
"""Stripe subscription management — handles trials, paid plans, and promo codes.
Uses Stripe's HTTP API directly (no external dependency required).

Promo codes are validated server-side against the `promo_codes` table
(db/promo_codes_schema.sql) as of S1-10, with the built-in PROMO_CODES dict kept as the
degrade path for a database where that migration has not been run.
"""
import datetime
import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import base64
import hashlib
import hmac

from wingman import supabase_common


STRIPE_API_KEY = os.environ.get("STRIPE_API_KEY", "")
STRIPE_API_URL = "https://api.stripe.com/v1"
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

# Trial and pricing configuration
TRIAL_DAYS = 7
PLAN_PRICE_CENTS = 999  # $9.99/month
PLAN_PRICE_ID = os.environ.get("STRIPE_PRICE_ID", "price_test_subscription")  # Set in .env for real deployments

# The BUILT-IN promo table — the fallback, not the source of record any more.
#
# S1-10, finding L3: these were the only definition, so anybody who could read the
# repository had free access, there was no way to expire a code or cap how often it could
# be handed out, and retiring one meant a deploy. They now live in the `promo_codes` table
# (db/promo_codes_schema.sql), which load_promo_codes() below reads.
#
# This dict stays as the degrade path, exactly as this repo does everywhere else a manual
# migration is involved: until the SQL is run — or if Supabase is unreachable at that
# moment — promo redemption behaves precisely as it did before, rather than every code
# suddenly reading as invalid. The seed rows in that file match these three, so the
# switchover is a no-op.
#
# `kind` decides how a code is applied, and the two kinds are not interchangeable:
#
#   "grant"    — redeemed immediately against the user's own row, server-side, with no
#                Stripe involvement and no card. Sets `status` and extends access by
#                `grant_days`. Works even with Stripe unconfigured. Redeem endpoint:
#                POST /api/subscription/redeem-promo.
#   "checkout" — a discount that only means anything once Stripe is in the picture; it is
#                passed to create_checkout_session() and applied there. Validating one is
#                all the UI can do before the user reaches checkout.
#
# Anything without a `kind` is treated as "checkout", which is what the original two are.
PROMO_CODES = {
    "BETAUSER": {
        "kind": "grant",
        "status": "beta",
        "grant_days": 7,
        "description": "Beta access for 1 more week",
    },
    "FREEMONTH": {"kind": "checkout", "discount_months": 1, "description": "1 free month"},
    "WELCOME10": {"kind": "checkout", "discount_percent": 10, "description": "10% off first month"},
}

# Statuses a "grant" promo may set. Guards against a typo in the table above quietly
# writing a status that subscription_state() has no branch for, which would read as
# "no access" and lock out the very user who just redeemed a code.
GRANTABLE_STATUSES = {"beta"}


def stripe_request(method, endpoint, data=None, params=None):
    """Make an authenticated request to Stripe's API."""
    if not STRIPE_API_KEY:
        return None, "Stripe API key not configured"

    url = f"{STRIPE_API_URL}{endpoint}"
    if params:
        url += "?" + urllib.parse.urlencode(params)

    headers = {
        "Authorization": f"Bearer {STRIPE_API_KEY}",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    body = None
    if data:
        body = urllib.parse.urlencode(data).encode()

    try:
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else None, None
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        try:
            error_data = json.loads(error_body)
            error_msg = error_data.get("error", {}).get("message", str(e))
        except:
            error_msg = error_body
        return None, error_msg
    except Exception as e:
        return None, str(e)


def create_customer(userid, email, name=""):
    """Create a Stripe customer for a user."""
    data = {
        "email": email,
        "metadata[userid]": userid,
    }
    if name:
        data["name"] = name

    result, error = stripe_request("POST", "/customers", data)
    if error:
        return None, error
    return result.get("id"), None


def get_or_create_customer(userid, email, name=""):
    """Get existing Stripe customer or create a new one."""
    # First, try to find existing customer by metadata
    result, _ = stripe_request("GET", "/customers", params={"limit": 100})
    if result and result.get("data"):
        for customer in result["data"]:
            if customer.get("metadata", {}).get("userid") == userid:
                return customer["id"], None

    # Not found, create new
    return create_customer(userid, email, name)


def create_checkout_session(customer_id, email, success_url, cancel_url, promo_code=None):
    """Create a Stripe checkout session for subscription."""
    data = {
        "customer": customer_id,
        "payment_method_types[]": "card",
        "line_items[0][price]": PLAN_PRICE_ID,
        "line_items[0][quantity]": "1",
        "mode": "subscription",
        "success_url": success_url,
        "cancel_url": cancel_url,
        "subscription_data[trial_period_days]": str(TRIAL_DAYS),
    }

    # Add promo code if provided and valid
    if promo_code:
        promo_data, _ = validate_promo_code(promo_code)
        if promo_data:
            if "discount_months" in promo_data:
                # For discount months, extend trial
                data["subscription_data[trial_period_days]"] = str(
                    TRIAL_DAYS + (promo_data["discount_months"] * 30)
                )
            elif "discount_percent" in promo_data:
                # Would need a coupon in Stripe for percent-based discounts
                pass

    result, error = stripe_request("POST", "/checkout/sessions", data)
    if error:
        return None, None, error
    # Stripe returns the hosted-checkout URL on the session. Build it by hand and it
    # will 404 — the old /pay/{id} form is retired and the current one is not a
    # predictable function of the session id.
    return result.get("id"), result.get("url"), None


def create_subscription_direct(customer_id, promo_code=None):
    """Create a subscription directly (for API use, not checkout)."""
    data = {
        "customer": customer_id,
        "items[0][price]": PLAN_PRICE_ID,
        "trial_period_days": str(TRIAL_DAYS),
    }

    result, error = stripe_request("POST", "/subscriptions", data)
    if error:
        return None, error
    return result.get("id"), None


def get_subscription(subscription_id):
    """Get subscription details from Stripe."""
    result, error = stripe_request("GET", f"/subscriptions/{subscription_id}")
    if error:
        return None, error
    return result, None


def cancel_subscription(subscription_id):
    """Cancel at the end of the paid period, not immediately.

    The user has already paid through the current period, so revoking access the
    instant they click Cancel would be taking money for time they don't get — and it
    contradicts what the confirmation dialog promises them. Returns the updated
    subscription object; `current_period_end` on it is when access actually stops.
    """
    data = {"cancel_at_period_end": "true"}
    result, error = stripe_request("POST", f"/subscriptions/{subscription_id}", data)
    if error:
        return None, error
    return result, None


def get_customer_subscriptions(customer_id):
    """Get all subscriptions for a customer."""
    result, error = stripe_request("GET", "/subscriptions", params={
        "customer": customer_id,
        "limit": 100,
    })
    if error:
        return None, error
    return result.get("data", []), None


# ---------- The promo_codes table (S1-10, finding L3) ----------
#
# Cached in-process for a short window. A promo lookup happens on a keystroke in the redeem
# box, and a code's definition changes about once a quarter — but the cache is short enough
# that retiring a code in the dashboard takes effect within a minute rather than at the next
# deploy, which is the whole point of moving them out of the source.
PROMO_CACHE_TTL_SECONDS = int(os.environ.get("PROMO_CACHE_TTL_SECONDS", "") or 60)

_promo_cache = {"codes": None, "at": 0.0}
_promo_table_warned = False


def _promo_row_to_definition(row):
    """One promo_codes row in the shape the rest of the code already expects."""
    return {k: v for k, v in {
        "kind": row.get("kind") or "checkout",
        "status": row.get("status"),
        "grant_days": row.get("grant_days"),
        "discount_months": row.get("discount_months"),
        "discount_percent": row.get("discount_percent"),
        "description": row.get("description"),
        "expires_at": row.get("expires_at"),
        "max_redemptions": row.get("max_redemptions"),
        "redemption_count": row.get("redemption_count") or 0,
    }.items() if v is not None}


def load_promo_codes(force=False):
    """{CODE: definition} from the promo_codes table, falling back to PROMO_CODES.

    Degrades rather than breaks, which is this repo's rule for anything behind a manual
    migration: if db/promo_codes_schema.sql has not been run, or Supabase is unreachable
    right now, redemption behaves exactly as it did before instead of every code suddenly
    reading as invalid — which would look, to a student holding a valid code, identical to
    being cheated. Warns ONCE per process so the operator sees it without the log filling up.
    """
    global _promo_table_warned
    now = time.time()
    if not force and _promo_cache["codes"] is not None and \
            now - _promo_cache["at"] < PROMO_CACHE_TTL_SECONDS:
        return _promo_cache["codes"]

    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    codes = None
    if url and key:
        try:
            rows = supabase_common.supabase_get(
                url, "promo_codes",
                {"select": ("code,kind,status,grant_days,discount_months,discount_percent,"
                            "description,is_active,expires_at,max_redemptions,"
                            "redemption_count"),
                 "is_active": "eq.true"},
                key)
            codes = {str(r.get("code") or "").upper(): _promo_row_to_definition(r)
                     for r in (rows or []) if r.get("code")}
        except Exception as e:                                     # noqa: BLE001
            if not _promo_table_warned:
                _promo_table_warned = True
                print(f"[WARN] promo_codes table unreadable ({e}); using the built-in "
                      f"codes. Run db/promo_codes_schema.sql in the Supabase SQL editor.")
    if codes is None:
        codes = dict(PROMO_CODES)
    _promo_cache["codes"] = codes
    _promo_cache["at"] = now
    return codes


def _promo_expired(definition):
    expires = definition.get("expires_at")
    if not expires:
        return False
    try:
        when = datetime.datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
    except ValueError:
        # An unparseable expiry is treated as EXPIRED. A date nobody can read is not a
        # reason to keep handing out free access.
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    return datetime.datetime.now(datetime.timezone.utc) >= when


def _promo_exhausted(definition):
    cap = definition.get("max_redemptions")
    if cap in (None, ""):
        return False
    try:
        return int(definition.get("redemption_count") or 0) >= int(cap)
    except (TypeError, ValueError):
        return False


def validate_promo_code(code):
    """Validate a promo code. Returns (promo_data, error).

    Expiry and the redemption cap answer the SAME "Invalid promo code" as an unknown code,
    deliberately: distinguishing them would tell a caller which codes exist, which is the
    thing having them in a table is meant to stop.
    """
    code_upper = (code or "").upper().strip()
    definition = load_promo_codes().get(code_upper)
    if not definition:
        return None, "Invalid promo code"
    if _promo_expired(definition) or _promo_exhausted(definition):
        return None, "Invalid promo code"
    return definition, None


def note_promo_redemption(code, definition):
    """Increment promo_codes.redemption_count for `code`, once, without a lost update.

    Compare-and-swap on the count we read, like S1-6's conditional PATCH: two concurrent
    redemptions must not both write count+1 from the same starting value, or a
    max_redemptions of 100 would hand out considerably more than 100.

    Best-effort by design and always returns None — the per-user "already used" guard is
    users.promo_codes_used and is enforced by S1-6's conditional write. This count is the
    GLOBAL cap and the operator's usage number; failing it must not fail a redemption the
    student already earned.
    """
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        return
    try:
        observed = int((definition or {}).get("redemption_count") or 0)
        supabase_common.supabase_patch(
            url, "promo_codes",
            {"code": f"eq.{(code or '').upper().strip()}",
             "redemption_count": f"eq.{observed}"},
            {"redemption_count": observed + 1}, key)
    except Exception as e:                                         # noqa: BLE001
        print(f"[WARN] Could not record redemption of {code!r}: {e}")
    finally:
        # The cached count is now stale either way, so the next lookup re-reads. Without
        # this a capped code would keep validating from a cache that never moves.
        _promo_cache["codes"] = None


def verify_stripe_webhook_signature(payload, signature):
    """Verify Stripe webhook signature."""
    if not STRIPE_WEBHOOK_SECRET:
        return False

    try:
        # Stripe's Signature header is a comma-separated list of key=value pairs,
        # e.g. "t=1620000000,v1=abc123...". Sign "<timestamp>.<payload>" and compare
        # the result against the v1 signature — NOT against any slice of the content.
        parts = {}
        for item in signature.split(","):
            key, _, value = item.partition("=")
            parts[key.strip()] = value
        timestamp = parts.get("t")
        v1 = parts.get("v1")
        if not timestamp or not v1:
            return False
        signed_content = timestamp + "." + payload.decode()
        expected_sig = hmac.new(
            STRIPE_WEBHOOK_SECRET.encode(),
            signed_content.encode(),
            hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected_sig.encode("utf-8"), v1.encode("utf-8"))
    except Exception:
        return False


def promo_kind(promo_data):
    """How a promo is applied. Codes predating `kind` are checkout discounts."""
    return (promo_data or {}).get("kind", "checkout")


def extend_from(current_iso, days):
    """ISO timestamp `days` after whichever is later: now, or `current_iso`.

    Extending from the later of the two is what makes a grant *additive* — someone who
    redeems BETAUSER with two days of trial left gets nine days, not seven. Extending
    from now would silently take back the trial they hadn't used yet.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    base = now
    if current_iso:
        try:
            current = datetime.datetime.fromisoformat(str(current_iso).replace("Z", "+00:00"))
            if current.tzinfo is None:
                current = current.replace(tzinfo=datetime.timezone.utc)
            base = max(now, current)
        except ValueError:
            base = now
    return (base + datetime.timedelta(days=days)).isoformat()


def trial_ends_at_iso(days=TRIAL_DAYS):
    """Return ISO timestamp for when trial ends."""
    end = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=days)
    return end.isoformat()


def is_trial_expired(trial_ends_at_iso):
    """Check if a trial has expired."""
    if not trial_ends_at_iso:
        return True
    try:
        end = datetime.datetime.fromisoformat(trial_ends_at_iso.replace("Z", "+00:00"))
        return datetime.datetime.now(datetime.timezone.utc) > end
    except:
        return True


def days_until_trial_end(trial_ends_at_iso):
    """Return number of days left in trial, or 0 if expired."""
    if not trial_ends_at_iso:
        return 0
    try:
        end = datetime.datetime.fromisoformat(trial_ends_at_iso.replace("Z", "+00:00"))
        delta = end - datetime.datetime.now(datetime.timezone.utc)
        # Round up, not down: on signup day a 7-day trial has 6 days and 23-odd hours
        # left, and delta.days floors that to 6. Users read "6 days left" one second
        # after starting a 7-day trial as the trial being short-changed.
        return max(0, math.ceil(delta.total_seconds() / 86400))
    except:
        return 0

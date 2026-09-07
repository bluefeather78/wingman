"""Unit tests for the Stripe period-end extraction fix (2026-09-07).

Stripe API version 2025-03-31.basil+ (the account runs 2026-08-26.dahlia) removed
`current_period_end` from the Subscription object and moved it onto the subscription
ITEMS. Reading only the top-level field silently returned None, so a cancel-at-period-end
recorded no end date and subscription_state() then revoked a paying user's access
immediately. `_sub_period_end` reads the item first with a legacy top-level fallback.

Pure functions, no Supabase / network seam.
"""
import datetime

from app.routes.subscription import _sub_period_end, _updates_from_subscription, _period_end_iso

# 2026-10-07T00:00:00Z, a plausible next-cycle end for a sub started ~2026-09-07.
FUTURE_TS = 1791331200


def _iso(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).isoformat()


# ---------- _sub_period_end ----------

def test_reads_period_end_from_items_dahlia_shape():
    """The current (basil+/dahlia) shape: period end lives under items.data[]."""
    sub = {"id": "sub_x", "status": "active",
           "items": {"data": [{"current_period_end": FUTURE_TS}]}}
    assert _sub_period_end(sub) == FUTURE_TS


def test_falls_back_to_legacy_top_level_field():
    """Pre-basil payloads (and any single-value shape) still resolve."""
    sub = {"id": "sub_x", "status": "active", "current_period_end": FUTURE_TS}
    assert _sub_period_end(sub) == FUTURE_TS


def test_item_wins_over_top_level_when_both_present():
    sub = {"id": "sub_x", "current_period_end": 111,
           "items": {"data": [{"current_period_end": FUTURE_TS}]}}
    assert _sub_period_end(sub) == FUTURE_TS


def test_none_when_absent_everywhere():
    assert _sub_period_end({"id": "sub_x", "items": {"data": [{}]}}) is None
    assert _sub_period_end({"id": "sub_x"}) is None
    assert _sub_period_end({}) is None
    assert _sub_period_end(None) is None


# ---------- _updates_from_subscription (regression) ----------

def test_cancel_at_period_end_records_date_from_items():
    """The bug: a canceling sub on the dahlia shape must still write subscription_end_at,
    or subscription_state revokes access immediately instead of at period end."""
    sub = {"id": "sub_x", "status": "active", "cancel_at_period_end": True,
           "items": {"data": [{"current_period_end": FUTURE_TS}]}}
    updates = _updates_from_subscription(sub)
    assert updates["subscription_status"] == "canceled"
    assert updates["subscription_end_at"] == _iso(FUTURE_TS)


def test_canceled_status_records_date_from_items():
    sub = {"id": "sub_x", "status": "canceled",
           "items": {"data": [{"current_period_end": FUTURE_TS}]}}
    updates = _updates_from_subscription(sub)
    assert updates["subscription_status"] == "canceled"
    assert updates["subscription_end_at"] == _iso(FUTURE_TS)


def test_active_not_canceling_leaves_no_end_date():
    sub = {"id": "sub_x", "status": "active",
           "items": {"data": [{"current_period_end": FUTURE_TS}]}}
    updates = _updates_from_subscription(sub)
    assert updates["subscription_status"] == "active"
    assert "subscription_end_at" not in updates

"""Unit tests for app.core.subscription_state / _iso_in_future / _login_payload
and app.deps.subscription_block_reason.

No Supabase: subscription_block_reason's only network seam is get_user_subscription (the five
subscription columns, cached per Phase 2 item 3), mocked via monkeypatch on
app.deps.get_user_subscription — patching the seam rather than the cache keeps these tests
about gate logic. Dates are computed relative to now, so no clock
freezing is needed.
"""
import datetime

import pytest

from app.core import subscription_state, _iso_in_future, _login_payload, ai_tier
import app.deps as deps


def _iso(delta_days):
    return (datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(days=delta_days)).isoformat()


# ---------- _iso_in_future ----------

@pytest.mark.parametrize("value,expected", [
    (None, False),
    ("", False),
    ("not-a-date", False),
    (_iso(1), True),
    (_iso(-1), False),
])
def test_iso_in_future(value, expected):
    assert _iso_in_future(value) is expected


def test_iso_in_future_naive_treated_as_utc():
    # A naive timestamp in the future still reads as future.
    naive = (datetime.datetime.now() + datetime.timedelta(days=1)).replace(
        tzinfo=None).isoformat()
    assert _iso_in_future(naive) is True


# ---------- subscription_state: two-tier model, nobody locked out ----------
#
# The load-bearing change: has_access is ALWAYS True for a signed-in account (no lockout).
# `in_paid_period` is the field that varies, and it is what the AI tier turns on.

def test_state_active_is_paid_period():
    st = subscription_state({"subscription_status": "active"})
    assert st["has_access"] is True
    assert st["in_paid_period"] is True
    assert st["status"] == "active"
    assert st["days_left"] == 0  # active has no countdown


def test_state_trial_valid_is_free_with_access():
    # A legacy trial row (pre-migration) keeps access but is NOT a paid period.
    st = subscription_state({"subscription_status": "trial", "trial_ends_at": _iso(3)})
    assert st["has_access"] is True
    assert st["in_paid_period"] is False


def test_state_expired_trial_is_not_locked_out():
    # Was has_access False under the trial model; now a lapsed trial is just Free.
    st = subscription_state({"subscription_status": "trial", "trial_ends_at": _iso(-1)})
    assert st["has_access"] is True
    assert st["in_paid_period"] is False


def test_state_trial_dateless_has_access():
    st = subscription_state({"subscription_status": "trial", "trial_ends_at": None})
    assert st["has_access"] is True
    assert st["in_paid_period"] is False


def test_state_beta_active_is_paid_period():
    st = subscription_state({"subscription_status": "beta", "subscription_end_at": _iso(5)})
    assert st["has_access"] is True
    assert st["in_paid_period"] is True
    assert st["days_left"] >= 1


def test_state_beta_expired_lapses_to_free():
    st = subscription_state({"subscription_status": "beta", "subscription_end_at": _iso(-1)})
    assert st["has_access"] is True       # never a lockout
    assert st["in_paid_period"] is False  # comp ran out -> metered Free


def test_state_canceled_still_in_paid_period():
    st = subscription_state({"subscription_status": "canceled", "subscription_end_at": _iso(2)})
    assert st["has_access"] is True
    assert st["in_paid_period"] is True   # keeps the tier until the period paid for


def test_state_canceled_period_ended_lapses_to_free():
    st = subscription_state({"subscription_status": "canceled", "subscription_end_at": _iso(-2)})
    assert st["has_access"] is True       # never a lockout
    assert st["in_paid_period"] is False  # lapses to metered Free


def test_state_past_due_is_free_with_access():
    st = subscription_state({"subscription_status": "past_due"})
    assert st["has_access"] is True       # free-with-cap, not locked out
    assert st["in_paid_period"] is False
    assert st["status"] == "past_due"


def test_state_unknown_status_is_free_with_access():
    st = subscription_state({"subscription_status": "something_stripe_invented"})
    assert st["has_access"] is True
    assert st["in_paid_period"] is False


def test_state_defaults_missing_status_to_free():
    st = subscription_state({})
    assert st["status"] == "free"
    assert st["has_access"] is True
    assert st["in_paid_period"] is False


def test_state_carries_through_fields():
    rec = {"subscription_status": "active", "subscription_end_at": _iso(1),
           "stripe_customer_id": "cus_123"}
    st = subscription_state(rec)
    assert st["stripe_customer_id"] == "cus_123"
    assert st["subscription_end_at"] == rec["subscription_end_at"]


# ---------- ai_tier (two-tier model, derived from subscription_state) ----------

def test_ai_tier_active_is_paid():
    assert ai_tier({"subscription_status": "active"}) == "paid"


def test_ai_tier_beta_in_period_is_paid():
    assert ai_tier({"subscription_status": "beta", "subscription_end_at": _iso(3)}) == "paid"


def test_ai_tier_beta_lapsed_is_free():
    # A lapsed comp falls back to metered Free, never to a lockout.
    assert ai_tier({"subscription_status": "beta", "subscription_end_at": _iso(-1)}) == "free"


def test_ai_tier_canceled_in_period_is_paid():
    assert ai_tier({"subscription_status": "canceled", "subscription_end_at": _iso(2)}) == "paid"


def test_ai_tier_canceled_lapsed_is_free():
    assert ai_tier({"subscription_status": "canceled", "subscription_end_at": _iso(-2)}) == "free"


def test_ai_tier_trial_is_free():
    # Pre-retirement: a valid trial is still the metered Free tier for AI purposes.
    assert ai_tier({"subscription_status": "trial", "trial_ends_at": _iso(3)}) == "free"


def test_ai_tier_past_due_is_free():
    assert ai_tier({"subscription_status": "past_due"}) == "free"


def test_ai_tier_default_is_free():
    assert ai_tier({}) == "free"


# ---------- _login_payload ----------

def test_login_payload_shape():
    rec = {
        "userid": "alice", "first_name": "Al", "last_name": "Ice",
        "email": "al@example.com", "location": "Seattle",
        "subscription_status": "active",
    }
    p = _login_payload(rec)
    assert p["ok"] is True
    assert p["userid"] == "alice"
    assert p["firstName"] == "Al"
    assert p["lastName"] == "Ice"
    assert p["email"] == "al@example.com"
    assert p["location"] == "Seattle"
    assert p["subscription"]["has_access"] is True


def test_login_payload_missing_location_defaults_empty():
    rec = {"userid": "a", "first_name": "A", "last_name": "B", "email": "a@b.co"}
    assert _login_payload(rec)["location"] == ""


# ---------- subscription_block_reason (two-tier: never blocks) ----------
#
# There is no app-access lockout anymore, so this returns None for EVERY account state —
# including the ones that used to 402 (expired trial, past_due, lapsed canceled/beta). The
# daily AI allowance is a separate 429 gate at the AI route, not a 402 here.

def test_block_reason_empty_userid_none():
    assert deps.subscription_block_reason("") is None
    assert deps.subscription_block_reason(None) is None


@pytest.mark.parametrize("record", [
    {"subscription_status": "active"},
    {"subscription_status": "past_due"},
    {"subscription_status": "trial", "trial_ends_at": _iso(-1)},   # was 402 before
    {"subscription_status": "canceled", "subscription_end_at": _iso(-1)},  # was 402
    {"subscription_status": "beta", "subscription_end_at": _iso(-1)},      # was 402
    {},
])
def test_block_reason_never_blocks(record):
    # No Supabase lookup happens anymore — the function short-circuits to None regardless.
    assert deps.subscription_block_reason("alice") is None


def test_block_reason_does_not_read_supabase(monkeypatch):
    def unexpected(_uid):
        raise AssertionError("subscription_block_reason must not read the account anymore")
    monkeypatch.setattr(deps, "get_user_subscription", unexpected)
    assert deps.subscription_block_reason("alice") is None

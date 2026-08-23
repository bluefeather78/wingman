"""Unit tests for app.core.subscription_state / _iso_in_future / _login_payload
and app.deps.subscription_block_reason.

No Supabase: subscription_block_reason's only network seam is get_user, mocked via
monkeypatch on app.deps.get_user. Dates are computed relative to now, so no clock
freezing is needed.
"""
import datetime

import pytest

from app.core import subscription_state, _iso_in_future, _login_payload
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


# ---------- subscription_state: six status paths ----------

def test_state_active():
    st = subscription_state({"subscription_status": "active"})
    assert st["has_access"] is True
    assert st["status"] == "active"
    assert st["days_left"] == 0  # active has no countdown


def test_state_trial_valid():
    st = subscription_state({"subscription_status": "trial", "trial_ends_at": _iso(3)})
    assert st["has_access"] is True
    assert st["is_trial_expired"] is False
    assert st["days_left"] >= 1


def test_state_trial_expired():
    st = subscription_state({"subscription_status": "trial", "trial_ends_at": _iso(-1)})
    assert st["has_access"] is False
    assert st["is_trial_expired"] is True
    assert st["days_left"] == 0


def test_state_trial_dateless_reads_as_not_expired():
    # The load-bearing rule: NULL trial_ends_at is "clock not started", not "expired".
    st = subscription_state({"subscription_status": "trial", "trial_ends_at": None})
    assert st["has_access"] is True
    assert st["is_trial_expired"] is False


def test_state_beta_active():
    st = subscription_state({"subscription_status": "beta", "subscription_end_at": _iso(5)})
    assert st["has_access"] is True
    assert st["days_left"] >= 1


def test_state_beta_expired():
    st = subscription_state({"subscription_status": "beta", "subscription_end_at": _iso(-1)})
    assert st["has_access"] is False


def test_state_canceled_still_in_paid_period():
    st = subscription_state({"subscription_status": "canceled", "subscription_end_at": _iso(2)})
    assert st["has_access"] is True  # keeps access until the period paid for


def test_state_canceled_period_ended():
    st = subscription_state({"subscription_status": "canceled", "subscription_end_at": _iso(-2)})
    assert st["has_access"] is False


def test_state_past_due_no_access():
    st = subscription_state({"subscription_status": "past_due"})
    assert st["has_access"] is False
    assert st["status"] == "past_due"


def test_state_unknown_status_no_access():
    st = subscription_state({"subscription_status": "something_stripe_invented"})
    assert st["has_access"] is False


def test_state_defaults_missing_status_to_trial():
    st = subscription_state({})
    assert st["status"] == "trial"
    # No trial_ends_at -> dateless trial -> access
    assert st["has_access"] is True


def test_state_carries_through_fields():
    rec = {"subscription_status": "active", "subscription_end_at": _iso(1),
           "stripe_customer_id": "cus_123"}
    st = subscription_state(rec)
    assert st["stripe_customer_id"] == "cus_123"
    assert st["subscription_end_at"] == rec["subscription_end_at"]


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


# ---------- subscription_block_reason ----------

def test_block_reason_empty_userid_none():
    assert deps.subscription_block_reason("") is None
    assert deps.subscription_block_reason(None) is None


def test_block_reason_get_user_raises_fails_open(monkeypatch):
    def boom(_):
        raise RuntimeError("supabase down")
    monkeypatch.setattr(deps, "get_user", boom)
    assert deps.subscription_block_reason("alice") is None


def test_block_reason_no_record_none(monkeypatch):
    monkeypatch.setattr(deps, "get_user", lambda _: None)
    assert deps.subscription_block_reason("alice") is None


def test_block_reason_has_access_none(monkeypatch):
    monkeypatch.setattr(deps, "get_user", lambda _: {"subscription_status": "active"})
    assert deps.subscription_block_reason("alice") is None


def test_block_reason_past_due_message(monkeypatch):
    monkeypatch.setattr(deps, "get_user", lambda _: {"subscription_status": "past_due"})
    msg = deps.subscription_block_reason("alice")
    assert msg and "could not charge" in msg.lower()


def test_block_reason_canceled_message(monkeypatch):
    monkeypatch.setattr(deps, "get_user",
                        lambda _: {"subscription_status": "canceled",
                                   "subscription_end_at": _iso(-1)})
    msg = deps.subscription_block_reason("alice")
    assert msg and "subscription has ended" in msg.lower()


def test_block_reason_beta_message(monkeypatch):
    monkeypatch.setattr(deps, "get_user",
                        lambda _: {"subscription_status": "beta",
                                   "subscription_end_at": _iso(-1)})
    msg = deps.subscription_block_reason("alice")
    assert msg and "beta access has ended" in msg.lower()


def test_block_reason_expired_trial_default_message(monkeypatch):
    monkeypatch.setattr(deps, "get_user",
                        lambda _: {"subscription_status": "trial",
                                   "trial_ends_at": _iso(-1)})
    msg = deps.subscription_block_reason("alice")
    assert msg and "free trial has ended" in msg.lower()


def test_block_reason_lowercases_userid(monkeypatch):
    seen = {}
    def fake_get(uid):
        seen["uid"] = uid
        return {"subscription_status": "active"}
    monkeypatch.setattr(deps, "get_user", fake_get)
    deps.subscription_block_reason("  ALICE  ")
    assert seen["uid"] == "alice"

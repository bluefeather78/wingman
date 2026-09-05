"""Unit tests for wingman/subscription_common.py (repo root, stdlib-only).

Time is frozen by swapping the module's `datetime` reference for a namespace whose
`datetime` class returns a fixed `now()`. Stripe network functions are NOT tested.
"""
import datetime
import hashlib
import hmac
import types

import pytest

from wingman import subscription_common as sc


FROZEN = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)


class FrozenDatetime(datetime.datetime):
    @classmethod
    def now(cls, tz=None):
        return FROZEN if tz is None else FROZEN.astimezone(tz)


@pytest.fixture
def frozen(monkeypatch):
    """Freeze subscription_common's clock at FROZEN while keeping real timedelta/etc."""
    ns = types.SimpleNamespace(
        datetime=FrozenDatetime,
        timedelta=datetime.timedelta,
        timezone=datetime.timezone,
    )
    monkeypatch.setattr(sc, "datetime", ns)
    return FROZEN


# ---------- extend_from ----------

def test_extend_from_none_extends_from_now(frozen):
    out = datetime.datetime.fromisoformat(sc.extend_from(None, 7))
    assert out == frozen + datetime.timedelta(days=7)


def test_extend_from_future_is_additive(frozen):
    # Two days of trial left + a 7-day grant => 9 days from now (max(now, current)).
    current = (frozen + datetime.timedelta(days=2)).isoformat()
    out = datetime.datetime.fromisoformat(sc.extend_from(current, 7))
    assert out == frozen + datetime.timedelta(days=9)


def test_extend_from_past_extends_from_now(frozen):
    current = (frozen - datetime.timedelta(days=5)).isoformat()
    out = datetime.datetime.fromisoformat(sc.extend_from(current, 7))
    assert out == frozen + datetime.timedelta(days=7)


def test_extend_from_zulu_suffix_parsed(frozen):
    current = (frozen + datetime.timedelta(days=3)).isoformat().replace("+00:00", "Z")
    out = datetime.datetime.fromisoformat(sc.extend_from(current, 1))
    assert out == frozen + datetime.timedelta(days=4)


def test_extend_from_naive_timestamp_assumed_utc(frozen):
    # A naive ISO string (no offset) is treated as UTC before comparing.
    current = (frozen + datetime.timedelta(days=3)).replace(tzinfo=None).isoformat()
    out = datetime.datetime.fromisoformat(sc.extend_from(current, 1))
    assert out == frozen + datetime.timedelta(days=4)


def test_extend_from_garbage_falls_back_to_now(frozen):
    out = datetime.datetime.fromisoformat(sc.extend_from("not-a-date", 7))
    assert out == frozen + datetime.timedelta(days=7)


# ---------- trial_ends_at_iso ----------

def test_trial_ends_at_iso_default(frozen):
    out = datetime.datetime.fromisoformat(sc.trial_ends_at_iso())
    assert out == frozen + datetime.timedelta(days=sc.TRIAL_DAYS)


def test_trial_ends_at_iso_custom_days(frozen):
    out = datetime.datetime.fromisoformat(sc.trial_ends_at_iso(10))
    assert out == frozen + datetime.timedelta(days=10)


# ---------- is_trial_expired ----------

def test_is_trial_expired_none_is_true(frozen):
    assert sc.is_trial_expired(None) is True


def test_is_trial_expired_past(frozen):
    past = (frozen - datetime.timedelta(days=1)).isoformat()
    assert sc.is_trial_expired(past) is True


def test_is_trial_expired_future(frozen):
    future = (frozen + datetime.timedelta(days=1)).isoformat()
    assert sc.is_trial_expired(future) is False


def test_is_trial_expired_garbage_is_true(frozen):
    assert sc.is_trial_expired("nonsense") is True


# ---------- days_until_trial_end (math.ceil rounding) ----------

def test_days_until_none_is_zero(frozen):
    assert sc.days_until_trial_end(None) == 0


def test_days_until_ceils_up_one_second_in(frozen):
    # 2 days + 1 second remaining: floor would say 2, ceil says 3.
    end = (frozen + datetime.timedelta(days=2, seconds=1)).isoformat()
    assert sc.days_until_trial_end(end) == 3


def test_days_until_exact_boundary(frozen):
    end = (frozen + datetime.timedelta(days=2)).isoformat()
    assert sc.days_until_trial_end(end) == 2


def test_days_until_expired_is_zero(frozen):
    end = (frozen - datetime.timedelta(days=1)).isoformat()
    assert sc.days_until_trial_end(end) == 0


def test_days_until_garbage_is_zero(frozen):
    assert sc.days_until_trial_end("nope") == 0


# ---------- validate_promo_code (case-fold lookup) ----------

def test_validate_promo_lowercase():
    data, err = sc.validate_promo_code("betauser")
    assert err is None
    assert data["kind"] == "grant"


def test_validate_promo_with_whitespace():
    data, err = sc.validate_promo_code("  freemonth  ")
    assert err is None
    assert data is sc.PROMO_CODES["FREEMONTH"]


def test_validate_promo_invalid():
    data, err = sc.validate_promo_code("NOPE")
    assert data is None
    assert err == "Invalid promo code"


# ---------- promo_kind ----------

def test_promo_kind_grant():
    assert sc.promo_kind(sc.PROMO_CODES["BETAUSER"]) == "grant"


def test_promo_kind_checkout():
    assert sc.promo_kind(sc.PROMO_CODES["FREEMONTH"]) == "checkout"


def test_promo_kind_missing_defaults_checkout():
    # A code predating `kind` (or None) is treated as a checkout discount.
    assert sc.promo_kind({"discount_percent": 10}) == "checkout"
    assert sc.promo_kind(None) == "checkout"


# ---------- verify_stripe_webhook_signature ----------

def test_webhook_no_secret_returns_false(monkeypatch):
    monkeypatch.setattr(sc, "STRIPE_WEBHOOK_SECRET", "")
    assert sc.verify_stripe_webhook_signature(b"{}", "t=1,v1=abc") is False


def test_webhook_valid_signature_verifies(monkeypatch):
    # A correctly computed Stripe signature must verify: sign "<ts>.<payload>" and
    # compare against the v1 value from the header. (Regression guard for the fixed bug
    # where the HMAC was compared against the wrong string and every webhook returned False.)
    secret = "whsec_test"
    monkeypatch.setattr(sc, "STRIPE_WEBHOOK_SECRET", secret)
    payload = b'{"id":"evt_1"}'
    ts = "1700000000"
    real_sig = hmac.new(secret.encode(), (ts + "." + payload.decode()).encode(),
                        hashlib.sha256).hexdigest()
    header = f"t={ts},v1={real_sig}"
    assert sc.verify_stripe_webhook_signature(payload, header) is True


def test_webhook_wrong_signature_returns_false(monkeypatch):
    monkeypatch.setattr(sc, "STRIPE_WEBHOOK_SECRET", "whsec_test")
    header = "t=1700000000,v1=deadbeef"  # not the real HMAC
    assert sc.verify_stripe_webhook_signature(b'{"id":"evt_1"}', header) is False


def test_webhook_missing_v1_returns_false(monkeypatch):
    monkeypatch.setattr(sc, "STRIPE_WEBHOOK_SECRET", "whsec_test")
    assert sc.verify_stripe_webhook_signature(b"{}", "t=1700000000") is False


def test_webhook_malformed_signature_returns_false(monkeypatch):
    monkeypatch.setattr(sc, "STRIPE_WEBHOOK_SECRET", "whsec_test")
    assert sc.verify_stripe_webhook_signature(b"{}", "garbage") is False

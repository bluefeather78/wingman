"""Non-ASCII credentials must answer 403/400, not 500 — S1-12, finding L2.

`hmac.compare_digest` accepts two `str` operands only while BOTH are pure ASCII; give it
one non-ASCII codepoint and it raises TypeError. Four credential checks passed `str`
straight from client input, so `X-Cron-Secret: é` and `?t=é` came back as unhandled 500s.
A 500 on a credential check is the wrong answer, and a distinguishable one.
"""
import hmac

import pytest

import app.routes.email as email_route
import app.services.email as email_service
from app.auth.passwords import verify_password, hash_password
from wingman import subscription_common


class _Req:
    """Minimal stand-in for starlette's Request: the route reads .headers only."""
    def __init__(self, headers=None):
        self.headers = headers or {}


def test_str_compare_digest_really_does_raise():
    """Guards the premise. If CPython ever relaxes this, the fixes below become belt-only."""
    with pytest.raises(TypeError):
        hmac.compare_digest("é", "é")


# ---------------- the cron secret ----------------

def test_non_ascii_cron_secret_is_forbidden_not_a_crash(monkeypatch):
    monkeypatch.setattr(email_route, "EMAIL_CRON_SECRET", "s3cret")
    resp = email_route.handle_email_sweep(request=_Req({"X-Cron-Secret": "é"}), body={})
    assert resp.status_code == 403


def test_the_right_cron_secret_still_passes(monkeypatch):
    """The encode() must not break the happy path — it gets as far as the sweep, not 403."""
    monkeypatch.setattr(email_route, "EMAIL_CRON_SECRET", "s3cret")
    monkeypatch.setattr(email_route.email_service, "run_trial_sweep",
                        lambda **kw: {"ok": True})
    monkeypatch.setattr(email_route.email_service, "run_deadline_alert_sweep",
                        lambda **kw: {"ok": True})
    resp = email_route.handle_email_sweep(request=_Req({"X-Cron-Secret": "s3cret"}), body={})
    assert resp.status_code == 200


# ---------------- the unsubscribe token ----------------

def test_non_ascii_unsubscribe_token_is_invalid_not_a_crash():
    assert email_service.verify_unsubscribe_token("alice", "é") is False


def test_a_real_unsubscribe_token_still_verifies():
    token = email_service.unsubscribe_token("alice")
    assert token  # JWT_SECRET is set by conftest, so the HMAC is real
    assert email_service.verify_unsubscribe_token("alice", token) is True
    assert email_service.verify_unsubscribe_token("bob", token) is False


# ---------------- the legacy password path ----------------

def test_non_ascii_password_hash_against_a_legacy_row_is_a_miss_not_a_crash():
    """A legacy row holds a bare client SHA-256; the incoming value is client-controlled."""
    legacy = "a" * 64
    assert verify_password(legacy, "é" * 64) == (False, False)


def test_a_matching_legacy_hash_still_verifies_and_asks_for_an_upgrade():
    legacy = "a" * 64
    assert verify_password(legacy, legacy) == (True, True)


def test_argon2_rows_are_untouched_by_the_change():
    stored = hash_password("b" * 64)
    ok, _ = verify_password(stored, "b" * 64)
    assert ok is True


# ---------------- the Stripe webhook signature ----------------

def test_non_ascii_stripe_signature_is_rejected(monkeypatch):
    monkeypatch.setattr(subscription_common, "STRIPE_WEBHOOK_SECRET", "whsec_test")
    assert subscription_common.verify_stripe_webhook_signature(b"{}", "t=1,v1=é") is False

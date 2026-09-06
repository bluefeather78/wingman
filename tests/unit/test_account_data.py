"""Unit tests for the self-serve data export (DATA_DELETION_EXPORT_PLAN.md, P0).

The two that matter most, because they are the risk of the whole feature:
  * the export never leaks a secret (allow-list redaction), and
  * the endpoint has no userid parameter, so a caller can only ever export THEIR OWN data.
"""
import inspect
import json

import pytest

import app.services.account_data as ad
import app.routes.account_data as route
from app.auth import AuthedUser, get_current_user


# A `users` row as get_user() returns it — every column, secrets included.
FULL_ROW = {
    "userid": "alice", "first_name": "Alice", "last_name": "Ng",
    "email": "alice@example.com", "location": "CA", "google_id": "g-123",
    "created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-02-01T00:00:00+00:00",
    "subscription_status": "trial", "trial_ends_at": "2026-01-08T00:00:00+00:00",
    "subscription_end_at": None, "promo_codes_used": ["BETAUSER"],
    "is_adult": False, "parental_consent": True,
    "terms_accepted_at": "2026-01-01T00:00:00+00:00",
    "privacy_accepted_at": "2026-01-01T00:00:00+00:00", "terms_version": "2026-09-06",
    "lifecycle_email_optout": False,
    "google_calendar_connected_at": "2026-01-02T00:00:00+00:00",
    "google_calendar_id": "cal-abc",
    "data": {"student-profile": {"synthesized": "loves robotics"},
             "hs-tracker-data": "{}", "hs-tracker-saved": {"x": True}},
    # --- everything below MUST NOT appear in the export ---
    "password_hash": "argon2$secret",
    "token_version": 3,
    "refresh_jtis": ["jti-1", "jti-2"],
    "google_calendar_access_token": "ya29.SECRET",
    "google_calendar_refresh_token": "1//REFRESH_SECRET",
    "google_calendar_token_expires_at": "2026-01-02T01:00:00+00:00",
    "stripe_customer_id": "cus_SECRET",
    "stripe_subscription_id": "sub_SECRET",
}

_SECRETS = ("argon2$secret", "ya29.SECRET", "1//REFRESH_SECRET", "cus_SECRET",
            "sub_SECRET", "jti-1", "jti-2")


def _stub_sources(monkeypatch, row=FULL_ROW, satellite=None):
    """get_user -> row; every satellite _supabase_request -> `satellite` (default None)."""
    monkeypatch.setattr(ad, "get_user", lambda uid: row)
    monkeypatch.setattr(ad, "_supabase_request",
                        lambda table, **kw: satellite if satellite is not None else None)


# ---------- redaction: the export leaks no secret ----------

def test_export_excludes_every_secret(monkeypatch):
    _stub_sources(monkeypatch)
    blob = json.dumps(ad.assemble_export("alice"))
    for secret in _SECRETS:
        assert secret not in blob, f"export leaked {secret!r}"


def test_export_account_block_is_allow_listed(monkeypatch):
    """A NEW secret column added to users later must be excluded by default."""
    row = dict(FULL_ROW, some_future_token="SHOULD_NOT_LEAK")
    _stub_sources(monkeypatch, row=row)
    export = ad.assemble_export("alice")
    assert "some_future_token" not in export["account"]
    assert "SHOULD_NOT_LEAK" not in json.dumps(export)


def test_export_surfaces_stripe_as_booleans_not_ids(monkeypatch):
    _stub_sources(monkeypatch)
    account = ad.assemble_export("alice")["account"]
    assert account["has_stripe_customer"] is True
    assert account["has_stripe_subscription"] is True
    assert "stripe_customer_id" not in account
    assert "stripe_subscription_id" not in account


def test_export_includes_the_personal_fields_and_app_data(monkeypatch):
    _stub_sources(monkeypatch)
    export = ad.assemble_export("alice")
    assert export["account"]["email"] == "alice@example.com"
    assert export["account"]["terms_version"] == "2026-09-06"
    assert export["app_data"]["student-profile"] == {"synthesized": "loves robotics"}


# ---------- missing-table-safe (user_events not built yet, §0.1) ----------

def test_satellites_are_missing_table_safe(monkeypatch):
    """Every satellite read returning None (missing table / unreachable) yields [] / an
    empty summary — never an error. This is what lets us ship before events capture exists."""
    _stub_sources(monkeypatch, satellite=None)
    export = ad.assemble_export("alice")
    assert export["activity"] == []
    assert export["behavioral_events"] == []
    assert export["emails_sent"] == []
    assert export["mailing_list_signups"] == []
    assert export["submitted_opportunities"] == []
    assert export["ai_usage"]["total_cost_usd"] == 0.0
    assert export["ai_usage"]["total_billed_calls"] == 0


# ---------- user_costs summarized ----------

def test_costs_are_summarized(monkeypatch):
    rows = [
        {"feature": "ranking", "cost_usd": "0.010000", "calls": 2},
        {"feature": "ranking", "cost_usd": "0.005000", "calls": 1},
        {"feature": "profile_chat", "cost_usd": "0.020000", "calls": 3},
    ]
    summary = ad._summarize_costs(rows)
    assert summary["total_cost_usd"] == pytest.approx(0.035)
    assert summary["total_billed_calls"] == 6
    assert summary["by_feature"]["ranking"]["calls"] == 3
    assert summary["by_feature"]["profile_chat"]["cost_usd"] == pytest.approx(0.02)


def test_paginated_read_stops_on_short_page(monkeypatch):
    """_read_user_rows keeps paging while a full page comes back, and stops on a short one —
    without looping forever if the backend keeps answering full pages it should terminate on
    the count check. Two pages: 1000 then 3."""
    calls = {"n": 0}
    def fake(table, params=None, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return [{"i": i} for i in range(1000)]
        return [{"i": 1}, {"i": 2}, {"i": 3}]
    monkeypatch.setattr(ad, "_supabase_request", fake)
    rows = ad._read_user_rows("user_events", "alice", "i")
    assert len(rows) == 1003
    assert calls["n"] == 2


# ---------- assemble_export: no account ----------

def test_export_none_for_missing_account(monkeypatch):
    monkeypatch.setattr(ad, "get_user", lambda uid: None)
    monkeypatch.setattr(ad, "_supabase_request", lambda *a, **k: None)
    assert ad.assemble_export("ghost") is None


# ---------- the route: identity comes only from the token ----------

def test_route_takes_no_userid_parameter():
    """The IDOR guarantee, asserted structurally: the handler's ONLY dependency is
    get_current_user and it accepts no request body — so there is nowhere for a caller to
    name another account. If someone adds a json_body/userid param, this fails."""
    params = list(inspect.signature(route.handle_account_export).parameters.values())
    assert len(params) == 1
    dep = params[0].default
    assert getattr(dep, "dependency", None) is get_current_user


def test_route_404_when_account_gone(monkeypatch):
    monkeypatch.setattr(route.account_export_limiter, "allow", lambda k: True)
    monkeypatch.setattr(route, "touch_user_activity", lambda *a, **k: None)
    monkeypatch.setattr(route, "assemble_export", lambda uid: None)
    resp = route.handle_account_export(user=AuthedUser(id="ghost"))
    assert resp.status_code == 404


def test_route_success_is_a_json_attachment(monkeypatch):
    monkeypatch.setattr(route.account_export_limiter, "allow", lambda k: True)
    monkeypatch.setattr(route, "touch_user_activity", lambda *a, **k: None)
    monkeypatch.setattr(route, "assemble_export", lambda uid: {"userid": uid, "account": {}})
    resp = route.handle_account_export(user=AuthedUser(id="alice"))
    assert resp.status_code == 200
    assert resp.media_type == "application/json"
    assert "attachment" in resp.headers["content-disposition"]
    assert json.loads(resp.body)["userid"] == "alice"


def test_route_429_when_rate_limited(monkeypatch):
    monkeypatch.setattr(route.account_export_limiter, "allow", lambda k: False)
    monkeypatch.setattr(route.account_export_limiter, "retry_after", lambda k: 42)
    called = {"assembled": False}
    def _assemble(uid):
        called["assembled"] = True
        return {}
    monkeypatch.setattr(route, "assemble_export", _assemble)
    resp = route.handle_account_export(user=AuthedUser(id="alice"))
    assert resp.status_code == 429
    assert resp.headers["Retry-After"] == "42"
    assert called["assembled"] is False        # denied BEFORE assembling anything

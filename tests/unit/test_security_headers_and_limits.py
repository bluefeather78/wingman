"""Security headers, Secure cookies, CORS and the remaining body caps — S1-5, M4 + M11.

There were no security headers at all: the only middleware on the app added cache-control.
CORS defaulted to "*" and render.yaml never set CORS_ALLOW_ORIGINS. The two OAuth state
cookies were httponly but not secure. And S0-2 capped only the AI proxies and the resume
upload — every other route read its body with no ceiling.
"""
import importlib
import json
import os

import pytest
from fastapi.testclient import TestClient

import app.core as core
import app.main as main
import app.routes.google_oauth as gr
import app.routes.user_data as ud


@pytest.fixture
def client():
    return TestClient(main.app, raise_server_exceptions=False)


# ---------------- headers ----------------

def test_the_baseline_headers_are_on_every_response(client):
    h = client.get("/api/opportunities").headers
    assert h["x-content-type-options"] == "nosniff"
    assert h["referrer-policy"] == "strict-origin-when-cross-origin"
    assert h["x-frame-options"] == "DENY"
    assert "camera=()" in h["permissions-policy"]


def test_hsts_is_sent_on_https_only(client):
    """Sending it on a plain-http dev response would pin localhost to https in the
    developer's browser — wrong, and persistent."""
    plain = client.get("/api/opportunities").headers
    assert "strict-transport-security" not in plain
    secure = client.get("/api/opportunities",
                        headers={"X-Forwarded-Proto": "https"}).headers
    assert secure["strict-transport-security"].startswith("max-age=63072000")


def test_https_is_read_from_the_forwarded_header_not_the_internal_hop(client):
    """request.url.scheme behind Render's proxy is the INTERNAL hop's scheme, which reads
    http in production — so it cannot be what decides."""
    h = client.get("/api/opportunities",
                   headers={"X-Forwarded-Proto": "https,http"}).headers
    assert "strict-transport-security" in h


def test_the_walkthrough_is_frameable_because_the_landing_page_iframes_it(client):
    """A blanket DENY would blank the film on the landing page."""
    assert client.get("/walkthrough.html").headers["x-frame-options"] == "SAMEORIGIN"
    assert client.get("/terms.html").headers["x-frame-options"] == "DENY"


def test_the_csp_ships_report_only_first(client):
    """expo export inlines @font-face rules and preload tags into the head; the policy
    needs one iteration against a real exported bundle before it enforces."""
    h = client.get("/api/opportunities").headers
    assert "content-security-policy-report-only" in h
    assert "content-security-policy" not in h


def test_the_csp_frame_ancestors_is_self_not_none(client):
    csp = client.get("/api/opportunities").headers["content-security-policy-report-only"]
    assert "frame-ancestors 'self'" in csp
    assert "object-src 'none'" in csp
    assert "base-uri 'self'" in csp


def test_csp_enforce_flips_the_header_name(monkeypatch):
    monkeypatch.setenv("CSP_ENFORCE", "1")
    reloaded = importlib.reload(main)
    try:
        h = TestClient(reloaded.app).get("/api/opportunities").headers
        assert "content-security-policy" in h
        assert "content-security-policy-report-only" not in h
    finally:
        monkeypatch.delenv("CSP_ENFORCE", raising=False)
        importlib.reload(main)


# ---------------- CORS ----------------

def test_cors_is_wide_open_off_render_because_dev_origins_are_not_knowable(monkeypatch):
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("CORS_ALLOW_ORIGINS", raising=False)
    reloaded = importlib.reload(main)
    try:
        assert reloaded._allow_origins == ["*"]
    finally:
        importlib.reload(main)


def test_cors_is_pinned_to_the_app_origins_on_render(monkeypatch):
    """The default was "*" and render.yaml never set CORS_ALLOW_ORIGINS, so production
    shipped wide open."""
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.delenv("CORS_ALLOW_ORIGINS", raising=False)
    reloaded = importlib.reload(main)
    try:
        assert "*" not in reloaded._allow_origins
        assert "https://highschoolwingman.com" in reloaded._allow_origins
    finally:
        monkeypatch.delenv("RENDER", raising=False)
        importlib.reload(main)


def test_an_explicit_setting_still_wins(monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://staging.example.com")
    reloaded = importlib.reload(main)
    try:
        assert reloaded._allow_origins == ["https://staging.example.com"]
    finally:
        monkeypatch.delenv("RENDER", raising=False)
        monkeypatch.delenv("CORS_ALLOW_ORIGINS", raising=False)
        importlib.reload(main)


# ---------------- cookies ----------------

class _Resp:
    def __init__(self):
        self.kw = None

    def set_cookie(self, name, value, **kw):
        self.kw = dict(kw, name=name, value=value)


class _Req:
    def __init__(self, host):
        self.headers = {"Host": host}


def test_the_state_cookie_is_secure_off_loopback():
    resp = _Resp()
    gr._state_cookie(resp, "google_oauth_state", "s", _Req("highschoolwingman.com"))
    assert resp.kw["secure"] is True
    assert resp.kw["httponly"] is True
    assert resp.kw["samesite"] == "lax"


def test_the_state_cookie_is_not_secure_on_loopback():
    """Unconditional `secure` breaks local dev over plain http: the browser silently drops
    the cookie and the callback then fails the CSRF check with no visible cause."""
    resp = _Resp()
    gr._state_cookie(resp, "google_oauth_state", "s", _Req("localhost:8000"))
    assert resp.kw["secure"] is False


def test_samesite_is_lax_not_strict():
    """strict would drop the cookie on the way back from Google, breaking sign-in."""
    resp = _Resp()
    gr._state_cookie(resp, "google_calendar_oauth_state", "s", _Req("example.com"))
    assert resp.kw["samesite"] == "lax"


# ---------------- body caps ----------------

def test_an_over_large_json_body_is_refused_before_the_handler(client):
    big = json.dumps({"key": "k", "value": "x" * (2 * 1024 * 1024)})
    res = client.post("/api/data/save", content=big,
                      headers={"Content-Type": "application/json"})
    assert res.status_code == 413


def test_the_cap_is_on_the_shared_dependency_so_new_routes_inherit_it():
    """Route-by-route capping is bounded by remembering; the dependency is not."""
    import app.deps as deps
    from app.config import JSON_MAX_BODY_BYTES
    assert deps._json_raw_body is not None
    assert JSON_MAX_BODY_BYTES > 0


def test_an_over_large_data_value_is_refused(monkeypatch):
    """The request cap bounds ONE request; users.data ACCUMULATES, and is read in full on
    every app open."""
    monkeypatch.setattr(ud, "touch_user_activity", lambda *a: None)
    monkeypatch.setattr(ud, "update_user_data", lambda *a: True)

    class _U:
        id = "alice"

    over = {"key": "hs-tracker-data",
            "value": "x" * (ud.USER_DATA_MAX_VALUE_BYTES + 10)}
    assert ud.handle_data_save(body=over, user=_U()).status_code == 413
    ok = {"key": "hs-tracker-data", "value": "x" * 100}
    assert ud.handle_data_save(body=ok, user=_U()).status_code == 200


def test_an_unserializable_data_value_is_a_400_not_a_500(monkeypatch):
    monkeypatch.setattr(ud, "touch_user_activity", lambda *a: None)

    class _U:
        id = "alice"

    resp = ud.handle_data_save(body={"key": "k", "value": {1, 2, 3}}, user=_U())
    assert resp.status_code == 400


# ---------------- event caps ----------------

@pytest.fixture
def _capture_events(monkeypatch):
    monkeypatch.setattr(core, "_events_available", True)
    monkeypatch.setattr(core, "_events_buffer", [])
    monkeypatch.setattr(core, "_start_events_flusher", lambda: None)
    monkeypatch.setattr(core, "_VALID_EVENT_ACTIONS", {"save"})
    return core._events_buffer


def test_a_batch_is_bounded(_capture_events, monkeypatch):
    events = [{"action": "save"}] * (core.EVENTS_MAX_PER_REQUEST + 50)
    assert core.record_user_events("alice", events) == core.EVENTS_MAX_PER_REQUEST


def test_an_oversized_context_is_dropped_not_truncated(_capture_events):
    """Half a context dict reads as real telemetry while being silently incomplete."""
    core.record_user_events("alice", [
        {"action": "save", "context": {"blob": "x" * (core.EVENT_MAX_CONTEXT_BYTES + 10)}}])
    assert core._events_buffer[0]["context"] == {"dropped": "context too large"}


def test_an_ordinary_context_is_kept(_capture_events):
    core.record_user_events("alice", [{"action": "save", "context": {"rank": 3}}])
    assert core._events_buffer[0]["context"] == {"rank": 3}


def test_an_unserializable_context_is_treated_as_too_large(_capture_events):
    core.record_user_events("alice", [{"action": "save", "context": {"s": {1, 2}}}])
    assert core._events_buffer[0]["context"] == {"dropped": "context too large"}

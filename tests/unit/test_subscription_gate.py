"""Unit tests for the subscription GATE — app.deps.require_subscription /
optional_subscribed_user, and the wiring that puts one of them on every route that is
"using the app".

The rule these enforce: once a trial or subscription ends the account keeps its session
but loses access to the app, not merely to the calls that cost money. subscription_state()
stays the single source of truth (test_subscription_state.py covers it); these tests cover
turning its verdict into a 402 and hanging that off the right routes.

No Supabase: the only network seam is get_user_account, monkeypatched on app.deps.
"""
import datetime
import inspect

import pytest
from fastapi import HTTPException

import app.deps as deps
from app.auth import AuthedUser
from app.routes import (account, auth, email, google_oauth, mailing_list, opportunities,
                        resume, subscription, user_data)


def _iso(delta_days):
    return (datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(days=delta_days)).isoformat()


def _account(record):
    return lambda _uid: record


ACTIVE = {"subscription_status": "active"}
EXPIRED_TRIAL = {"subscription_status": "trial", "trial_ends_at": _iso(-1)}
LIVE_TRIAL = {"subscription_status": "trial", "trial_ends_at": _iso(3)}


# ---------- require_subscription ----------

@pytest.mark.parametrize("record", [ACTIVE, LIVE_TRIAL])
def test_require_subscription_passes_user_through(monkeypatch, record):
    monkeypatch.setattr(deps, "get_user_account", _account(record))
    user = AuthedUser(id="alice")
    assert deps.require_subscription(user) is user


def test_require_subscription_402s_expired_trial(monkeypatch):
    monkeypatch.setattr(deps, "get_user_account", _account(EXPIRED_TRIAL))
    with pytest.raises(HTTPException) as exc:
        deps.require_subscription(AuthedUser(id="alice"))
    assert exc.value.status_code == 402
    # The detail IS the message the paywall screen shows — main.py renders it as
    # {"error": ...}, which is the shape the client parses.
    assert "free trial has ended" in exc.value.detail.lower()


def test_require_subscription_fails_open_when_supabase_is_down(monkeypatch):
    """A Supabase outage must not lock out every paying user — same choice
    subscription_block_reason already makes."""
    def boom(_uid):
        raise RuntimeError("supabase down")
    monkeypatch.setattr(deps, "get_user_account", boom)
    user = AuthedUser(id="alice")
    assert deps.require_subscription(user) is user


# ---------- optional_subscribed_user ----------

def test_optional_never_blocks_signed_out(monkeypatch):
    """No token, no account, nothing to have lapsed — the same unattributed residual the
    cost accounting reports."""
    def unexpected(_uid):
        raise AssertionError("must not look up an account for a signed-out caller")
    monkeypatch.setattr(deps, "get_user_account", unexpected)
    assert deps.optional_subscribed_user(None) is None


def test_optional_blocks_a_lapsed_signed_in_caller(monkeypatch):
    monkeypatch.setattr(deps, "get_user_account", _account(EXPIRED_TRIAL))
    with pytest.raises(HTTPException) as exc:
        deps.optional_subscribed_user(AuthedUser(id="alice"))
    assert exc.value.status_code == 402


def test_optional_passes_a_current_caller(monkeypatch):
    monkeypatch.setattr(deps, "get_user_account", _account(ACTIVE))
    user = AuthedUser(id="alice")
    assert deps.optional_subscribed_user(user) is user


# ---------- route wiring ----------
#
# The gate is only worth anything if it is actually ON the routes. These assert the wiring
# route by route, so removing a dependency (or adding a route with plain get_current_user)
# fails here rather than silently reopening the app to lapsed accounts.

GATED = {
    ("POST", "/api/data/save"),
    ("POST", "/api/data/load"),
    ("POST", "/api/account/location"),
    ("GET", "/api/opportunities"),
    ("GET", "/api/opportunities/{opp_id}/deadline"),
    # Gated for the same reason as the deadline check: the generate branch is a paid
    # model call. The read branch is free, but a route that is sometimes free is not a
    # route that may be left open.
    ("GET", "/api/opportunities/{opp_id}/action-items"),
    ("POST", "/api/opportunities/{opp_id}/subscribe"),
    ("GET", "/api/mailing-list/status"),
    ("GET", "/api/mailing-list/subscriptions"),
    ("POST", "/api/calendar/sync"),
    ("POST", "/api/user-submitted-opportunities"),
}

# Never gated: the ways OUT of the block, and the session lifecycle around them. A paywall
# you cannot pay through is a lockout.
UNGATED = {
    ("POST", "/api/subscription/status"),
    ("POST", "/api/subscription/checkout"),
    ("POST", "/api/subscription/cancel"),
    ("POST", "/api/subscription/redeem-promo"),
    ("POST", "/api/subscription/validate-promo"),
    ("POST", "/api/login"),
    ("POST", "/api/register"),
    ("POST", "/api/auth/refresh"),
    ("POST", "/api/auth/logout-all"),
    ("GET", "/api/email/unsubscribe"),
}

GATE_DEPENDENCIES = {deps.require_subscription, deps.optional_subscribed_user}


# Walk the routers themselves rather than app.main's FastAPI instance: recent FastAPI
# defers include_router into a wrapper, so app.routes does not list the real APIRoutes.
_ROUTE_MODULES = (account, auth, email, google_oauth, mailing_list, opportunities,
                  resume, subscription, user_data)


def _endpoint(method, path):
    for module in _ROUTE_MODULES:
        for route in module.router.routes:
            if getattr(route, "path", None) == path and method in getattr(route, "methods", ()):
                return route.endpoint
    raise AssertionError(f"no route registered for {method} {path}")


def _gate_dependencies(endpoint):
    return {p.default.dependency
            for p in inspect.signature(endpoint).parameters.values()
            if hasattr(p.default, "dependency")} & GATE_DEPENDENCIES


@pytest.mark.parametrize("method,path", sorted(GATED))
def test_route_is_subscription_gated(method, path):
    assert _gate_dependencies(_endpoint(method, path)), \
        f"{method} {path} must depend on require_subscription/optional_subscribed_user"


@pytest.mark.parametrize("method,path", sorted(UNGATED))
def test_route_is_not_gated(method, path):
    assert not _gate_dependencies(_endpoint(method, path)), \
        f"{method} {path} must stay reachable to a lapsed account"


def test_ai_routes_gate_by_hand():
    """The two AI proxies keep their inline subscription_block_reason call rather than the
    dependency: they must stay reachable signed-out for mock mode, and they return their
    402 as a json_error body built in the handler. Assert the call is still there."""
    import app.routes.ai as ai
    for fn in (ai.handle_messages, ai.handle_messages_claude):
        assert "subscription_block_reason" in inspect.getsource(fn)


def test_resume_routes_gate_by_hand():
    import app.routes.resume as resume
    for fn in (resume.handle_extract_from_resume, resume.handle_extract_from_linkedin):
        assert "subscription_block_reason" in inspect.getsource(fn)


# ---------- the wire format the client sees ----------

def test_402_detail_is_a_string_so_it_renders_as_error(monkeypatch):
    """main.py's HTTPException handler renders `detail` as {"error": detail} — the shape
    httpClient parses and shows on the paywall. A non-str detail would come back as the
    generic "Request failed.", losing the reason. (No TestClient here: this environment
    cannot open the socketpair its event loop needs.)"""
    import app.main as main

    monkeypatch.setattr(deps, "get_user_account", _account(EXPIRED_TRIAL))
    with pytest.raises(HTTPException) as exc:
        deps.require_subscription(AuthedUser(id="alice"))
    assert isinstance(exc.value.detail, str) and exc.value.detail
    assert main.http_exception_as_error  # the handler that turns it into {"error": ...}

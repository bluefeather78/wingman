"""Unit tests for the subscription GATE — app.deps.require_subscription /
optional_subscribed_user, and the wiring that keeps one of them on every route that is
"using the app".

Two-tier model (TWO_TIER_AI_PLAN.md §2b, §4.3): there is no app-access lockout anymore, so
the gate NEVER 402s — every signed-in account has at least metered Free access, and a lapsed
paid/comped account is just Free. The gate dependency is kept ON the routes deliberately: it
still enforces auth (a missing token is 401) and it is the one-place seam where a future
lockout would live, so the wiring tests still assert its presence. The per-user AI allowance
is a SEPARATE 429 gate at the AI route (step 4), not a 402 here.

No Supabase: subscription_block_reason short-circuits to None and no longer reads an account.
"""
import datetime
import inspect

import pytest
from fastapi import HTTPException

import app.deps as deps
from app.auth import AuthedUser
from app.routes import (account, account_data, auth, email, events, google_oauth,
                        mailing_list, opportunities, resume, subscription, user_data)


def _iso(delta_days):
    return (datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(days=delta_days)).isoformat()


def _account(record):
    return lambda _uid: record


ACTIVE = {"subscription_status": "active"}
EXPIRED_TRIAL = {"subscription_status": "trial", "trial_ends_at": _iso(-1)}
LIVE_TRIAL = {"subscription_status": "trial", "trial_ends_at": _iso(3)}


# ---------- require_subscription (never 402s now) ----------

@pytest.mark.parametrize("record", [ACTIVE, LIVE_TRIAL, EXPIRED_TRIAL])
def test_require_subscription_passes_user_through(monkeypatch, record):
    # Even a lapsed trial passes: no lockout, they are just Free.
    monkeypatch.setattr(deps, "get_user_subscription", _account(record))
    user = AuthedUser(id="alice")
    assert deps.require_subscription(user) is user


def test_require_subscription_does_not_read_supabase(monkeypatch):
    """No account read at all anymore: a signed-in caller always has access."""
    def unexpected(_uid):
        raise AssertionError("must not look up an account — nobody is locked out")
    monkeypatch.setattr(deps, "get_user_subscription", unexpected)
    user = AuthedUser(id="alice")
    assert deps.require_subscription(user) is user


# ---------- optional_subscribed_user ----------

def test_optional_never_blocks_signed_out(monkeypatch):
    def unexpected(_uid):
        raise AssertionError("must not look up an account for a signed-out caller")
    monkeypatch.setattr(deps, "get_user_subscription", unexpected)
    assert deps.optional_subscribed_user(None) is None


def test_optional_passes_a_lapsed_signed_in_caller(monkeypatch):
    # Was a 402 under the trial model; now a lapsed account is Free and passes through.
    monkeypatch.setattr(deps, "get_user_subscription", _account(EXPIRED_TRIAL))
    user = AuthedUser(id="alice")
    assert deps.optional_subscribed_user(user) is user


def test_optional_passes_a_current_caller(monkeypatch):
    monkeypatch.setattr(deps, "get_user_subscription", _account(ACTIVE))
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
    # Behavioral capture (P-A) is pure telemetry — it must stay reachable to a lapsed
    # account (we still record what they do) and never 402, so it uses get_optional_user,
    # not the subscription gate.
    ("POST", "/api/events"),
    # Data export + delete (DATA_DELETION_EXPORT_PLAN.md): a lapsed account must be able to
    # take a copy of, and delete, its own data — a paywall you can't export or delete through
    # is a data-hostage situation and the app stores reject it. Auth still required
    # (get_current_user, hard 401), plus a password re-auth on delete, but no subscription gate.
    ("POST", "/api/account/export"),
    ("POST", "/api/account/delete"),
    # Google-only delete re-auth start: authenticated (get_current_user) but NOT subscription
    # gated — a lapsed account must still be able to delete. The redirect/callback GETs use
    # nonces + a state cookie, no bearer, so they carry no gate dependency either.
    ("POST", "/api/account/reauth/google/start"),
    # Stripe calls this with a signature, not a bearer token — it must never require auth or
    # the subscription gate. Its events are what LIFT the block (write status='active'), so
    # gating it would make it unreachable exactly when it matters.
    ("POST", "/api/webhook/stripe"),
}

GATE_DEPENDENCIES = {deps.require_subscription, deps.optional_subscribed_user}


# Walk the routers themselves rather than app.main's FastAPI instance: recent FastAPI
# defers include_router into a wrapper, so app.routes does not list the real APIRoutes.
_ROUTE_MODULES = (account, account_data, auth, email, events, google_oauth, mailing_list,
                  opportunities, resume, subscription, user_data)


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


def test_ai_route_gates_by_hand():
    """The AI route gates by hand rather than with the dependency: the MOCK branch must stay
    reachable signed-out, and it returns its 401/402 as a json_error body built in the
    handler. One route since S1-1 (POST /api/ai), which picks the provider — and therefore
    which key the gate consults — from the server-side feature id."""
    import app.routes.ai as ai
    assert "_ai_access_error" in inspect.getsource(ai._serve_ai)
    assert "subscription_block_reason" in inspect.getsource(ai._ai_access_error)


# ---------- S0-1 / finding C1: the live AI branch requires a signed-in, subscribed caller ----------
#
# The bug this pins: get_optional_user never 401s and subscription_block_reason(None) returns
# None, so an anonymous POST reached the provider and billed a real call (verified live
# 2026-09-03). The fix keys on whether a KEY IS CONFIGURED, not on the route, so mock mode
# stays reachable signed-out — these three cases are exactly that distinction.

def test_ai_live_branch_401s_a_signed_out_caller(monkeypatch):
    import app.routes.ai as ai

    def unexpected(_uid):
        raise AssertionError("must not look up an account before the 401")
    monkeypatch.setattr(deps, "get_user_subscription", unexpected)
    denied = ai._ai_access_error(None, key_configured=True)
    assert denied is not None and denied.status_code == 401


def test_ai_live_branch_allows_a_lapsed_caller(monkeypatch):
    # Was a 402 under the trial model. Now a lapsed account is Free and reaches the live
    # branch; the daily allowance (step 4) is what limits it, with a 429, not a 402.
    import app.routes.ai as ai

    monkeypatch.setattr(deps, "get_user_subscription", _account(EXPIRED_TRIAL))
    assert ai._ai_access_error("alice", key_configured=True) is None


def test_ai_live_branch_allows_a_current_caller(monkeypatch):
    import app.routes.ai as ai

    monkeypatch.setattr(deps, "get_user_subscription", _account(LIVE_TRIAL))
    assert ai._ai_access_error("alice", key_configured=True) is None


def test_ai_mock_branch_stays_reachable_signed_out(monkeypatch):
    """CLAUDE.md's standing constraint: no API keys -> the app is still fully
    click-through-able. With no key configured there is nothing to spend, so a signed-out
    caller must NOT be 401'd."""
    import app.routes.ai as ai

    def unexpected(_uid):
        raise AssertionError("must not look up an account for a signed-out caller")
    monkeypatch.setattr(deps, "get_user_subscription", unexpected)
    assert ai._ai_access_error(None, key_configured=False) is None


def test_ai_mock_branch_allows_a_lapsed_caller(monkeypatch):
    """No lockout on either branch anymore: an identified lapsed account is Free, so the
    mock branch is reachable and returns no access error."""
    import app.routes.ai as ai

    monkeypatch.setattr(deps, "get_user_subscription", _account(EXPIRED_TRIAL))
    assert ai._ai_access_error("alice", key_configured=False) is None


def test_ai_handlers_consult_the_gate_before_spending(monkeypatch):
    """End-to-end through the handler: a signed-out call to a key-configured instance must
    return 401 without touching activity, the provider, or the mock generator."""
    import app.routes.ai as ai

    for attr in ("touch_user_activity", "_proxy_to_gemini", "_proxy_to_anthropic",
                 "_mock_response"):
        monkeypatch.setattr(ai, attr,
                            lambda *a, **k: pytest.fail("reached past the gate"))
    monkeypatch.setattr(ai, "GEMINI_API_KEY", "live-key")
    monkeypatch.setattr(ai, "ANTHROPIC_API_KEY", "live-key")
    monkeypatch.setattr(ai, "client_ip", lambda _r: "1.2.3.4")

    for feature in ("ranking", "profile_chat"):     # one Gemini, one Claude
        resp = ai._serve_ai(request=None,
                            raw_body=('{"feature":"%s"}' % feature).encode(), user=None)
        assert resp.status_code == 401


def test_resume_routes_gate_on_the_allowance():
    # Resume/LinkedIn imports are metered Free-tier actions and paid Claude calls, so they now
    # gate on the two-tier allowance (M11) rather than the old — now no-op — block reason.
    import app.routes.resume as resume
    for fn in (resume.handle_extract_from_resume, resume.handle_extract_from_linkedin):
        assert "ai_allowance_state" in inspect.getsource(fn)


# ---------- no lockout: the gate raises nothing for any account state ----------

def test_gate_raises_nothing_for_any_state(monkeypatch):
    """The subscription gate has no 402 path left — every signed-in account passes. (The AI
    allowance 429 lives at the AI route, not here.)"""
    for record in (ACTIVE, LIVE_TRIAL, EXPIRED_TRIAL,
                   {"subscription_status": "past_due"},
                   {"subscription_status": "canceled", "subscription_end_at": _iso(-1)}):
        monkeypatch.setattr(deps, "get_user_subscription", _account(record))
        user = AuthedUser(id="alice")
        assert deps.require_subscription(user) is user
        assert deps.optional_subscribed_user(user) is user

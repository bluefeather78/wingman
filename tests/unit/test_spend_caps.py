"""Unit tests for the three spend layers — S0-5 in SECURITY_HARDENING_PLAN.md, finding H4.

The hole these close: everything in this repo RECORDED spend and nothing read it back to
refuse a call. One 7-day trial account (which costs $0) could loop
GET /api/opportunities/<id>/deadline?refresh=1 across the catalog — refresh=1 bypassed the
7-day cache unconditionally at ~$0.07 a verified check, i.e. ~$90 per pass over 1,300 rows,
repeatable.

The three layers are tested separately because each covers a hole the others do not, and
they FAIL DIFFERENTLY on purpose: the per-user budget refuses that user (429), the circuit
breaker degrades the request for everyone (a working-but-dumber app is the right failure
direction for a billing incident).
"""
import pytest

import app.services.budget as budget
import app.routes.ai as ai


@pytest.fixture(autouse=True)
def _clean_budget_state(monkeypatch):
    """Each test starts with empty caches and a fresh cooldown window — these are
    process-global, so a leaked entry would make the next test read a stale total."""
    budget._cache.clear()
    monkeypatch.setattr(budget, "forced_recheck_limiter",
                        budget.RateLimiter(1, 3600))
    yield
    budget._cache.clear()


def _spend(monkeypatch, total):
    """Pin what user_costs reports. None means 'could not be read'."""
    monkeypatch.setattr(budget, "_sum_cost", lambda params: total)


# ---------- layer 1: the per-user daily budget ----------

def test_under_the_budget_is_not_blocked(monkeypatch):
    _spend(monkeypatch, 0.10)
    monkeypatch.setattr(budget, "USER_DAILY_BUDGET_USD", 0.50)
    assert budget.over_user_budget("alice") is None


def test_at_or_over_the_budget_returns_a_message(monkeypatch):
    _spend(monkeypatch, 0.50)
    monkeypatch.setattr(budget, "USER_DAILY_BUDGET_USD", 0.50)
    msg = budget.over_user_budget("alice")
    assert msg and "allowance" in msg.lower()
    # The overwhelming majority of anyone who sees this is a student who used the app hard,
    # not an attacker — and their data is untouched, which is the first thing they will fear.
    assert "stays put" in msg


def test_an_exempt_userid_bypasses_the_budget(monkeypatch):
    """The operator override the plan asks for: demos, and support cases where someone
    legitimately needs more."""
    _spend(monkeypatch, 99.0)
    monkeypatch.setattr(budget, "USER_DAILY_BUDGET_USD", 0.50)
    monkeypatch.setattr(budget, "BUDGET_EXEMPT_USERIDS", frozenset({"alice"}))
    assert budget.over_user_budget("alice") is None
    assert budget.over_user_budget("bob") is not None


def test_a_non_positive_budget_disables_the_layer(monkeypatch):
    _spend(monkeypatch, 99.0)
    monkeypatch.setattr(budget, "USER_DAILY_BUDGET_USD", 0.0)
    assert budget.over_user_budget("alice") is None


def test_an_unreadable_spend_total_fails_open(monkeypatch):
    """Same choice subscription_block_reason already makes: a Supabase blip must not lock out
    every paying user. It does mean the caps bound spend rather than enforcing access — the
    access control is S0-1's gate."""
    _spend(monkeypatch, None)
    monkeypatch.setattr(budget, "USER_DAILY_BUDGET_USD", 0.50)
    assert budget.over_user_budget("alice") is None


# ---------- the cache, and why note_spend exists ----------

def test_the_total_is_read_once_per_window(monkeypatch):
    calls = []

    def counting(params):
        calls.append(params)
        return 0.10

    monkeypatch.setattr(budget, "_sum_cost", counting)
    assert budget.user_spend_today("alice") == 0.10
    assert budget.user_spend_today("alice") == 0.10
    assert len(calls) == 1


def test_note_spend_makes_a_burst_see_its_own_spending(monkeypatch):
    """Without this, everything spent inside one TTL window is invisible to the check that is
    supposed to stop it — the exact shape of a burst attack."""
    monkeypatch.setattr(budget, "_sum_cost", lambda params: 0.40)
    monkeypatch.setattr(budget, "USER_DAILY_BUDGET_USD", 0.50)
    assert budget.over_user_budget("alice") is None
    budget.note_spend("alice", 0.15)
    assert budget.over_user_budget("alice") is not None


def test_note_spend_also_advances_the_global_total(monkeypatch):
    monkeypatch.setattr(budget, "_sum_cost", lambda params: 1.0)
    monkeypatch.setattr(budget, "GLOBAL_DAILY_BUDGET_USD", 2.0)
    assert budget.circuit_open() is False
    budget.note_spend("alice", 1.5)
    assert budget.circuit_open() is True


def test_a_stale_day_is_re_read_rather_than_carried_over(monkeypatch):
    """The first call after UTC midnight must not carry yesterday's total into a fresh
    budget — the entry stores the day it was read for."""
    monkeypatch.setattr(budget, "_sum_cost", lambda params: 0.0)
    budget.user_spend_today("alice")
    total, read_at, _day = budget._cache["alice"]
    budget._cache["alice"] = (9.99, read_at, "1999-01-01")
    assert budget.user_spend_today("alice") == 0.0


def test_note_spend_ignores_junk():
    budget._cache["alice"] = (1.0, 0.0, budget._today())
    budget.note_spend("alice", None)
    budget.note_spend("alice", "not a number")
    budget.note_spend("alice", -5)
    assert budget._cache["alice"][0] == 1.0


# ---------- layer 2: the forced-recheck cooldown ----------

def test_a_second_forced_recheck_of_the_same_row_is_refused():
    assert budget.forced_recheck_ok("alice", "opp-1") is True
    assert budget.forced_recheck_ok("alice", "opp-1") is False


def test_the_cooldown_is_per_row_and_per_user():
    """Per-row, or one student's refresh would block another row they also track; per-user, or
    one student would block the whole cohort from ever forcing a check."""
    assert budget.forced_recheck_ok("alice", "opp-1") is True
    assert budget.forced_recheck_ok("alice", "opp-2") is True
    assert budget.forced_recheck_ok("bob", "opp-1") is True
    assert budget.forced_recheck_ok("alice", "opp-1") is False


def test_the_cooldown_reports_a_retry_after():
    budget.forced_recheck_ok("alice", "opp-1")
    assert budget.forced_recheck_retry_after("alice", "opp-1") >= 1


def test_a_non_positive_cooldown_disables_the_layer(monkeypatch):
    monkeypatch.setattr(budget, "FORCED_RECHECK_MAX_PER_WINDOW", 0)
    for _ in range(5):
        assert budget.forced_recheck_ok("alice", "opp-1") is True


# ---------- layer 3: the global circuit breaker ----------

def test_the_circuit_opens_at_the_global_ceiling(monkeypatch):
    monkeypatch.setattr(budget, "GLOBAL_DAILY_BUDGET_USD", 25.0)
    _spend(monkeypatch, 24.99)
    assert budget.circuit_open() is False
    budget._cache.clear()
    _spend(monkeypatch, 25.0)
    assert budget.circuit_open() is True


def test_the_circuit_fails_closed_shut_when_spend_is_unreadable(monkeypatch):
    """'Closed' as in the breaker stays closed and current flows: an unreadable total must not
    degrade the whole app to mock on a database blip."""
    monkeypatch.setattr(budget, "GLOBAL_DAILY_BUDGET_USD", 1.0)
    _spend(monkeypatch, None)
    assert budget.circuit_open() is False


def test_a_non_positive_global_budget_disables_the_layer(monkeypatch):
    monkeypatch.setattr(budget, "GLOBAL_DAILY_BUDGET_USD", 0.0)
    _spend(monkeypatch, 999.0)
    assert budget.circuit_open() is False


# ---------- how the AI proxies apply the two layers that reach them ----------

def test_proxy_refuses_a_user_over_budget(monkeypatch):
    monkeypatch.setattr(ai.budget, "circuit_open", lambda: False)
    monkeypatch.setattr(ai.budget, "over_user_budget", lambda uid: "no more today")
    live, refused = ai._live_branch("alice", key_configured=True)
    assert live is False
    assert refused is not None and refused.status_code == 429


def test_proxy_degrades_to_mock_when_the_circuit_is_open(monkeypatch):
    """Degrade, don't error: the mock branch already exists and is exercised offline every
    day, so it is a known-good reduced mode rather than a new failure path."""
    monkeypatch.setattr(ai.budget, "circuit_open", lambda: True)
    monkeypatch.setattr(ai.budget, "over_user_budget",
                        lambda uid: pytest.fail("per-user budget is moot once the app is "
                                                "already degraded for everyone"))
    live, refused = ai._live_branch("alice", key_configured=True)
    assert live is False and refused is None


def test_the_circuit_does_not_relax_the_signed_out_401(monkeypatch):
    """Degrading is a spend decision, not an access decision. The key is still configured, so
    an anonymous caller is still refused — otherwise the circuit breaker would REOPEN the C1
    hole S0-1 just closed."""
    monkeypatch.setattr(ai.budget, "circuit_open", lambda: True)
    denied = ai._ai_access_error(None, key_configured=True)
    assert denied is not None and denied.status_code == 401


def test_live_branch_passes_a_user_in_good_standing(monkeypatch):
    monkeypatch.setattr(ai.budget, "circuit_open", lambda: False)
    monkeypatch.setattr(ai.budget, "over_user_budget", lambda uid: None)
    assert ai._live_branch("alice", key_configured=True) == (True, None)


def test_no_key_means_no_spend_checks_at_all(monkeypatch):
    monkeypatch.setattr(ai.budget, "circuit_open",
                        lambda: pytest.fail("nothing to spend without a key"))
    assert ai._live_branch("alice", key_configured=False) == (False, None)


# ---------- the routes that were the exploit ----------

def test_action_items_degrades_instead_of_generating(monkeypatch):
    """allow_paid=False must take resolve()'s existing no-API-key path rather than a second,
    parallel fallback that could drift from it."""
    import app.services.action_items as svc

    monkeypatch.setattr(svc, "get_opportunity_for_action_items",
                        lambda _id: {"id": "x", "name": "N", "action_items": None})
    monkeypatch.setattr(svc, "ANTHROPIC_API_KEY", "live-key")
    monkeypatch.setattr(svc, "process_one",
                        lambda *a, **k: pytest.fail("must not call a model"))

    payload, cost = svc.resolve("x", allow_paid=False)
    assert cost == 0.0
    assert payload["source"] == "generic-fallback"


def test_action_items_keeps_a_stored_list_when_degraded(monkeypatch):
    """Never replace what the batch verified with generic items just because we are degraded."""
    import app.services.action_items as svc

    monkeypatch.setattr(svc, "get_opportunity_for_action_items", lambda _id: {
        "id": "x", "name": "N", "action_items": [{"task": "Apply"}],
        "action_items_source": "page", "action_items_checked_at": None})
    monkeypatch.setattr(svc, "ANTHROPIC_API_KEY", "live-key")
    monkeypatch.setattr(svc, "process_one",
                        lambda *a, **k: pytest.fail("must not call a model"))

    payload, cost = svc.resolve("x", allow_paid=False)
    assert cost == 0.0 and payload["source"] == "page"


def test_the_deadline_route_charges_the_cooldown_only_for_a_real_bypass():
    """A stale row would be re-checked by any passive load anyway, so charging the cooldown
    for it would penalise normal use and stop nothing. Pin the condition itself."""
    import inspect
    import app.routes.opportunities as opps

    src = inspect.getsource(opps.handle_deadline_check)
    assert "if fresh and force and not budget.forced_recheck_ok" in src
    assert "budget.over_user_budget" in src
    assert "budget.circuit_open" in src


def test_recording_a_cost_advances_the_budget_counters(monkeypatch):
    """The wiring that makes layers 1 and 3 see spend as it happens: record_user_cost bumps
    the in-process totals before any of its own early returns, because the money was spent
    whether or not we manage to write it down."""
    import app.core as core

    seen = []
    monkeypatch.setattr(budget, "note_spend", lambda uid, cost: seen.append((uid, cost)))
    monkeypatch.setattr(core, "_user_costs_available", False)   # force an early return
    core.record_user_cost("alice", "gemini", "ranking", cost=0.02)
    assert seen == [("alice", 0.02)]

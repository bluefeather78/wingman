"""Unit tests for app.services.budget.user_requests_today / _sum_calls (TWO_TIER_AI_PLAN.md
step 1 — the read-only request-count instrumentation behind the Free-tier allowance).

No Supabase: the only seam is _supabase_request, monkeypatched. SUPABASE_URL/_SERVICE_KEY are
module globals in budget (imported by bare name from app.config), so they are patched there.
The in-process cache is cleared per test, and distinct userids avoid cross-test bleed.
"""
import pytest

from app.services import budget


@pytest.fixture(autouse=True)
def _clear_cache_and_keys(monkeypatch):
    monkeypatch.setattr(budget, "SUPABASE_URL", "http://supabase.test", raising=False)
    monkeypatch.setattr(budget, "SUPABASE_SERVICE_KEY", "svc", raising=False)
    with budget._lock:
        budget._cache.clear()
    yield
    with budget._lock:
        budget._cache.clear()


def test_sum_calls_single_page(monkeypatch):
    monkeypatch.setattr(budget, "_supabase_request",
                        lambda table, params: [{"calls": 3}, {"calls": 2}, {"calls": 0}])
    assert budget.user_requests_today("alice") == 5


def test_sum_calls_none_when_unreadable(monkeypatch):
    # None (not 0) means "unknown" -> callers fail open. Distinct from a real empty read.
    monkeypatch.setattr(budget, "_supabase_request", lambda table, params: None)
    assert budget.user_requests_today("bob") is None


def test_sum_calls_empty_is_zero(monkeypatch):
    monkeypatch.setattr(budget, "_supabase_request", lambda table, params: [])
    assert budget.user_requests_today("carol") == 0


def test_sum_calls_handles_null_calls(monkeypatch):
    monkeypatch.setattr(budget, "_supabase_request",
                        lambda table, params: [{"calls": None}, {"calls": 4}])
    assert budget.user_requests_today("dave") == 4


def test_no_keys_returns_none(monkeypatch):
    monkeypatch.setattr(budget, "SUPABASE_URL", "", raising=False)
    monkeypatch.setattr(budget, "_supabase_request",
                        lambda table, params: (_ for _ in ()).throw(AssertionError("no call")))
    assert budget.user_requests_today("erin") is None


def test_blank_userid_returns_none():
    assert budget.user_requests_today("") is None
    assert budget.user_requests_today(None) is None


def test_result_is_cached(monkeypatch):
    calls = {"n": 0}

    def counting(table, params):
        calls["n"] += 1
        return [{"calls": 7}]

    monkeypatch.setattr(budget, "_supabase_request", counting)
    assert budget.user_requests_today("frank") == 7
    assert budget.user_requests_today("frank") == 7  # served from cache
    assert calls["n"] == 1


def test_calls_cache_separate_from_cost_cache(monkeypatch):
    # A cost read for the same user must not be served the call-count, or vice versa.
    monkeypatch.setattr(budget, "_supabase_request",
                        lambda table, params: [{"calls": 9, "cost_usd": 1.23}])
    assert budget.user_requests_today("grace") == 9
    assert budget.global_spend_today() == 1.23


def test_sum_calls_paginates(monkeypatch):
    full = [{"calls": 1}] * budget._PAGE
    pages = [full, [{"calls": 1}, {"calls": 1}]]  # 1000 + 2 across two pages

    def paged(table, params):
        idx = int(params["offset"]) // budget._PAGE
        return pages[idx] if idx < len(pages) else []

    monkeypatch.setattr(budget, "_supabase_request", paged)
    assert budget.user_requests_today("heidi") == budget._PAGE + 2

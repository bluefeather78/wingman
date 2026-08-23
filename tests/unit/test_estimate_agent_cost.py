"""Unit tests for ops.core cost helpers.

  * estimate_agent_cost — the figure operators authorise paid runs against. The historic
    under-quote bug came from NOT excluding failed runs and snapshot-commit rows from the
    average; those exclusions get dedicated proofs here.
  * _group_untracked_models / _group_untracked_feature_models — fold blank-model rows into
    one 'Other' bucket sorted last.

recent_runs() is monkeypatched so nothing touches Supabase. estimate_agent_cost also reads
agent_defaults() (a local JSON file / built-in defaults), which is offline and left real.
"""
import datetime

import pytest

import ops.core as opscore


@pytest.fixture(autouse=True)
def _reset_runs_cache():
    """estimate_agent_cost goes through the monkeypatched recent_runs, but reset the shared
    cache anyway so no test leaks rows into another via ops.core's module globals."""
    with opscore._runs_cache_lock:
        opscore._runs_cache["at"] = 0.0
        opscore._runs_cache["rows"] = []
    yield
    with opscore._runs_cache_lock:
        opscore._runs_cache["at"] = 0.0
        opscore._runs_cache["rows"] = []


def _iso(dt):
    return dt.isoformat()


def _run(agent="review_checker", *, cost_usd=1.0, items=100, errors=0,
         finished=True, mode="live", minutes=10):
    """A minimal agent_runs row shaped the way estimate_agent_cost / _run_status read it."""
    start = datetime.datetime(2026, 8, 20, 12, 0, 0, tzinfo=datetime.timezone.utc)
    end = start + datetime.timedelta(minutes=minutes)
    return {
        "agent": agent,
        "cost_usd": cost_usd,
        "items_processed": items,
        "errors": errors,
        "mode": mode,
        "started_at": _iso(start),
        "finished_at": _iso(end) if finished else None,
    }


def _patch_runs(monkeypatch, rows):
    monkeypatch.setattr(opscore, "recent_runs", lambda force=False: rows)


# ===========================================================================
# estimate_agent_cost — the `free` short-circuit.
# ===========================================================================
def test_free_agent_short_circuits_to_exact_zero(monkeypatch):
    """A free agent's $0.00 is a fact about its design (free is not an absence): cost
    fields are 0.0, provisional is False, free is True — regardless of history."""
    _patch_runs(monkeypatch, [_run(agent="link_checker", cost_usd=0.0)])
    est = opscore.estimate_agent_cost("links", 1000)
    assert est["free"] is True
    assert est["provisional"] is False
    assert est["est_cost_usd"] == 0.0
    assert est["est_cost_per_item"] == 0.0
    assert est["est_cost_low_usd"] == 0.0
    assert est["est_cost_high_usd"] == 0.0
    assert est["based_on_runs"] == 0
    assert est["est_seconds"]  # a positive wall-time estimate, not None


# ===========================================================================
# estimate_agent_cost — averaging, spread, provisional.
# ===========================================================================
def test_average_and_spread_over_clean_runs(monkeypatch):
    # rate 0.01 and rate 0.02 -> mean 0.015, low 0.01, high 0.02
    rows = [
        _run(cost_usd=1.0, items=100),   # 0.01/item
        _run(cost_usd=4.0, items=200),   # 0.02/item
    ]
    _patch_runs(monkeypatch, rows)
    est = opscore.estimate_agent_cost("reviews", 1000)
    assert est["est_cost_per_item"] == pytest.approx(0.015)
    assert est["est_cost_usd"] == pytest.approx(0.015 * 1000, rel=1e-6)
    assert est["est_cost_low_usd"] == pytest.approx(0.01 * 1000)
    assert est["est_cost_high_usd"] == pytest.approx(0.02 * 1000)
    assert est["based_on_runs"] == 2
    assert est["provisional"] is True   # n < 3


def test_provisional_false_with_three_or_more_clean_runs(monkeypatch):
    rows = [_run(cost_usd=1.0, items=100) for _ in range(3)]
    _patch_runs(monkeypatch, rows)
    est = opscore.estimate_agent_cost("reviews", 10)
    assert est["based_on_runs"] == 3
    assert est["provisional"] is False


# ===========================================================================
# estimate_agent_cost — the exclusions that fixed the under-quote bug.
# ===========================================================================
def test_failed_run_is_excluded_from_the_average(monkeypatch):
    """A failed run (errors>0) counted every row it touched but errored out before paying
    for most of them, so it lands as an implausibly cheap per-item rate. It must NOT drag
    the mean down."""
    clean = _run(cost_usd=1.0, items=100)                 # 0.01/item
    failed = _run(cost_usd=0.001, items=100, errors=3)    # 0.00001/item — excluded
    _patch_runs(monkeypatch, [clean, failed])
    est = opscore.estimate_agent_cost("reviews", 1000)
    # Only the clean run survives, so the rate is exactly its 0.01 — not pulled toward 0.
    assert est["est_cost_per_item"] == pytest.approx(0.01)
    assert est["based_on_runs"] == 1


def test_snapshot_commit_run_is_excluded_from_the_average(monkeypatch):
    """snapshot-commit rows carry real item counts against cost_usd = 0 by construction
    (the dry run already paid), so averaging one in is averaging in a free run."""
    clean = _run(cost_usd=2.0, items=100)                        # 0.02/item
    commit = _run(cost_usd=0.0, items=500, mode="snapshot-commit")
    _patch_runs(monkeypatch, [clean, commit])
    est = opscore.estimate_agent_cost("reviews", 1000)
    assert est["est_cost_per_item"] == pytest.approx(0.02)
    assert est["based_on_runs"] == 1


def test_unfinished_run_is_excluded(monkeypatch):
    clean = _run(cost_usd=1.0, items=100)
    running = _run(cost_usd=0.5, items=100, finished=False)
    _patch_runs(monkeypatch, [clean, running])
    est = opscore.estimate_agent_cost("reviews", 100)
    assert est["based_on_runs"] == 1


def test_zero_item_and_null_cost_runs_are_excluded(monkeypatch):
    clean = _run(cost_usd=1.0, items=100)
    zero_items = _run(cost_usd=1.0, items=0)
    null_cost = _run(cost_usd=None, items=100)
    _patch_runs(monkeypatch, [clean, zero_items, null_cost])
    est = opscore.estimate_agent_cost("reviews", 100)
    assert est["based_on_runs"] == 1


def test_rows_from_other_agents_are_ignored(monkeypatch):
    """Only rows whose agent matches this agent's db_agent count."""
    mine = _run(agent="review_checker", cost_usd=1.0, items=100)
    other = _run(agent="scraper", cost_usd=99.0, items=1)
    _patch_runs(monkeypatch, [mine, other])
    est = opscore.estimate_agent_cost("reviews", 100)
    assert est["based_on_runs"] == 1
    assert est["est_cost_per_item"] == pytest.approx(0.01)


# ===========================================================================
# estimate_agent_cost — empty history.
# ===========================================================================
def test_empty_history_returns_all_none_cost_fields(monkeypatch):
    _patch_runs(monkeypatch, [])
    est = opscore.estimate_agent_cost("reviews", 1000)
    assert est["est_cost_usd"] is None
    assert est["est_cost_per_item"] is None
    assert est["est_cost_low_usd"] is None
    assert est["est_cost_high_usd"] is None
    assert est["based_on_runs"] == 0
    assert est["provisional"] is True   # 0 < 3
    # Wall time still falls back to the configured delay (reviews default min_delay = 5).
    assert est["est_seconds"] == 5 * 1000


# ===========================================================================
# _group_untracked_models
# ===========================================================================
def _model_row(model, cost, calls, users=1, provider="google"):
    return {"model": model, "cost_usd": cost, "calls": calls, "users": users,
            "provider": provider, "input_tokens": 0, "output_tokens": 0,
            "web_searches": 0}


def test_group_untracked_models_no_blank_rows_passthrough():
    rows = [_model_row("gemini-3.6-flash", 1.0, 10)]
    assert opscore._group_untracked_models(rows) == rows


def test_group_untracked_models_folds_blank_and_legacy_label():
    """Both '' and '(before model tracking)' are absences and fold into one Other bucket."""
    named = _model_row("gemini-3.6-flash", 1.0, 10)
    blank1 = _model_row("", 0.10, 5, users=3, provider="google")
    blank2 = _model_row("(before model tracking)", 0.09, 4, users=7, provider="anthropic")
    out = opscore._group_untracked_models([named, blank1, blank2])
    assert len(out) == 2
    # Named row first, Other bucket appended last regardless of cost.
    assert out[0] == named
    other = out[-1]
    assert other["key"] == opscore.UNTRACKED_MODEL_KEY
    assert other["model"] == opscore.UNTRACKED_MODEL_LABEL
    assert other["untracked"] is True
    assert other["cost_usd"] == pytest.approx(0.19)
    assert other["calls"] == 9
    # users is the MAX across folded rows, never the sum.
    assert other["users"] == 7
    assert other["cost_per_call"] == pytest.approx(round(0.19 / 9, 6))
    # both providers preserved for the tooltip
    assert set(other["providers"]) == {
        opscore.PROVIDER_LABELS["google"], opscore.PROVIDER_LABELS["anthropic"]}


def test_group_untracked_models_sorts_other_last_even_when_costliest():
    named = _model_row("gemini-3.6-flash", 0.01, 1)
    blank = _model_row("", 100.0, 1)   # far more expensive, still sorts last
    out = opscore._group_untracked_models([named, blank])
    assert out[-1]["untracked"] is True
    assert out[0]["model"] == "gemini-3.6-flash"


def test_group_untracked_models_divide_by_zero_guard():
    """A blank bucket with zero calls must not raise — cost_per_call falls back to 0.0."""
    blank = _model_row("", 0.0, 0, users=0)
    out = opscore._group_untracked_models([blank])
    assert len(out) == 1
    assert out[0]["cost_per_call"] == 0.0


# ===========================================================================
# _group_untracked_feature_models
# ===========================================================================
def test_group_untracked_feature_models_passthrough_sorted_by_cost():
    entries = [
        {"model": "gemini-3.6-flash", "cost_usd": 1.0, "calls": 5},
        {"model": "claude-haiku-4-5", "cost_usd": 3.0, "calls": 2},
    ]
    out = opscore._group_untracked_feature_models(entries)
    # named rows sorted by cost descending
    assert [m["model"] for m in out] == ["claude-haiku-4-5", "gemini-3.6-flash"]


def test_group_untracked_feature_models_folds_blank_last():
    entries = [
        {"model": "gemini-3.6-flash", "cost_usd": 1.0, "calls": 5},
        {"model": "", "cost_usd": 0.10, "calls": 2},
        {"model": "", "cost_usd": 0.05, "calls": 1},
    ]
    out = opscore._group_untracked_feature_models(entries)
    assert len(out) == 2
    other = out[-1]
    assert other["untracked"] is True
    assert other["model"] == opscore.UNTRACKED_MODEL_LABEL
    assert other["cost_usd"] == pytest.approx(0.15)
    assert other["calls"] == 3


def test_group_untracked_feature_models_no_blank_passthrough():
    entries = [{"model": "gemini-3.6-flash", "cost_usd": 1.0, "calls": 5}]
    out = opscore._group_untracked_feature_models(entries)
    assert len(out) == 1
    assert out[0]["model"] == "gemini-3.6-flash"

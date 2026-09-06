"""Unit tests for the AI-tiers block in ops.core.get_user_metrics
(TWO_TIER_AI_PLAN.md §5-6 — step 1, read-only instrumentation).

The Supabase fetchers are monkeypatched, so no network: accounts, per-user spend and the
activity surfaces are injected directly and the assembled `tiers` block is asserted.
"""
import datetime

import pytest

import ops.core as core


def _iso(delta_days):
    return (datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(days=delta_days)).isoformat()


def _account(uid, status, **extra):
    r = {"userid": uid, "first_name": uid, "last_name": "T",
         "email": f"{uid}@x.co", "created_at": _iso(-3), "data": {},
         "subscription_status": status}
    r.update(extra)
    return r


@pytest.fixture
def patched(monkeypatch):
    """A world with 2 paid + 2 free accounts and a fixed latest spend day."""
    latest = datetime.date.today().isoformat()
    accounts = [
        _account("alice", "active"),                                   # paid
        _account("bob", "beta", subscription_end_at=_iso(5)),          # paid
        _account("carol", "trial", trial_ends_at=_iso(3)),            # free
        _account("dave", "past_due"),                                  # free
    ]
    # carol used 8 requests, dave 2, on the latest day.
    day_calls = {"carol": {latest: 8}, "dave": {latest: 2}}
    spend = {"carol": {"cost_usd": 0.1, "calls": 8}, "dave": {"cost_usd": 0.02, "calls": 2}}

    monkeypatch.setattr(core, "_fetch_all_accounts", lambda: accounts)
    monkeypatch.setattr(core, "_fetch_user_spend",
                        lambda since: (spend, set(), True, day_calls, latest))
    monkeypatch.setattr(core, "_fetch_mailing_list_users", lambda: set())
    monkeypatch.setattr(core, "fetch_user_activity", lambda since: {})
    monkeypatch.setattr(core, "_activity_first_day", lambda: None)
    monkeypatch.setattr(core, "flush_user_activity", lambda: None)
    monkeypatch.setattr(core, "record_metrics_snapshot", lambda m: None)
    # No cap hits recorded yet (the gate that writes them is a later step).
    monkeypatch.setattr(core, "_fetch_activity_surface", lambda surface, since: {})
    return latest


def test_tier_counts_and_rosters(patched):
    tiers = core.get_user_metrics()["tiers"]
    assert tiers["paid"] == 2
    assert tiers["free"] == 2
    assert set(tiers["paid_userids"]) == {"alice", "bob"}
    assert set(tiers["free_userids"]) == {"carol", "dave"}


def test_tiers_report_config_and_not_enforced(patched):
    tiers = core.get_user_metrics()["tiers"]
    assert tiers["allowance"] == core.FREE_TIER_DAILY_AI_ACTIONS
    assert tiers["first_day_allowance"] == core.FIRST_DAY_AI_ACTIONS
    assert tiers["enforced"] is False
    assert tiers["unit"] == "requests"


def test_usage_histogram_buckets_free_users_by_requests(patched):
    usage = core.get_user_metrics()["tiers"]["usage"]
    by_label = {b["label"]: b for b in usage["histogram"]}
    # dave=2 (<50% of 10) and carol=8 (80-99% of 10).
    assert "dave" in by_label["0-50%"]["userids"]
    assert by_label["0-50%"]["count"] == 1
    assert "carol" in by_label["80-99%"]["userids"]
    assert by_label["80-99%"]["count"] == 1
    assert by_label["100%+"]["count"] == 0


def test_usage_percentiles_ignore_zero_users(patched):
    usage = core.get_user_metrics()["tiers"]["usage"]
    assert usage["active_free"] == 2                 # carol + dave both used AI
    assert usage["median_requests"] in (2, 8)        # percentile picks an actual value
    assert usage["p90_requests"] == 8
    assert usage["latest_day"] == patched


def test_cap_hits_plumbing_empty_but_ready(patched):
    cap = core.get_user_metrics()["tiers"]["cap_hits"]
    assert cap["ready"] is True      # activity table present
    assert cap["count"] == 0         # nothing writes ai_limit_hit yet
    assert cap["day"] is None
    assert cap["userids"] == []


def test_cap_hits_counts_recorded_surface(patched, monkeypatch):
    day = datetime.date.today().isoformat()
    monkeypatch.setattr(core, "_fetch_activity_surface",
                        lambda surface, since: {"carol": {day}} if surface == "ai_limit_hit" else {})
    cap = core.get_user_metrics()["tiers"]["cap_hits"]
    assert cap["ready"] is True
    assert cap["count"] == 1
    assert cap["day"] == day
    assert cap["userids"] == ["carol"]


def test_cap_hits_not_ready_when_activity_absent(patched, monkeypatch):
    monkeypatch.setattr(core, "_fetch_activity_surface", lambda surface, since: None)
    cap = core.get_user_metrics()["tiers"]["cap_hits"]
    assert cap["ready"] is False
    assert cap["count"] == 0


def test_shaped_users_carry_ai_tier(patched):
    users = {u["userid"]: u for u in core.get_user_metrics()["users"]}
    assert users["alice"]["ai_tier"] == "paid"
    assert users["carol"]["ai_tier"] == "free"

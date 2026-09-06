"""Unit tests for the two-tier AI allowance engine (MARQUEE M11) in app.services.budget:
action classing + collapse, user_actions_today, and ai_allowance_state.

No Supabase: get_user_subscription is monkeypatched on the budget module. The action ledger
is process-global, so it is cleared per test.
"""
import datetime

import pytest

from app.services import budget


def _iso(delta_days):
    return (datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(days=delta_days)).isoformat()


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    with budget._action_lock:
        budget._action_ledger.clear()
    # Default: a plain Free account, enforcement ON (tests that want observe mode flip it off
    # explicitly).
    monkeypatch.setattr(budget, "get_user_subscription",
                        lambda uid: {"subscription_status": "free", "created_at": _iso(-10)})
    monkeypatch.setattr(budget, "FREE_TIER_AI_GATE_ENFORCED", True)
    monkeypatch.setattr(budget, "FREE_TIER_DAILY_AI_ACTIONS", 10)
    monkeypatch.setattr(budget, "FIRST_DAY_AI_ACTIONS", 20)
    yield
    with budget._action_lock:
        budget._action_ledger.clear()


# ---------- action classing ----------

def test_action_class_mapping():
    assert budget.action_class_for("profile_chat") == "profile"
    assert budget.action_class_for("ranking") == "find_matches"
    assert budget.action_class_for("tracker_extract") == "track"
    assert budget.action_class_for("deadline_check") == "deadline"
    assert budget.action_class_for("resume_import") == "resume"
    assert budget.action_class_for("action_items") is None      # not a metered action
    assert budget.action_class_for("nope") is None


# ---------- collapse vs per-call ----------

def test_collapse_class_burst_is_one_action():
    # A profile chat session's several calls within one window collapse to 1.
    for _ in range(6):
        budget.note_action("alice", "profile_chat")
    assert budget.user_actions_today("alice") == 1


def test_different_collapse_classes_count_separately():
    budget.note_action("alice", "profile_chat")     # profile
    budget.note_action("alice", "ranking")          # find_matches
    assert budget.user_actions_today("alice") == 2


def test_per_call_class_counts_each():
    budget.note_action("alice", "tracker_extract")
    budget.note_action("alice", "tracker_extract")
    budget.note_action("alice", "tracker_extract")
    assert budget.user_actions_today("alice") == 3


def test_unmetered_feature_does_not_count():
    budget.note_action("alice", "action_items")
    assert budget.user_actions_today("alice") == 0


def test_actions_are_per_user():
    budget.note_action("alice", "tracker_extract")
    assert budget.user_actions_today("bob") == 0


def test_blank_user_is_zero():
    assert budget.user_actions_today("") == 0
    assert budget.user_actions_today(None) == 0


# ---------- ai_allowance_state: tiers ----------

def test_paid_is_unlimited(monkeypatch):
    monkeypatch.setattr(budget, "get_user_subscription",
                        lambda uid: {"subscription_status": "active"})
    st = budget.ai_allowance_state("alice", feature="profile_chat")
    assert st["tier"] == "paid" and st["unlimited"] is True and st["over"] is False
    assert st["used"] is None and st["limit"] is None


def test_free_under_limit_not_over():
    st = budget.ai_allowance_state("alice", feature="profile_chat")
    assert st["tier"] == "free" and st["over"] is False
    assert st["used"] == 0 and st["limit"] == 10 and st["remaining"] == 10


def test_free_over_limit_when_enforced(monkeypatch):
    monkeypatch.setattr(budget, "FREE_TIER_DAILY_AI_ACTIONS", 2)
    budget.note_action("alice", "tracker_extract")
    budget.note_action("alice", "tracker_extract")   # used == 2 == limit
    st = budget.ai_allowance_state("alice", feature="tracker_extract")   # a NEW per-call action
    assert st["over"] is True and st["remaining"] == 0
    assert "AI actions" in st["reason"]


def test_observe_mode_never_blocks_on_the_action_count(monkeypatch):
    monkeypatch.setattr(budget, "FREE_TIER_AI_GATE_ENFORCED", False)
    monkeypatch.setattr(budget, "FREE_TIER_DAILY_AI_ACTIONS", 1)
    budget.note_action("alice", "tracker_extract")   # used == 1 == limit
    st = budget.ai_allowance_state("alice", feature="tracker_extract")
    assert st["over"] is False        # observe mode: reported but not enforced
    assert st["used"] == 1 and st["remaining"] == 0


def test_continuing_an_existing_action_is_not_over(monkeypatch):
    monkeypatch.setattr(budget, "FREE_TIER_DAILY_AI_ACTIONS", 1)
    budget.note_action("alice", "profile_chat")      # opens the profile action (used == 1)
    # Another profile call in the same window CONTINUES that action, so it must not be blocked
    # even though used == limit.
    st = budget.ai_allowance_state("alice", feature="profile_chat")
    assert st["over"] is False


def test_observe_mode_off_never_blocks_even_at_the_limit(monkeypatch):
    # With the per-user dollar backstop removed, the ONLY per-user gate is the action count,
    # and it is inert in observe mode — a Free user at the limit is reported, never blocked.
    monkeypatch.setattr(budget, "FREE_TIER_AI_GATE_ENFORCED", False)
    monkeypatch.setattr(budget, "FREE_TIER_DAILY_AI_ACTIONS", 1)
    budget.note_action("alice", "tracker_extract")   # used == 1 == limit
    st = budget.ai_allowance_state("alice", feature="tracker_extract")
    assert st["over"] is False and st["reason"] is None


def test_first_day_gets_the_boosted_limit(monkeypatch):
    monkeypatch.setattr(budget, "get_user_subscription",
                        lambda uid: {"subscription_status": "free", "created_at": _iso(0)})
    st = budget.ai_allowance_state("alice", feature="profile_chat")
    assert st["limit"] == 20


def test_exempt_userid_is_unlimited(monkeypatch):
    monkeypatch.setattr(budget, "BUDGET_EXEMPT_USERIDS", frozenset({"alice"}))
    st = budget.ai_allowance_state("alice", feature="profile_chat")
    assert st["tier"] == "paid" and st["unlimited"] is True


def test_reset_at_is_next_utc_midnight():
    st = budget.ai_allowance_state("alice", feature="profile_chat")
    reset = datetime.datetime.fromisoformat(st["reset_at"])
    assert reset.hour == 0 and reset.minute == 0
    assert reset > datetime.datetime.now(datetime.timezone.utc)


def test_no_feature_is_never_over(monkeypatch):
    # action_items passes feature=None: with no feature there is no new action to gate on, and
    # the dollar backstop is gone, so such a call is never blocked (it is still costed).
    monkeypatch.setattr(budget, "FREE_TIER_DAILY_AI_ACTIONS", 1)
    budget.note_action("alice", "tracker_extract")   # used == 1 == limit
    st = budget.ai_allowance_state("alice", feature=None)
    assert st["over"] is False        # no feature -> no new action -> action cap not applied

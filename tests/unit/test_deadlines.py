"""Unit tests for app.services.deadlines — pure cache/payload helpers only.

The Supabase-seam functions (get_opportunity_for_deadline_check,
patch_opportunity_deadline, log_deadline_check) are deliberately NOT tested here.
Clock is frozen by subclassing the module's datetime.datetime.
"""
import datetime as _dt

import pytest

from app.services import deadlines


_NOW = _dt.datetime(2026, 8, 23, 12, 0, 0, tzinfo=_dt.timezone.utc)


class _FrozenDateTime(_dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return _NOW if tz is None else _NOW.astimezone(tz)


@pytest.fixture
def frozen_clock(monkeypatch):
    monkeypatch.setattr(deadlines.datetime, "datetime", _FrozenDateTime)


# --------------------------------------------------------------------------- #
# deadline_cache_is_fresh
# --------------------------------------------------------------------------- #
def test_fresh_none_is_false(frozen_clock):
    assert deadlines.deadline_cache_is_fresh(None) is False


def test_fresh_empty_is_false(frozen_clock):
    assert deadlines.deadline_cache_is_fresh("") is False


def test_fresh_parse_error_is_false(frozen_clock):
    assert deadlines.deadline_cache_is_fresh("not-a-date") is False


def test_fresh_within_window_true(frozen_clock):
    # 3 days ago < 7-day window
    ts = (_NOW - _dt.timedelta(days=3)).isoformat()
    assert deadlines.deadline_cache_is_fresh(ts) is True


def test_fresh_older_than_window_false(frozen_clock):
    ts = (_NOW - _dt.timedelta(days=8)).isoformat()
    assert deadlines.deadline_cache_is_fresh(ts) is False


def test_fresh_exactly_at_boundary_false(frozen_clock):
    # delta == 7 days is NOT < 7 days -> stale
    ts = (_NOW - _dt.timedelta(days=deadlines.DEADLINE_STALE_DAYS)).isoformat()
    assert deadlines.deadline_cache_is_fresh(ts) is False


def test_fresh_handles_z_suffix(frozen_clock):
    # "Z" is replaced with +00:00 before parsing.
    ts = "2026-08-22T12:00:00Z"
    assert deadlines.deadline_cache_is_fresh(ts) is True


# --------------------------------------------------------------------------- #
# cached_deadline_payload — key mapping + defaults
# --------------------------------------------------------------------------- #
def test_cached_payload_full():
    opp = {
        "status": "running",
        "important_dates": [{"label": "x"}],
        "was_estimated": False,
        "important_date_note": "note",
        "last_checked_at": "2026-08-23T00:00:00+00:00",
    }
    got = deadlines.cached_deadline_payload(opp, "db-cache")
    assert got == {
        "status": "running",
        "important_dates": [{"label": "x"}],
        "was_estimated": False,
        "important_date_note": "note",
        "last_checked_at": "2026-08-23T00:00:00+00:00",
        "source": "db-cache",
    }


def test_cached_payload_missing_keys_default():
    got = deadlines.cached_deadline_payload({}, "src")
    assert got["status"] is None
    assert got["important_dates"] == []   # None/missing -> []
    assert got["was_estimated"] is None
    assert got["important_date_note"] is None
    assert got["last_checked_at"] is None
    assert got["source"] == "src"


def test_cached_payload_none_important_dates_becomes_list():
    got = deadlines.cached_deadline_payload({"important_dates": None}, "src")
    assert got["important_dates"] == []


# --------------------------------------------------------------------------- #
# mock_deadline_check_payload — deterministic structure
# --------------------------------------------------------------------------- #
def test_mock_payload_structure():
    opp = {"name": "Prog", "url": "https://x.org/a"}
    got = deadlines.mock_deadline_check_payload(opp)
    assert got["status"] == "running"
    assert got["was_estimated"] is True
    assert got["last_checked_at"] is None
    assert got["source"] == "mock"
    assert len(got["important_dates"]) == 1
    d = got["important_dates"][0]
    assert d["label"] == "Application Deadline"
    assert d["type"] == "deadline"
    # date_iso is a valid ISO date (process-salted hash -> don't pin exact value)
    _dt.date.fromisoformat(d["date_iso"])


def test_mock_payload_handles_missing_name_url():
    got = deadlines.mock_deadline_check_payload({})
    _dt.date.fromisoformat(got["important_dates"][0]["date_iso"])
    assert got["source"] == "mock"

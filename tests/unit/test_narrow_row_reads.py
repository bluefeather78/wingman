"""Row reads ask for what they use — S1-15, finding L10.

get_user() selects `*`, so update_user_location, update_subscription, bump_token_version,
the calendar sync and the mailing-list signup each pulled password_hash, the Google Calendar
refresh token and the student's whole 37KB `data` blob into memory to answer a question
about other columns. Nothing leaked, but every new consumer of `record` was one json.dumps
from doing so, and ops' roster read did it for every account at once.
"""
import urllib.error
import urllib.parse

import pytest

import app.core as core
import app.services.google_oauth as gsvc
import app.services.mailing_list as mlsvc
import ops.core as ops


class _Recorder:
    """Captures the query string of every _users_request call."""
    def __init__(self, rows=None):
        self.rows = rows if rows is not None else [{"userid": "alice"}]
        self.queries = []

    def __call__(self, method, query="", data=None, prefer=None):
        self.queries.append(query)
        return self.rows

    def selects(self):
        out = []
        for q in self.queries:
            for part in q.lstrip("?").split("&"):
                if part.startswith("select="):
                    out.append(urllib.parse.unquote(part[len("select="):]))
        return out


# ---------------- the helpers ----------------

def test_user_exists_reads_one_column(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(core, "_users_request", rec)
    assert core.user_exists("alice") is True
    assert rec.selects() == ["userid"]


def test_user_exists_is_false_for_a_missing_row(monkeypatch):
    monkeypatch.setattr(core, "_users_request", _Recorder(rows=[]))
    assert core.user_exists("ghost") is False


def test_select_user_asks_for_exactly_what_it_was_given(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(core, "_users_request", rec)
    core.select_user("alice", "userid,token_version")
    assert rec.selects() == ["userid,token_version"]


def test_a_missing_column_degrades_to_a_wide_read_rather_than_400ing(monkeypatch):
    """PostgREST 400s the WHOLE read on one unknown column, so a narrow select would
    otherwise break a working feature over an un-migrated optional one."""
    calls = []

    def _req(method, query="", data=None, prefer=None):
        calls.append(query)
        if "google_calendar_id" in query:
            raise urllib.error.HTTPError("u", 400, "Bad Request", {}, None)
        return [{"userid": "alice"}]

    monkeypatch.setattr(core, "_users_request", _req)
    monkeypatch.setattr(core, "_error_body", lambda e: {"message": "no such column"})
    assert core.select_user("alice", "userid,google_calendar_id") == {"userid": "alice"}
    assert "select=%2A" in calls[-1] or "select=*" in calls[-1]


def test_the_fallback_does_not_latch(monkeypatch):
    """Unlike the account read, select_user is called with several different column sets —
    one missing column must not widen all of them."""
    seen = []

    def _req(method, query="", data=None, prefer=None):
        seen.append(query)
        if "missing_col" in query:
            raise urllib.error.HTTPError("u", 400, "Bad", {}, None)
        return [{"userid": "alice"}]

    monkeypatch.setattr(core, "_users_request", _req)
    monkeypatch.setattr(core, "_error_body", lambda e: {})
    core.select_user("alice", "userid,missing_col")
    core.select_user("alice", "userid,token_version")
    assert "select=userid%2Ctoken_version" in seen[-1]


def test_a_non_400_still_raises(monkeypatch):
    def _req(*a, **k):
        raise urllib.error.HTTPError("u", 503, "Down", {}, None)
    monkeypatch.setattr(core, "_users_request", _req)
    with pytest.raises(urllib.error.HTTPError):
        core.select_user("alice", "userid")


# ---------------- the call sites ----------------

@pytest.mark.parametrize("call,expected", [
    (lambda: core.update_user_location("alice", "NY"), "userid"),
    (lambda: core.update_subscription("alice", {"subscription_status": "beta"}), "userid"),
    (lambda: core.bump_token_version("alice"), "userid,token_version"),
])
def test_the_core_writers_no_longer_read_the_whole_row(monkeypatch, call, expected):
    rec = _Recorder()
    monkeypatch.setattr(core, "_users_request", rec)
    call()
    assert rec.selects() == [expected]
    assert "*" not in rec.selects()


def test_the_calendar_token_read_asks_only_for_the_calendar_columns(monkeypatch):
    rec = _Recorder(rows=[{"userid": "alice"}])
    monkeypatch.setattr(core, "_users_request", rec)
    assert gsvc.get_google_calendar_access_token("alice") is None
    selected = rec.selects()[0]
    assert "password_hash" not in selected
    assert "google_calendar_refresh_token" in selected
    assert "*" not in selected


def test_the_mailing_list_signup_asks_only_for_the_name(monkeypatch):
    rec = _Recorder(rows=[{"userid": "alice", "first_name": "A", "last_name": "B"}])
    monkeypatch.setattr(core, "_users_request", rec)
    assert mlsvc.select_user("alice", "userid,first_name,last_name")["first_name"] == "A"
    assert rec.selects() == ["userid,first_name,last_name"]


# ---------------- the ops roster ----------------

def test_the_roster_never_holds_a_password_hash_or_a_calendar_token(monkeypatch):
    """ops/ is localhost-only, but it holds EVERY account at once — which is exactly the
    place a stray json.dumps hurts most."""
    monkeypatch.setattr(ops, "_users_request", lambda *a, **k: [{
        "userid": "alice", "email": "a@example.com", "data": {"tracker": []},
        "password_hash": "argon2$secret",
        "google_calendar_refresh_token": "1//refresh",
        "google_calendar_access_token": "ya29.access",
    }])
    rows = ops._fetch_all_accounts()
    assert rows[0].get("password_hash") is None
    assert rows[0].get("google_calendar_refresh_token") is None
    assert rows[0].get("google_calendar_access_token") is None


def test_the_roster_keeps_data_because_the_funnel_reads_it(monkeypatch):
    """Dropping `data` would silently zero every funnel stage after 'signed_up' — a
    security tidy-up that quietly breaks a dashboard is not a win."""
    monkeypatch.setattr(ops, "_users_request", lambda *a, **k: [{
        "userid": "alice", "data": {"tracker": [{"id": "x"}]}, "password_hash": "h",
    }])
    assert ops._fetch_all_accounts()[0]["data"] == {"tracker": [{"id": "x"}]}
    assert "data" not in ops._ACCOUNT_SECRET_COLUMNS


def test_the_roster_read_still_selects_star_for_migration_tolerance(monkeypatch):
    """One unknown column 400s the whole read, so the narrowing has to be on this side."""
    seen = []
    monkeypatch.setattr(ops, "_users_request",
                        lambda m, q="", **k: seen.append(q) or [])
    ops._fetch_all_accounts()
    assert "select=%2A" in seen[0]

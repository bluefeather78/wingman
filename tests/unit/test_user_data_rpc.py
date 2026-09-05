"""Writing into users.data — PRODUCTION_READINESS_PLAN.md Phase 4, perf_report finding 10,
tracked as security finding L9 since Phase 1.

update_user_data() was a read-modify-write: SELECT the whole `data` blob, set the key in
Python, PATCH the whole blob back. Three round trips and two transfers of every tracked
opportunity plus the entire profile — and, far worse, LOST UPDATES. Two overlapping saves
both read the same blob, each set their own key on their own copy, and the second PATCH
overwrote the first's key with the stale value it had read. Nothing errored. The student's
change was simply gone.

What these pin:

  * the RPC is used when it exists, and it is ONE statement — that is the whole fix, because
    the merge happens inside Postgres where concurrent writers serialise on the row.
  * a missing function FALLS BACK rather than breaking every save, since that is the state of
    every checkout until somebody runs db/user_data_rpc.sql.
  * the fallback warns once, not per save.
  * "no such account" still answers False, which is what keeps handle_data_save's 404 honest.
  * a real failure still RAISES, so it becomes an opaque 502 instead of a silent no-op that
    reports success to the student.
"""
import io
import json
import urllib.error

import pytest

import app.core as core


def _http_error(code, body):
    return urllib.error.HTTPError(
        "https://x", code, "err", {}, io.BytesIO(json.dumps(body).encode()))


@pytest.fixture(autouse=True)
def _rpc_on(monkeypatch):
    monkeypatch.setattr(core, "SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr(core, "SUPABASE_SERVICE_KEY", "service-key")
    monkeypatch.setattr(core, "_set_user_data_rpc_available", True)
    monkeypatch.setattr(core, "_set_user_data_rpc_warned", False)
    yield
    core._set_user_data_rpc_available = True
    core._set_user_data_rpc_warned = False


def test_a_save_is_one_rpc_call_not_a_read_then_a_patch(monkeypatch):
    calls = []

    def fake(table, method="GET", params=None, data=None, extra_headers=None):
        calls.append((table, method, data))
        return True
    monkeypatch.setattr(core, "_supabase_request_strict", fake)
    monkeypatch.setattr(core, "_users_request",
                        lambda *a, **k: pytest.fail("fell back to read-modify-write"))

    assert core.update_user_data("alice", "student-profile", {"text": "hi"}) is True
    assert calls == [("rpc/set_user_data", "POST",
                      {"uid": "alice", "kv": {"student-profile": {"text": "hi"}}})]


def test_several_keys_are_merged_in_one_statement(monkeypatch):
    """Four sequential saves could interleave with a write from another device between any
    two of them; one merge cannot."""
    calls = []

    def fake(table, method="GET", params=None, data=None, extra_headers=None):
        calls.append(data)
        return True
    monkeypatch.setattr(core, "_supabase_request_strict", fake)

    assert core.update_user_data_many("alice", {"a": 1, "b": 2, "c": 3}) is True
    assert len(calls) == 1
    assert calls[0]["kv"] == {"a": 1, "b": 2, "c": 3}


def test_a_null_value_is_stored_as_null_not_dropped(monkeypatch):
    """Unchanged from the read-modify-write: nothing in the app distinguishes absent from
    null (/api/data/load answers null for an unknown key), so there is no delete form."""
    sent = {}

    def fake(table, method="GET", params=None, data=None, extra_headers=None):
        sent.update(data)
        return True
    monkeypatch.setattr(core, "_supabase_request_strict", fake)

    core.update_user_data("alice", "k", None)
    assert sent["kv"] == {"k": None}


def test_no_such_account_answers_false_so_the_route_still_404s(monkeypatch):
    monkeypatch.setattr(core, "_supabase_request_strict",
                        lambda *a, **k: False)
    assert core.update_user_data("ghost", "k", 1) is False


def test_the_rpc_answer_is_read_the_same_whether_postgrest_wraps_it_in_a_list(monkeypatch):
    """PostgREST returns a scalar function's result bare or as a one-element list depending
    on the Accept header; both shapes must read the same."""
    for answer, expected in ((True, True), ([True], True), (False, False), ([False], False),
                             ([], False)):
        monkeypatch.setattr(core, "_supabase_request_strict", lambda *a, **k: answer)
        assert core.update_user_data("alice", "k", 1) is expected


# ---------- the un-migrated database ----------

def test_a_missing_function_falls_back_and_still_saves(monkeypatch, capsys):
    """Phase 3's lesson: 'the migration has not been run yet' is the untested case, and it is
    the state of every checkout."""
    monkeypatch.setattr(core, "_supabase_request_strict",
                        lambda *a, **k: (_ for _ in ()).throw(
                            _http_error(404, {"code": "PGRST202"})))
    patched = {}

    def fake_users(method, query="", data=None, prefer=None):
        if method == "GET":
            return [{"data": {"existing": 1}}]
        patched.update(data or {})
        return []
    monkeypatch.setattr(core, "_users_request", fake_users)

    assert core.update_user_data("alice", "k", 2) is True
    assert patched["data"] == {"existing": 1, "k": 2}
    assert "db/user_data_rpc.sql" in capsys.readouterr().out


def test_the_fallback_warning_is_printed_once_not_per_save(monkeypatch, capsys):
    monkeypatch.setattr(core, "_supabase_request_strict",
                        lambda *a, **k: (_ for _ in ()).throw(
                            _http_error(404, {"code": "PGRST202"})))
    monkeypatch.setattr(core, "_users_request",
                        lambda method, query="", data=None, prefer=None:
                            [{"data": {}}] if method == "GET" else [])
    for _ in range(5):
        core.update_user_data("alice", "k", 1)
    assert capsys.readouterr().out.count("set_user_data RPC missing") == 1


def test_the_missing_function_latch_stops_retrying_the_rpc(monkeypatch):
    """One failed call per checkout, not one per save forever."""
    attempts = []

    def fake(*a, **k):
        attempts.append(1)
        raise _http_error(404, {"code": "PGRST202"})
    monkeypatch.setattr(core, "_supabase_request_strict", fake)
    monkeypatch.setattr(core, "_users_request",
                        lambda method, query="", data=None, prefer=None:
                            [{"data": {}}] if method == "GET" else [])
    for _ in range(4):
        core.update_user_data("alice", "k", 1)
    assert len(attempts) == 1


def test_a_real_failure_raises_rather_than_silently_falling_back(monkeypatch):
    """A 500 from Supabase must become handle_data_save's opaque 502. Falling back would
    turn an outage into a second failing write and, worse, a report of success."""
    monkeypatch.setattr(core, "_supabase_request_strict",
                        lambda *a, **k: (_ for _ in ()).throw(
                            _http_error(500, {"code": "XX000"})))
    monkeypatch.setattr(core, "_users_request",
                        lambda *a, **k: pytest.fail("fell back on a real failure"))
    with pytest.raises(urllib.error.HTTPError):
        core.update_user_data("alice", "k", 1)


def test_no_supabase_credentials_uses_the_local_path_without_warning(monkeypatch, capsys):
    """Offline dev is CLAUDE.md's standing constraint, and it is not a broken migration."""
    monkeypatch.setattr(core, "SUPABASE_URL", "")
    monkeypatch.setattr(core, "_users_request",
                        lambda method, query="", data=None, prefer=None:
                            [{"data": {}}] if method == "GET" else [])
    assert core.update_user_data("alice", "k", 1) is True
    assert "set_user_data RPC missing" not in capsys.readouterr().out


def test_an_empty_write_does_not_touch_the_row(monkeypatch):
    monkeypatch.setattr(core, "user_exists", lambda uid: True)
    monkeypatch.setattr(core, "_supabase_request_strict",
                        lambda *a, **k: pytest.fail("wrote nothing, expensively"))
    assert core.update_user_data_many("alice", {}) is True

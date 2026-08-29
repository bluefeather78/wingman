"""Unit tests for the behavioral event log (P-A): app.core.record_user_events /
flush_user_events and the POST /api/events route.

The three postures under test mirror user_activity, for the same reasons:
  * buffered write + background flush (this table takes every impression),
  * latch off on a missing table/column (an un-run migration is a quiet no-op),
  * fail-open — capture must NEVER raise or block a request.

No Supabase and no TestClient (this env cannot open the event loop's socketpair — see
test_subscription_gate). The flush's only network seam is _supabase_request_strict,
monkeypatched; the route is exercised by calling the handler function directly.
"""
import io
import json
import urllib.error

import pytest

import app.core as core
from app.routes import events as events_route
from app.auth import AuthedUser


def _http_error(code, body):
    return urllib.error.HTTPError(
        "http://x", code, "err", {}, io.BytesIO(json.dumps(body).encode()))


@pytest.fixture(autouse=True)
def _clean_buffer(monkeypatch):
    """Every test starts with an empty buffer, capture ON, and no real flusher thread."""
    monkeypatch.setattr(core, "_events_buffer", [], raising=False)
    monkeypatch.setattr(core, "_events_available", True, raising=False)
    monkeypatch.setattr(core, "_events_flusher", object(), raising=False)  # suppress start
    yield


# ---------- record_user_events: buffering + validation ----------

def test_buffers_valid_events_and_returns_accepted_count():
    n = core.record_user_events("Alice", [
        {"action": "impression", "opportunity_id": "ec1", "context": {"rank": 1}},
        {"action": "save", "opportunity_id": "ec2"},
    ])
    assert n == 2
    assert len(core._events_buffer) == 2
    row = core._events_buffer[0]
    assert row["userid"] == "alice"          # lowercased like every user-keyed table
    assert row["action"] == "impression"
    assert row["opportunity_id"] == "ec1"
    assert row["context"] == {"rank": 1}
    assert row["ts"]                          # server-stamped on arrival


def test_drops_unknown_actions_and_non_dicts():
    n = core.record_user_events("bob", [
        {"action": "impression", "opportunity_id": "ec1"},
        {"action": "definitely_not_an_action", "opportunity_id": "ec2"},
        "not a dict",
        {"opportunity_id": "ec3"},            # no action
    ])
    assert n == 1
    assert [r["action"] for r in core._events_buffer] == ["impression"]


def test_opportunity_id_and_context_are_normalized():
    core.record_user_events("bob", [
        {"action": "search", "opportunity_id": "", "context": "not-a-dict"},
        {"action": "tag_filter"},             # no opp id, no context
    ])
    a, b = core._events_buffer
    assert a["opportunity_id"] is None        # "" -> None
    assert a["context"] == {}                 # non-dict -> {}
    assert b["opportunity_id"] is None        # missing -> None


def test_no_userid_records_nothing():
    assert core.record_user_events("", [{"action": "save", "opportunity_id": "ec1"}]) == 0
    assert core.record_user_events(None, [{"action": "save"}]) == 0
    assert core._events_buffer == []


def test_latched_off_records_nothing(monkeypatch):
    monkeypatch.setattr(core, "_events_available", False, raising=False)
    assert core.record_user_events("alice", [{"action": "save", "opportunity_id": "e"}]) == 0
    assert core._events_buffer == []


def test_empty_or_all_invalid_batch_returns_zero():
    assert core.record_user_events("alice", []) == 0
    assert core.record_user_events("alice", [{"action": "nope"}]) == 0


def test_buffer_overflow_drops_oldest(monkeypatch):
    monkeypatch.setattr(core, "EVENTS_MAX_BUFFER", 3, raising=False)
    core.record_user_events("alice", [{"action": "impression", "opportunity_id": f"a{i}"}
                                      for i in range(3)])
    assert [r["opportunity_id"] for r in core._events_buffer] == ["a0", "a1", "a2"]
    # Two more push the two oldest out — memory is bounded, newest kept.
    core.record_user_events("alice", [{"action": "save", "opportunity_id": "b0"},
                                      {"action": "save", "opportunity_id": "b1"}])
    assert [r["opportunity_id"] for r in core._events_buffer] == ["a2", "b0", "b1"]


def test_never_raises_even_on_a_hostile_event(monkeypatch):
    # A context that json can't handle must not blow up the request path; it is buffered as
    # given and only the flush would notice — but record itself must never raise.
    weird = object()
    assert core.record_user_events("alice", [{"action": "save", "context": weird}]) == 1


# ---------- flush_user_events: batch insert, latch, drop-on-transient ----------

def test_flush_posts_the_batch_and_clears(monkeypatch):
    sent = {}

    def fake(table, method="GET", params=None, data=None, extra_headers=None):
        sent.update(table=table, method=method, data=data, headers=extra_headers)
        return []
    monkeypatch.setattr(core, "_supabase_request_strict", fake)

    core.record_user_events("alice", [{"action": "save", "opportunity_id": "ec1"}])
    core.flush_user_events()
    assert sent["table"] == "user_events" and sent["method"] == "POST"
    assert isinstance(sent["data"], list) and sent["data"][0]["opportunity_id"] == "ec1"
    assert sent["headers"] == {"Prefer": "return=minimal"}
    assert core._events_buffer == []          # drained
    assert core._events_available is True


def test_flush_noop_on_empty_buffer(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("must not call Supabase with an empty buffer")
    monkeypatch.setattr(core, "_supabase_request_strict", boom)
    core.flush_user_events()                  # buffer empty from the fixture


def test_flush_latches_off_on_missing_table(monkeypatch):
    def missing(*a, **k):
        raise _http_error(404, {"code": "PGRST205", "message": "no table"})
    monkeypatch.setattr(core, "_supabase_request_strict", missing)
    core.record_user_events("alice", [{"action": "save", "opportunity_id": "ec1"}])
    core.flush_user_events()
    # The migration hasn't run: capture goes quiet rather than erroring every request.
    assert core._events_available is False


def test_flush_latches_off_on_missing_column(monkeypatch):
    def missing_col(*a, **k):
        raise _http_error(400, {"code": "PGRST204", "message": "unknown column"})
    monkeypatch.setattr(core, "_supabase_request_strict", missing_col)
    core.record_user_events("alice", [{"action": "save", "opportunity_id": "ec1"}])
    core.flush_user_events()
    assert core._events_available is False


def test_flush_drops_batch_on_transient_failure_but_stays_available(monkeypatch):
    def flaky(*a, **k):
        raise _http_error(503, {"message": "temporarily unavailable"})
    monkeypatch.setattr(core, "_supabase_request_strict", flaky)
    core.record_user_events("alice", [{"action": "save", "opportunity_id": "ec1"}])
    core.flush_user_events()
    # A transient error loses this interval rather than re-buffering forever, and capture
    # stays ON (unlike a missing table, which is a permanent setup fact).
    assert core._events_buffer == []
    assert core._events_available is True


# ---------- POST /api/events route ----------

def _body(events):
    return {"events": events}


def test_route_records_for_a_signed_in_user(monkeypatch):
    captured = {}
    monkeypatch.setattr(events_route, "record_user_events",
                        lambda uid, evs: captured.update(uid=uid, evs=evs) or len(evs))
    resp = events_route.handle_events(
        body=_body([{"action": "save", "opportunity_id": "ec1"}]),
        user=AuthedUser(id="alice"))
    assert captured["uid"] == "alice"
    assert json.loads(resp.body)["accepted"] == 1


def test_route_is_a_noop_for_a_signed_out_caller(monkeypatch):
    def unexpected(*a, **k):
        raise AssertionError("must not record for an unidentified caller")
    monkeypatch.setattr(events_route, "record_user_events", unexpected)
    resp = events_route.handle_events(body=_body([{"action": "save"}]), user=None)
    payload = json.loads(resp.body)
    assert payload == {"ok": True, "accepted": 0}


def test_route_tolerates_a_single_unwrapped_event(monkeypatch):
    seen = {}
    monkeypatch.setattr(events_route, "record_user_events",
                        lambda uid, evs: seen.update(evs=evs) or len(evs))
    events_route.handle_events(body={"action": "search", "context": {"query": "robotics"}},
                               user=AuthedUser(id="alice"))
    assert seen["evs"][0]["action"] == "search"


def test_route_tolerates_a_body_with_no_events(monkeypatch):
    monkeypatch.setattr(events_route, "record_user_events", lambda uid, evs: len(evs))
    resp = events_route.handle_events(body={}, user=AuthedUser(id="alice"))
    assert json.loads(resp.body)["accepted"] == 0

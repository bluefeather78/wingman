"""Unit tests for the API-error log: app.core.record_api_error / flush_api_errors (the
recorder the capture middleware calls) and ops.core._summarize_api_errors / get_api_errors
(the reads the admin console's API Errors tab and Health card make).

The recorder's three postures mirror user_events, for the same reasons:
  * buffered write + background flush (a burst of failures must not hammer Supabase on the
    request path and turn one outage into two),
  * latch off on a missing table/column (an un-run migration is a quiet no-op),
  * fail-open — recording an error must NEVER itself raise or block the response.

No Supabase and no TestClient (this env cannot open the event loop's socketpair — see
test_user_events). The flush's only network seam is _supabase_request_strict, monkeypatched.
"""
import datetime
import io
import json
import urllib.error

import pytest

import app.core as core
import ops.core as opscore


def _http_error(code, body):
    return urllib.error.HTTPError(
        "http://x", code, "err", {}, io.BytesIO(json.dumps(body).encode()))


def _iso(**delta):
    return (datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(**delta)).isoformat()


@pytest.fixture(autouse=True)
def _clean_buffer(monkeypatch):
    """Every test starts with an empty buffer, capture ON, and no real flusher thread."""
    monkeypatch.setattr(core, "_api_errors_buffer", [], raising=False)
    monkeypatch.setattr(core, "_api_errors_available", True, raising=False)
    monkeypatch.setattr(core, "_api_errors_flusher", object(), raising=False)  # suppress start
    yield


# ---------- record_api_error: buffering + normalization ----------

def test_buffers_a_full_error_row():
    ok = core.record_api_error("POST", "/api/ai/messages", 500, "KeyError",
                               message="boom", traceback_text="Traceback (most recent call last)")
    assert ok is True
    assert len(core._api_errors_buffer) == 1
    row = core._api_errors_buffer[0]
    assert row["method"] == "POST"
    assert row["path"] == "/api/ai/messages"
    assert row["status"] == 500
    assert row["error_type"] == "KeyError"
    assert row["message"] == "boom"
    assert row["traceback"].startswith("Traceback")
    assert row["ts"]                       # server-stamped on arrival


def test_returned_5xx_has_no_message_or_traceback():
    core.record_api_error("GET", "/api/opportunities", 502, "server_error")
    row = core._api_errors_buffer[0]
    assert row["message"] is None and row["traceback"] is None


def test_message_and_traceback_are_truncated(monkeypatch):
    monkeypatch.setattr(core, "_API_ERROR_MSG_MAX", 10, raising=False)
    monkeypatch.setattr(core, "_API_ERROR_TRACE_MAX", 5, raising=False)
    core.record_api_error("GET", "/x", 500, "E", message="0123456789ABCDEF",
                          traceback_text="ABCDEFGHIJ")
    row = core._api_errors_buffer[0]
    assert row["message"] == "0123456789" and row["traceback"] == "ABCDE"


def test_non_numeric_status_becomes_zero():
    core.record_api_error("GET", "/x", "notastatus", "E")
    assert core._api_errors_buffer[0]["status"] == 0


def test_latched_off_records_nothing(monkeypatch):
    monkeypatch.setattr(core, "_api_errors_available", False, raising=False)
    assert core.record_api_error("GET", "/x", 500, "E") is False
    assert core._api_errors_buffer == []


def test_buffer_overflow_drops_oldest(monkeypatch):
    monkeypatch.setattr(core, "API_ERRORS_MAX_BUFFER", 3, raising=False)
    for i in range(3):
        core.record_api_error("GET", f"/p{i}", 500, "E")
    assert [r["path"] for r in core._api_errors_buffer] == ["/p0", "/p1", "/p2"]
    core.record_api_error("GET", "/p3", 500, "E")     # pushes the oldest out
    assert [r["path"] for r in core._api_errors_buffer] == ["/p1", "/p2", "/p3"]


# ---------- flush_api_errors: batch insert, latch, drop-on-transient ----------

def test_flush_posts_the_batch_and_clears(monkeypatch):
    sent = {}

    def fake(table, method="GET", params=None, data=None, extra_headers=None):
        sent.update(table=table, method=method, data=data, headers=extra_headers)
        return []
    monkeypatch.setattr(core, "_supabase_request_strict", fake)

    core.record_api_error("GET", "/x", 500, "E", message="m")
    core.flush_api_errors()
    assert sent["table"] == "api_errors" and sent["method"] == "POST"
    assert sent["data"][0]["path"] == "/x"
    assert sent["headers"] == {"Prefer": "return=minimal"}
    assert core._api_errors_buffer == []
    assert core._api_errors_available is True


def test_flush_latches_off_on_missing_table(monkeypatch):
    monkeypatch.setattr(core, "_supabase_request_strict",
                        lambda *a, **k: (_ for _ in ()).throw(
                            _http_error(404, {"code": "PGRST205", "message": "no table"})))
    core.record_api_error("GET", "/x", 500, "E")
    core.flush_api_errors()
    assert core._api_errors_available is False    # migration not run -> capture goes quiet


def test_flush_drops_batch_on_transient_but_stays_available(monkeypatch):
    monkeypatch.setattr(core, "_supabase_request_strict",
                        lambda *a, **k: (_ for _ in ()).throw(
                            _http_error(503, {"message": "temporarily unavailable"})))
    core.record_api_error("GET", "/x", 500, "E")
    core.flush_api_errors()
    assert core._api_errors_buffer == []          # this interval dropped, not re-buffered
    assert core._api_errors_available is True      # a transient failure is not a setup fact


# ---------- _summarize_api_errors: the dashboard rollup ----------

def test_summary_groups_and_counts_recent():
    rows = [
        {"ts": _iso(hours=-1), "method": "GET", "path": "/api/opportunities",
         "status": 502, "error_type": "server_error"},
        {"ts": _iso(hours=-2), "method": "POST", "path": "/api/ai/messages",
         "status": 500, "error_type": "KeyError"},
        {"ts": _iso(days=-3), "method": "GET", "path": "/api/opportunities",
         "status": 500, "error_type": "TimeoutError"},
    ]
    s = opscore._summarize_api_errors(rows)
    assert s["total"] == 3
    assert s["last_24h"] == 2                       # the 3-day-old row is outside 24h
    assert s["by_status"][0] == {"status": 500, "count": 2}   # most common first
    assert s["by_path"][0]["path"] == "/api/opportunities"    # 2 hits -> top
    assert s["by_path"][0]["count"] == 2
    assert {t["type"] for t in s["by_type"]} == {"server_error", "KeyError", "TimeoutError"}


def test_summary_of_empty_is_all_zero():
    s = opscore._summarize_api_errors([])
    assert s["total"] == 0 and s["last_24h"] == 0 and s["most_recent"] is None
    assert s["by_status"] == [] and s["by_path"] == []


# ---------- get_api_errors: missing-table vs outage classification ----------

def test_get_api_errors_reports_missing_table_as_a_setup_step(monkeypatch):
    monkeypatch.setattr(opscore, "_supabase_request_strict",
                        lambda *a, **k: (_ for _ in ()).throw(
                            _http_error(404, {"code": "PGRST205", "message": "no table"})))
    r = opscore.get_api_errors(days=7, limit=10)
    assert r["ok"] is True and r["available"] is False
    assert "api_errors_schema.sql" in r["error"]


def test_get_api_errors_reports_outage_as_not_ok(monkeypatch):
    monkeypatch.setattr(opscore, "_supabase_request_strict",
                        lambda *a, **k: (_ for _ in ()).throw(
                            _http_error(503, {"message": "down"})))
    r = opscore.get_api_errors(days=7, limit=10)
    assert r["ok"] is False and r["available"] is True


def test_get_api_errors_returns_rows_and_summary(monkeypatch):
    rows = [{"id": 1, "ts": _iso(hours=-1), "method": "GET", "path": "/x",
             "status": 500, "error_type": "E", "message": None, "traceback": None}]
    monkeypatch.setattr(opscore, "_supabase_request_strict", lambda *a, **k: rows)
    r = opscore.get_api_errors(days=7, limit=10)
    assert r["ok"] is True and r["available"] is True
    assert r["errors"] == rows
    assert r["summary"]["total"] == 1


def test_get_api_errors_can_omit_rows_for_the_health_card(monkeypatch):
    rows = [{"id": 1, "ts": _iso(hours=-1), "path": "/x", "status": 500, "error_type": "E"}]
    monkeypatch.setattr(opscore, "_supabase_request_strict", lambda *a, **k: rows)
    r = opscore.get_api_errors(days=7, limit=10, include_rows=False)
    assert r["errors"] == []                        # summary only, no row payload
    assert r["summary"]["total"] == 1               # but the rollup is still computed

"""Idempotent cost rollups — PRODUCTION_READINESS_PLAN.md Phase 4, perf_report finding 11.

Both rollups in app/core.py had the same shape: remember the row id, SELECT its counters,
PATCH counters+delta. Three round trips, and a race the in-process lock could only hide inside
ONE process — two workers both read N and both write N+1, so one call's cost vanishes. The
per-process id caches (`_interactive_rollup`, `_user_costs_rows`) made it worse rather than
better: each worker independently believed it owned the row.

What is lost there is money, not latency. The spend caps themselves were never on this path
(budget.note_spend fires synchronously and separately, which test_cost_batching pins), but the
console's figures were — and the console is how anybody notices a runaway.

What these pin:

  * the RPC is used when it exists, and one call is ONE round trip.
  * the counters are sent as DELTAS, not as totals read back from the row. A "fix" that kept
    reading first and merely moved the addition into SQL would not close the race at all.
  * a missing function FALLS BACK to the old read-then-PATCH rather than dropping accounting,
    because losing attribution entirely is strictly worse than losing an increment.
  * the fallback warns once, and stops retrying the RPC.
  * user_costs' `first_at` survives a bump. It means "first call at this grain today"; letting
    the upsert overwrite it would turn it into a second last_at.
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
    monkeypatch.setattr(core, "_bump_rpc_available", True)
    monkeypatch.setattr(core, "_bump_rpc_warned", False)
    monkeypatch.setattr(core, "invalidate_runs_cache", lambda: None)
    core._interactive_rollup.clear()
    yield
    core._bump_rpc_available = True
    core._bump_rpc_warned = False
    core._interactive_rollup.clear()


@pytest.fixture
def rpc(monkeypatch):
    """Capture every RPC payload, and fail the test if anything reaches the old REST path."""
    calls = []

    def fake_strict(table, method="GET", params=None, data=None, extra_headers=None):
        calls.append((table, data))
        return 1
    monkeypatch.setattr(core, "_supabase_request_strict", fake_strict)
    monkeypatch.setattr(core, "_supabase_request",
                        lambda *a, **k: pytest.fail("fell back to read-then-PATCH"))
    return calls


# ---------- agent_runs ----------

def test_an_interactive_call_is_one_round_trip(rpc):
    core._bump_interactive_run("interactive_gemini", "2026-09-05",
                               calls=1, cost=0.02, searches=1, notes="n")
    assert len(rpc) == 1
    table, payload = rpc[0]
    assert table == "rpc/bump_interactive_run"
    assert payload["p_agent"] == "interactive_gemini"
    assert payload["p_mode"] == "2026-09-05"


def test_the_counters_are_sent_as_deltas_not_as_totals(rpc):
    """The whole point. Reading the row first and adding in SQL would leave the race exactly
    where it was — the addition has to happen inside the same statement as the write."""
    core._bump_interactive_run("interactive_claude", "2026-09-05",
                               calls=1, cost=0.02, searches=3, notes="n")
    _, payload = rpc[0]
    assert payload["p_calls"] == 1
    assert payload["p_cost"] == 0.02
    assert payload["p_searches"] == 3


def test_the_runs_cache_is_still_invalidated(monkeypatch):
    """The console polls a 5s cache; a rollup that does not bust it reads as a frozen chart."""
    monkeypatch.setattr(core, "_supabase_request_strict", lambda *a, **k: 1)
    busted = []
    monkeypatch.setattr(core, "invalidate_runs_cache", lambda: busted.append(1))
    core._bump_interactive_run("interactive_gemini", "2026-09-05", 1, 0.01, 0, "n")
    assert busted == [1]


def test_recording_an_interactive_cost_goes_through_the_rpc(monkeypatch, rpc):
    """End to end from the public entry point, since that is what the AI route calls."""
    monkeypatch.setattr(core, "record_user_cost", lambda *a, **k: None)
    core.record_interactive_cost(
        "interactive_claude",
        {"input_tokens": 1000, "output_tokens": 100,
         "server_tool_use": {"web_search_requests": 2}},
        model="claude-haiku", userid=None, feature="profile_chat")
    assert [t for t, _ in rpc] == ["rpc/bump_interactive_run"]
    assert rpc[0][1]["p_calls"] == 1
    assert rpc[0][1]["p_searches"] == 2
    assert rpc[0][1]["p_cost"] > 0


# ---------- user_costs ----------

def test_a_user_cost_write_is_one_round_trip(rpc):
    import datetime
    now = datetime.datetime(2026, 9, 5, 12, 0, tzinfo=datetime.timezone.utc)
    core._write_user_cost("alice", "2026-09-05", "claude", "profile_chat", "m",
                          calls=30, input_tokens=3000, output_tokens=300,
                          searches=2, cost=0.30, now=now)
    assert len(rpc) == 1
    table, payload = rpc[0]
    assert table == "rpc/bump_user_cost"
    assert payload["p_calls"] == 30
    assert payload["p_input_tokens"] == 3000
    assert payload["p_output_tokens"] == 300
    assert payload["p_searches"] == 2
    assert payload["p_cost"] == 0.30
    assert payload["p_at"] == now.isoformat()


def test_a_coalesced_flush_still_sends_one_delta(monkeypatch, rpc):
    """Phase 2 batched 30 calls into one write; Phase 4 must not silently turn that back into
    30 statements."""
    from app.services import budget
    monkeypatch.setattr(budget, "note_spend", lambda *a: None)
    monkeypatch.setattr(core, "_start_cost_flusher", lambda: None)
    with core._cost_buffer_lock:
        core._cost_buffer.clear()
    for _ in range(30):
        core.record_user_cost("alice", "claude", "profile_chat", cost=0.01, model="m")
    core.flush_user_costs()
    assert len(rpc) == 1
    assert rpc[0][1]["p_calls"] == 30


def test_a_null_model_is_sent_as_empty_string(rpc):
    """The grain constraint includes model and Postgres treats NULLs as distinct, so a NULL
    would create a fresh row on every call rather than conflicting with the last one."""
    import datetime
    core._write_user_cost("alice", "2026-09-05", "claude", "x", None,
                          1, 0, 0, 0, 0.01,
                          datetime.datetime.now(datetime.timezone.utc))
    assert rpc[0][1]["p_model"] == ""


def test_no_userid_writes_nothing(rpc):
    import datetime
    core._write_user_cost(None, "2026-09-05", "claude", "x", "m", 1, 0, 0, 0, 0.01,
                          datetime.datetime.now(datetime.timezone.utc))
    assert rpc == []


# ---------- the un-migrated database ----------

def test_a_missing_function_falls_back_to_the_old_path(monkeypatch, capsys):
    """Losing attribution entirely is strictly worse than losing an increment, so the
    fallback stays. Phase 3's lesson: this is the state of every checkout."""
    monkeypatch.setattr(core, "_supabase_request_strict",
                        lambda *a, **k: (_ for _ in ()).throw(
                            _http_error(404, {"code": "PGRST202"})))
    rest = []

    def fake_rest(table, method="GET", params=None, data=None, extra_headers=None):
        rest.append((table, method))
        if method == "GET" and params and params.get("select") == "id":
            return [{"id": 7}]
        if method == "GET":
            return [{"items_processed": 4, "cost_usd": 1.0, "total_web_searches": 0}]
        return []
    monkeypatch.setattr(core, "_supabase_request", fake_rest)

    core._bump_interactive_run("interactive_gemini", "2026-09-05", 1, 0.02, 0, "n")
    assert ("agent_runs", "PATCH") in rest
    assert "db/cost_rollup_rpc.sql" in capsys.readouterr().out


def test_the_fallback_warning_is_printed_once_not_per_call(monkeypatch, capsys):
    monkeypatch.setattr(core, "_supabase_request_strict",
                        lambda *a, **k: (_ for _ in ()).throw(
                            _http_error(404, {"code": "PGRST202"})))
    monkeypatch.setattr(core, "_supabase_request", lambda *a, **k: [])
    for _ in range(5):
        core._bump_interactive_run("interactive_gemini", "2026-09-05", 1, 0.02, 0, "n")
    assert capsys.readouterr().out.count("cost rollup RPCs missing") == 1


def test_the_latch_stops_retrying_the_rpc(monkeypatch):
    attempts = []

    def fake(*a, **k):
        attempts.append(1)
        raise _http_error(404, {"code": "PGRST202"})
    monkeypatch.setattr(core, "_supabase_request_strict", fake)
    monkeypatch.setattr(core, "_supabase_request", lambda *a, **k: [])
    for _ in range(4):
        core._bump_interactive_run("interactive_gemini", "2026-09-05", 1, 0.02, 0, "n")
    assert len(attempts) == 1


def test_a_transient_rpc_failure_does_not_latch_the_rpc_off(monkeypatch):
    """A network blip must not permanently demote every later call to the racy path."""
    monkeypatch.setattr(core, "_supabase_request_strict",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("blip")))
    monkeypatch.setattr(core, "_supabase_request", lambda *a, **k: [])
    core._bump_interactive_run("interactive_gemini", "2026-09-05", 1, 0.02, 0, "n")
    assert core._bump_rpc_available is True


def test_accounting_never_raises_into_the_request_that_triggered_it(monkeypatch):
    """It is best-effort bookkeeping; a Supabase outage must not break a student's chat."""
    monkeypatch.setattr(core, "_supabase_request_strict",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(core, "_supabase_request",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    core._bump_interactive_run("interactive_gemini", "2026-09-05", 1, 0.02, 0, "n")


# ---------- the SQL says what the code assumes ----------

def test_the_partial_index_is_restricted_to_the_two_rollup_agents():
    """agent_runs is an append-only history — one row per run, an agent has run many times.
    A plain unique index on (agent, mode) would break every real agent immediately."""
    from wingman import REPO_ROOT
    import os
    sql = open(os.path.join(REPO_ROOT, "db", "cost_rollup_rpc.sql"), encoding="utf-8").read()
    assert "agent_runs_interactive_rollup_idx" in sql
    assert "where agent in ('interactive_gemini', 'interactive_claude')" in sql
    for agent in core.INTERACTIVE_AGENTS:
        assert agent in sql, f"{agent} is a rollup agent the index does not cover"


def test_the_rpc_refuses_a_non_rollup_agent():
    """Without the guard the function is a way to corrupt an ordinary agent's run history:
    outside the partial index the ON CONFLICT never fires, so every call appends a row."""
    from wingman import REPO_ROOT
    import os
    sql = open(os.path.join(REPO_ROOT, "db", "cost_rollup_rpc.sql"), encoding="utf-8").read()
    assert "raise exception 'bump_interactive_run is only for the interactive rollup agents" in sql


def test_first_at_is_not_overwritten_on_conflict():
    """It means 'first call at this grain today'. Letting the upsert overwrite it would turn
    it into a second last_at, and the console shows both."""
    from wingman import REPO_ROOT
    import os
    sql = open(os.path.join(REPO_ROOT, "db", "cost_rollup_rpc.sql"), encoding="utf-8").read()
    do_update = sql.split("on conflict on constraint user_costs_grain", 1)[1]
    body = do_update.split("returning", 1)[0]
    assert "first_at" not in body.replace("-- first_at is NOT touched", "")

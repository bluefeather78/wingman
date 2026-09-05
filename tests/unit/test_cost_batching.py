"""Unit tests for batched cost accounting — Phase 2 item 6.

Attribution used to cost a THREAD PER PAID CALL, and each thread held the global
_user_costs_lock across three or four Supabase round trips. Concurrent AI calls therefore
serialised behind one another's bookkeeping, and the process spawned an unbounded number of
threads to do it.

The load-bearing test here is the first one. Batching the WRITE is a latency fix; batching the
SPEND CAPS would be a hole in them — the per-user daily budget and the global circuit breaker
(S0-5) exist to stop one account spending ~$90 in a pass, and they read in-process counters
that must see every dollar the instant it is spent. note_spend stays synchronous and first.
"""
import pytest

import app.core as core


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    with core._cost_buffer_lock:
        core._cost_buffer.clear()
    monkeypatch.setattr(core, "SUPABASE_URL", "https://db.example")
    monkeypatch.setattr(core, "SUPABASE_SERVICE_KEY", "svc")
    monkeypatch.setattr(core, "_user_costs_available", True)
    # Never start the real flusher thread in tests.
    monkeypatch.setattr(core, "_start_cost_flusher", lambda: None)
    yield
    with core._cost_buffer_lock:
        core._cost_buffer.clear()


# ---------- the spend caps must NOT be batched ----------

def test_the_spend_caps_see_every_dollar_immediately(monkeypatch):
    """The one that matters. Batching note_spend would let an account outspend its daily
    budget by a whole flush interval's worth of calls."""
    from app.services import budget
    seen = []
    monkeypatch.setattr(budget, "note_spend", lambda uid, cost: seen.append((uid, cost)))
    core.record_user_cost("alice", "gemini", "ranking", cost=0.02)
    core.record_user_cost("alice", "gemini", "ranking", cost=0.03)
    assert seen == [("alice", 0.02), ("alice", 0.03)], "the caps are lagging behind the buffer"


def test_the_caps_are_bumped_even_when_attribution_is_off(monkeypatch):
    """The pre-existing guarantee: the money was spent whether or not we can write it down."""
    from app.services import budget
    seen = []
    monkeypatch.setattr(budget, "note_spend", lambda uid, cost: seen.append((uid, cost)))
    monkeypatch.setattr(core, "_user_costs_available", False)
    core.record_user_cost("alice", "gemini", "ranking", cost=0.02)
    assert seen == [("alice", 0.02)]


# ---------- the buffer ----------

def test_calls_on_one_key_coalesce_into_a_single_write(monkeypatch):
    writes = []
    monkeypatch.setattr(core, "_write_user_cost",
                        lambda *a: writes.append(a))
    for _ in range(30):
        core.record_user_cost("alice", "claude", "profile_chat", cost=0.01,
                              input_tokens=100, output_tokens=10, searches=1, model="m")
    assert writes == [], "nothing should be written before a flush"
    core.flush_user_costs()
    assert len(writes) == 1, "30 calls on one key must cost one write"
    (_uid, _day, surface, feature, model, calls, inp, out, searches, cost, _now) = writes[0]
    assert (surface, feature, model) == ("claude", "profile_chat", "m")
    assert calls == 30
    assert inp == 3000 and out == 300 and searches == 30
    assert cost == pytest.approx(0.30)


def test_different_keys_stay_separate(monkeypatch):
    writes = []
    monkeypatch.setattr(core, "_write_user_cost", lambda *a: writes.append(a))
    core.record_user_cost("alice", "claude", "profile_chat", cost=0.01)
    core.record_user_cost("bob", "claude", "profile_chat", cost=0.02)
    core.record_user_cost("alice", "gemini", "ranking", cost=0.03)
    core.flush_user_costs()
    assert len(writes) == 3


def test_a_flush_empties_the_buffer(monkeypatch):
    monkeypatch.setattr(core, "_write_user_cost", lambda *a: None)
    core.record_user_cost("alice", "claude", "x", cost=0.01)
    core.flush_user_costs()
    assert core._cost_buffer == {}
    writes = []
    monkeypatch.setattr(core, "_write_user_cost", lambda *a: writes.append(a))
    core.flush_user_costs()
    assert writes == [], "a second flush must not re-write what it already wrote"


def test_the_userid_is_normalised_into_the_key(monkeypatch):
    writes = []
    monkeypatch.setattr(core, "_write_user_cost", lambda *a: writes.append(a))
    core.record_user_cost("Alice", "claude", "x", cost=0.01)
    core.record_user_cost("  alice", "claude", "x", cost=0.01)
    core.flush_user_costs()
    assert len(writes) == 1 and writes[0][5] == 2


def test_a_null_model_is_stored_as_empty_string(monkeypatch):
    """The grain constraint includes model, and Postgres treats NULLs as distinct — a NULL
    would create a fresh row on every single call."""
    writes = []
    monkeypatch.setattr(core, "_write_user_cost", lambda *a: writes.append(a))
    core.record_user_cost("alice", "claude", "x", cost=0.01, model=None)
    core.flush_user_costs()
    assert writes[0][4] == ""


def test_a_wide_burst_flushes_itself(monkeypatch):
    """Bounded memory beats a tidy cadence: past COST_FLUSH_MAX_KEYS distinct keys it writes
    immediately rather than growing."""
    writes = []
    monkeypatch.setattr(core, "_write_user_cost", lambda *a: writes.append(a))
    monkeypatch.setattr(core, "COST_FLUSH_MAX_KEYS", 5)
    for i in range(5):
        core.record_user_cost(f"student{i}", "claude", "x", cost=0.01)
    assert len(writes) == 5, "the buffer grew past its ceiling instead of flushing"
    assert core._cost_buffer == {}


def test_no_thread_is_spawned_per_paid_call():
    """Spawning one thread per call was itself part of the cost this item removes."""
    import inspect
    src = inspect.getsource(core.record_user_cost_async)
    assert "threading.Thread" not in src
    assert "record_user_cost(" in src


def test_a_write_failure_cannot_break_a_students_chat(monkeypatch):
    def boom(*a):
        raise RuntimeError("supabase down")
    monkeypatch.setattr(core, "_write_user_cost", boom)
    core.record_user_cost("alice", "claude", "x", cost=0.01)
    with pytest.raises(RuntimeError):
        core.flush_user_costs()          # the flusher thread catches this, not the caller

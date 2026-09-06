"""Unit tests for the paid-check lane — Phase 2 item 8, MARQUEE M9.

handle_deadline_check calls a ~$0.07 Claude web-search check taking tens of seconds, straight
from one of the 40 anyio threadpool slots every route shares, and nothing bounded how many
could run at once. Same disease as item 2's AI lane, in a second place.

What these pin, in order of how quietly each would break:
  * a full lane NEVER sheds free work. Both routes are cache hits almost every time, and a
    lane taken too early would make the cheap path fail because of the expensive one — the
    exact inversion this is supposed to prevent. Nothing in the response would say so.
  * the lane is released on the DEGRADE path. The deadline route's except returns 200, so a
    leak there is invisible: repeated Anthropic failures would narrow the lane one slot at a
    time until every fresh check 503s, with the route still looking healthy.
  * action_items.resolve consults the gate only past its free exits, and degrades rather than
    refusing when it is full — the caller cannot know in advance whether that call would pay.
"""
import pytest

import app.routes.opportunities as opps
import app.services.action_items as action_items
from app.config import PAID_CHECK_MAX_CONCURRENCY, PAID_CHECK_SHED_RETRY_AFTER_SECONDS
from app.services.lanes import PaidLane


@pytest.fixture(autouse=True)
def _fresh_lane(monkeypatch):
    """A private lane per test, so nothing leaks between them or from the real one."""
    lane = PaidLane(PAID_CHECK_MAX_CONCURRENCY, "test")
    monkeypatch.setattr(opps, "paid_check_lane", lane)
    return lane


class _User:
    id = "student-1"


class _Req:
    def __init__(self, refresh=False):
        self.query_params = {"refresh": "1"} if refresh else {}


def _fill(lane):
    for _ in range(lane.limit):
        assert lane.try_acquire() is True


# ---------- the primitive ----------

def test_lane_admits_its_limit_then_sheds():
    lane = PaidLane(3, "x")
    assert [lane.try_acquire() for _ in range(4)] == [True, True, True, False]
    assert lane.in_flight == 3 and lane.shed_count == 1


def test_release_frees_a_slot():
    lane = PaidLane(1, "x")
    assert lane.try_acquire() is True
    assert lane.try_acquire() is False
    lane.release()
    assert lane.try_acquire() is True


def test_over_release_raises_rather_than_widening_the_lane():
    """A lane that has quietly grown past its limit is indistinguishable from a working one,
    so BoundedSemaphore raising is the wanted behaviour, not a nuisance."""
    lane = PaidLane(1, "x")
    lane.try_acquire()
    lane.release()
    with pytest.raises(ValueError):
        lane.release()


def test_the_route_and_the_config_agree():
    assert opps.paid_check_lane.limit == PAID_CHECK_MAX_CONCURRENCY


# ---------- the deadline route ----------

def _stub_deadline_route(monkeypatch, *, fresh, check=None):
    monkeypatch.setattr(opps, "SUPABASE_URL", "https://x", raising=False)
    monkeypatch.setattr(opps, "SUPABASE_SERVICE_KEY", "k", raising=False)
    monkeypatch.setattr(opps, "ANTHROPIC_API_KEY", "sk-test", raising=False)
    monkeypatch.setattr(opps, "touch_user_activity", lambda *a, **k: None)
    monkeypatch.setattr(opps, "record_user_cost_async", lambda *a, **k: None)
    monkeypatch.setattr(opps, "record_api_error", lambda *a, **k: None)
    monkeypatch.setattr(opps.deadlines, "get_opportunity_for_deadline_check",
                        lambda _id: {"id": "o1", "status": "open", "important_dates": [],
                                     "dates_last_checked_at": "2026-09-01T00:00:00Z"})
    monkeypatch.setattr(opps.deadlines, "deadline_cache_is_fresh", lambda _v: fresh)
    monkeypatch.setattr(opps.deadlines, "cached_deadline_payload",
                        lambda opp, src: {"source": src})
    monkeypatch.setattr(opps.deadlines, "log_deadline_check", lambda *a, **k: None)
    monkeypatch.setattr(opps.budget, "circuit_open", lambda: False)
    monkeypatch.setattr(opps.budget, "forced_recheck_ok", lambda *a: True)
    if check is not None:
        monkeypatch.setattr(opps, "check_deadline_one", check)


def test_a_full_lane_never_sheds_a_cached_deadline_read(monkeypatch, _fresh_lane):
    """The one that matters most. A cache hit costs nothing and is the overwhelming majority
    of this route's traffic; four slow paid checks must not start refusing it."""
    _stub_deadline_route(monkeypatch, fresh=True)
    _fill(_fresh_lane)
    resp = opps.handle_deadline_check("o1", _Req(refresh=False), user=_User())
    assert resp.status_code == 200
    assert _fresh_lane.shed_count == 0, "a free read must not even consult the lane"


def test_a_full_lane_sheds_a_fresh_paid_check_with_503_and_retry_after(monkeypatch, _fresh_lane):
    called = []
    _stub_deadline_route(monkeypatch, fresh=False,
                         check=lambda *a, **k: called.append(1))
    _fill(_fresh_lane)
    resp = opps.handle_deadline_check("o1", _Req(), user=_User())
    assert resp.status_code == 503
    assert resp.headers["Retry-After"] == str(PAID_CHECK_SHED_RETRY_AFTER_SECONDS)
    assert called == [], "a shed must never reach the paid call"


def test_the_deadline_lane_is_released_after_a_failed_check(monkeypatch, _fresh_lane):
    """The degrade path returns 200, so a leak here would be silent."""
    def boom(*a, **k):
        raise RuntimeError("anthropic down")
    _stub_deadline_route(monkeypatch, fresh=False, check=boom)
    resp = opps.handle_deadline_check("o1", _Req(), user=_User())
    assert resp.status_code == 200                      # degraded to cached, as designed
    assert _fresh_lane.in_flight == 0, "the slot leaked on the degrade path"


def test_repeated_failures_do_not_narrow_the_deadline_lane(monkeypatch, _fresh_lane):
    def boom(*a, **k):
        raise RuntimeError("anthropic down")
    _stub_deadline_route(monkeypatch, fresh=False, check=boom)
    for _ in range(_fresh_lane.limit * 3):
        opps.handle_deadline_check("o1", _Req(), user=_User())
    assert _fresh_lane.in_flight == 0
    _fill(_fresh_lane)                                  # still admits a full complement


# ---------- action_items.resolve ----------

class _Gate:
    """A lane that records how it was used."""

    def __init__(self, allow=True):
        self.allow = allow
        self.acquired = 0
        self.released = 0

    def try_acquire(self):
        if not self.allow:
            return False
        self.acquired += 1
        return True

    def release(self):
        self.released += 1


def _stub_resolve(monkeypatch, *, stored, fresh, process=None):
    monkeypatch.setattr(action_items, "ANTHROPIC_API_KEY", "sk-test", raising=False)
    monkeypatch.setattr(action_items, "get_opportunity_for_action_items",
                        lambda _id: {"id": "o1", "action_items": stored,
                                     "action_items_checked_at": "2026-09-01T00:00:00Z",
                                     "action_items_source": "stored", "name": "Prog"})
    monkeypatch.setattr(action_items, "_is_fresh", lambda _v: fresh)
    if process is not None:
        monkeypatch.setattr(action_items, "process_one", process)


def test_a_fresh_stored_checklist_never_touches_the_gate(monkeypatch):
    """resolve() is free almost every time; taking a lane slot for that would let checklist
    reads shed the deadline checks sharing the lane."""
    gate = _Gate()
    _stub_resolve(monkeypatch, stored=[{"text": "a"}], fresh=True)
    payload, cost = action_items.resolve("o1", paid_gate=gate)
    assert cost == 0.0 and gate.acquired == 0 and gate.released == 0


def test_a_full_gate_degrades_to_the_stored_list_instead_of_refusing(monkeypatch):
    """Unlike the deadline route, the caller cannot know in advance whether this would pay —
    so a full lane must degrade, not 503."""
    gate = _Gate(allow=False)
    _stub_resolve(monkeypatch, stored=[{"text": "kept"}], fresh=False,
                  process=lambda *a, **k: pytest.fail("must not call a model"))
    payload, cost = action_items.resolve("o1", paid_gate=gate)
    assert cost == 0.0
    assert payload["source"] == "stored"


def test_a_full_gate_with_nothing_stored_still_returns_a_checklist(monkeypatch):
    gate = _Gate(allow=False)
    _stub_resolve(monkeypatch, stored=None, fresh=False,
                  process=lambda *a, **k: pytest.fail("must not call a model"))
    payload, cost = action_items.resolve("o1", paid_gate=gate)
    assert cost == 0.0 and payload["source"] == "generic-fallback"


def test_the_gate_is_released_after_a_paid_generation(monkeypatch):
    gate = _Gate()

    class _D:
        write = False
        items = []
        source = "page"
    _stub_resolve(monkeypatch, stored=[{"text": "a"}], fresh=False,
                  process=lambda *a, **k: (_D(), 0.05, {}, ""))
    action_items.resolve("o1", paid_gate=gate)
    assert gate.acquired == 1 and gate.released == 1


def test_the_gate_is_released_when_generation_raises(monkeypatch):
    gate = _Gate()

    def boom(*a, **k):
        raise RuntimeError("model down")
    _stub_resolve(monkeypatch, stored=[{"text": "a"}], fresh=False, process=boom)
    payload, cost = action_items.resolve("o1", paid_gate=gate)
    assert gate.acquired == 1 and gate.released == 1
    assert payload["source"] == "stored"


def test_resolve_without_a_gate_is_unchanged(monkeypatch):
    """paid_gate is optional: every existing caller and test passes none."""
    _stub_resolve(monkeypatch, stored=[{"text": "a"}], fresh=True)
    payload, cost = action_items.resolve("o1")
    assert cost == 0.0

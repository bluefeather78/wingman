"""Unit tests for the bounded AI lane — Phase 2 item 2, finding M5.

The bug: FastAPI runs a plain-`def` handler in the anyio threadpool, whose 40 slots are
shared by EVERY route. Nothing bounded how many /api/ai could hold, and each holds one for
as long as the provider takes (up to AI_UPSTREAM_TIMEOUT_SECONDS). Enough concurrent AI
calls starved the pool and the service stopped answering requests that never touched a model.

What these pin, in order of how quietly each would break:
  * the route is `async def`. If it regresses to plain `def` the lane still "works" — the
    counter still counts — but a shed would first have to win a threadpool slot to happen,
    so the flood drains the pool this is meant to keep it out of. Nothing else would fail.
  * a shed NEVER reaches _serve_ai, so it never reaches a provider and never costs money.
  * the lane is released on the failure path as well as the success one. A leak here is
    invisible until the lane has silently narrowed to zero and every AI call 503s.

No TestClient — this environment cannot open the socketpair its event loop needs (see
test_ai_request_caps.py). The route is an async function, so it is awaited directly with the
dependency values passed in by hand.
"""
import asyncio
import inspect
import json

import pytest

import app.routes.ai as ai
from app.config import AI_MAX_CONCURRENCY, AI_SHED_RETRY_AFTER_SECONDS


@pytest.fixture(autouse=True)
def _reset_lane():
    """Every test starts with an empty lane and leaves one behind."""
    ai.ai_lane.in_flight = 0
    ai.ai_lane.shed_count = 0
    yield
    ai.ai_lane.in_flight = 0
    ai.ai_lane.shed_count = 0


def _await(coro):
    return asyncio.run(coro)


def _call(**kw):
    kw.setdefault("request", None)
    kw.setdefault("raw_body", b'{"feature":"ranking"}')
    kw.setdefault("user", None)
    return _await(ai.handle_ai(**kw))


# ---------- the counter ----------

def test_lane_limit_is_the_configured_one():
    assert ai.ai_lane.limit == AI_MAX_CONCURRENCY


def test_lane_admits_exactly_its_limit_then_sheds():
    assert all(ai.ai_lane.try_acquire() for _ in range(ai.ai_lane.limit))
    assert ai.ai_lane.in_flight == ai.ai_lane.limit
    assert ai.ai_lane.try_acquire() is False
    assert ai.ai_lane.shed_count == 1


def test_release_frees_a_slot_for_the_next_caller():
    for _ in range(ai.ai_lane.limit):
        ai.ai_lane.try_acquire()
    assert ai.ai_lane.try_acquire() is False
    ai.ai_lane.release()
    assert ai.ai_lane.try_acquire() is True


def test_double_release_cannot_widen_the_lane():
    """A release that outnumbers its acquire must not drive the count negative — that would
    permanently raise the effective limit for the life of the process."""
    ai.ai_lane.try_acquire()
    ai.ai_lane.release()
    ai.ai_lane.release()
    ai.ai_lane.release()
    assert ai.ai_lane.in_flight == 0
    for _ in range(ai.ai_lane.limit):
        assert ai.ai_lane.try_acquire() is True
    assert ai.ai_lane.try_acquire() is False


# ---------- the route ----------

def test_route_is_async_so_a_shed_costs_no_threadpool_slot():
    """The load-bearing property. A regression to plain `def` breaks nothing visible."""
    assert asyncio.iscoroutinefunction(ai.handle_ai)


def test_full_lane_sheds_with_503_and_retry_after():
    for _ in range(ai.ai_lane.limit):
        ai.ai_lane.try_acquire()
    resp = _call()
    assert resp.status_code == 503
    assert resp.headers["Retry-After"] == str(AI_SHED_RETRY_AFTER_SECONDS)
    assert "busy" in json.loads(resp.body)["error"].lower()


def test_a_shed_never_reaches_the_core_so_it_never_reaches_a_provider(monkeypatch):
    """The whole point: shedding is free. If this fails, a flood still bills."""
    reached = []
    monkeypatch.setattr(ai, "_serve_ai", lambda *a, **k: reached.append(1))
    for _ in range(ai.ai_lane.limit):
        ai.ai_lane.try_acquire()
    assert _call().status_code == 503
    assert reached == []


def test_shedding_is_503_not_429():
    """429 means 'you asked for too much' and is one caller's fault; 503 means 'the service
    is at capacity' and is nobody's. The limiter owns 429 — collapsing the two would hide the
    capacity signal this phase exists to surface."""
    for _ in range(ai.ai_lane.limit):
        ai.ai_lane.try_acquire()
    assert _call().status_code == 503


def test_a_served_request_releases_its_slot(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(ai, "_serve_ai", lambda *a, **k: sentinel)
    assert _call() is sentinel
    assert ai.ai_lane.in_flight == 0


def test_a_raising_request_still_releases_its_slot(monkeypatch):
    """Without the `finally`, one provider exception narrows the lane forever."""
    def boom(*a, **k):
        raise RuntimeError("upstream blew up")
    monkeypatch.setattr(ai, "_serve_ai", boom)
    with pytest.raises(RuntimeError):
        _call()
    assert ai.ai_lane.in_flight == 0


def test_the_lane_never_exceeds_its_limit_under_concurrency(monkeypatch):
    """The behavioural test: fire well past the limit at once and assert the high-water mark
    of genuinely-in-flight calls never crosses it."""
    peak = {"n": 0, "served": 0, "shed": 0}

    def slow(*a, **k):
        peak["n"] = max(peak["n"], ai.ai_lane.in_flight)
        peak["served"] += 1
        return "ok"

    monkeypatch.setattr(ai, "_serve_ai", slow)

    async def drive():
        results = await asyncio.gather(*[
            ai.handle_ai(request=None, raw_body=b'{"feature":"ranking"}', user=None)
            for _ in range(ai.ai_lane.limit * 4)
        ])
        for r in results:
            if getattr(r, "status_code", None) == 503:
                peak["shed"] += 1
        return results

    _await(drive())
    assert peak["n"] <= ai.ai_lane.limit
    assert peak["served"] + peak["shed"] == ai.ai_lane.limit * 4
    assert peak["shed"] > 0, "the point of the test is that some were shed"
    assert ai.ai_lane.in_flight == 0


# ---------- the event loop must stay clean ----------

def test_the_async_shell_does_no_blocking_work():
    """Anything blocking added to handle_ai stalls the loop for the WHOLE process — a
    strictly worse bug than the one item 2 fixes. The blocking calls all live in _serve_ai;
    this asserts none of their names have crept across the seam."""
    src = inspect.getsource(ai.handle_ai)
    for blocking in ("subscription_block_reason", "ai_allowance_state", "circuit_open",
                     "touch_user_activity", "call_gemini", "_anthropic_call",
                     "urlopen", "prompts.build", "limiter.allow"):
        assert blocking not in src, f"{blocking} must stay behind to_thread.run_sync"

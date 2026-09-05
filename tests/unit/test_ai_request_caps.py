"""Unit tests for the request caps in front of the two paid AI proxies — S0-2 in
SECURITY_HARDENING_PLAN.md, findings D1 (no throttle), D4 (no body limit) and M4.

What these pin, in the words of the live probes that found the holes (2026-09-03):
  * 12 rapid POSTs returned "200 x12, zero 429s"  -> _rate_limit_error
  * a 41,040-byte body returned 200 with 9,703 input tokens billed -> capped_raw_body

Both caps are checked BEFORE the upstream call — the body one is a dependency, so it fires
before the handler exists to make a call at all. No TestClient (this environment cannot open
the socketpair its event loop needs); the dependencies are awaited directly instead.
"""
import asyncio

import pytest
from fastapi import HTTPException

import app.deps as deps
import app.routes.ai as ai
from app.auth.ratelimit import RateLimiter


class FakeRequest:
    """The two attributes capped_raw_body touches: headers, and an async byte stream."""

    def __init__(self, chunks, content_length=None):
        self._chunks = chunks
        self.headers = {} if content_length is None else {"content-length": str(content_length)}
        self.streamed = False

    async def stream(self):
        self.streamed = True
        for chunk in self._chunks:
            yield chunk


def _await(coro):
    return asyncio.run(coro)


# ---------- capped_raw_body (D4 / M4) ----------

def test_body_under_the_cap_passes_through():
    dep = deps.capped_raw_body(100)
    req = FakeRequest([b"abc", b"def"], content_length=6)
    assert _await(dep(req)) == b"abcdef"


def test_oversized_content_length_is_refused_without_reading_the_body():
    """The point of checking the header first: an honest oversized upload is refused before
    a single byte is buffered."""
    dep = deps.capped_raw_body(10)
    req = FakeRequest([b"x" * 1000], content_length=1000)
    with pytest.raises(HTTPException) as exc:
        _await(dep(req))
    assert exc.value.status_code == 413
    assert req.streamed is False


def test_oversized_chunked_body_is_refused_mid_stream():
    """No Content-Length (or a lying one) must not be a bypass — the running total stops it."""
    dep = deps.capped_raw_body(10)
    req = FakeRequest([b"x" * 8, b"x" * 8, b"x" * 8], content_length=None)
    with pytest.raises(HTTPException) as exc:
        _await(dep(req))
    assert exc.value.status_code == 413


def test_a_lying_content_length_does_not_get_through():
    dep = deps.capped_raw_body(10)
    req = FakeRequest([b"x" * 500], content_length=1)
    with pytest.raises(HTTPException) as exc:
        _await(dep(req))
    assert exc.value.status_code == 413


def test_unparseable_content_length_falls_through_to_the_real_count():
    dep = deps.capped_raw_body(10)
    req = FakeRequest([b"ok"], content_length="not-a-number")
    assert _await(dep(req)) == b"ok"


def test_body_is_cached_so_a_second_read_still_works():
    """Consuming request.stream() would leave Starlette's Request.body() facing a spent
    stream; caching on _body — the attribute body() itself populates — keeps a downstream
    reader (an exception handler, a later dependency) working."""
    dep = deps.capped_raw_body(100)
    req = FakeRequest([b"abc"], content_length=3)
    assert _await(dep(req)) == b"abc"
    assert req._body == b"abc"
    req._chunks = [b"SHOULD NOT BE READ AGAIN"]
    assert _await(dep(req)) == b"abc"


def test_a_cached_body_over_the_cap_is_still_refused():
    dep = deps.capped_raw_body(2)
    req = FakeRequest([], content_length=None)
    req._body = b"way too long"
    with pytest.raises(HTTPException) as exc:
        _await(dep(req))
    assert exc.value.status_code == 413


def test_both_proxies_read_through_the_capped_dependency():
    """Wiring check: swapping either route back to the uncapped app.deps.raw_body reopens
    the billing lever, so assert the dependency by identity rather than by grepping."""
    import inspect
    for handler in (ai.handle_messages, ai.handle_messages_claude):
        param = inspect.signature(handler).parameters["raw_body"]
        assert param.default.dependency is ai.ai_raw_body
        assert param.default.dependency is not deps.raw_body


# ---------- the throttle (D1) ----------

def test_retry_after_counts_down_the_window():
    limiter = RateLimiter(1, 60)
    assert limiter.allow("k") is True
    assert limiter.allow("k") is False
    after = limiter.retry_after("k")
    assert 1 <= after <= 61


def test_retry_after_is_never_zero_for_an_unknown_key():
    assert RateLimiter(1, 60).retry_after("never-seen") >= 1


def test_nth_plus_one_request_is_429_with_retry_after(monkeypatch):
    """The exact probe that failed live: N+1 rapid POSTs, the last one must be refused."""
    limiter = RateLimiter(3, 60)
    monkeypatch.setattr(ai, "ai_user_limiter", limiter)
    monkeypatch.setattr(ai, "ai_ip_limiter", RateLimiter(1000, 60))

    for _ in range(3):
        assert ai._rate_limit_error("1.2.3.4", "alice") is None
    resp = ai._rate_limit_error("1.2.3.4", "alice")
    assert resp is not None and resp.status_code == 429
    assert int(resp.headers["Retry-After"]) >= 1


def test_the_two_buckets_are_independent(monkeypatch):
    """Not one composite (ip, user) key: that would let one attacker with N accounts, or one
    account across N addresses, multiply the ceiling by N. Spending the IP bucket must stop
    a caller whose own user bucket is untouched."""
    monkeypatch.setattr(ai, "ai_ip_limiter", RateLimiter(2, 60))
    monkeypatch.setattr(ai, "ai_user_limiter", RateLimiter(1000, 60))

    assert ai._rate_limit_error("1.2.3.4", "alice") is None
    assert ai._rate_limit_error("1.2.3.4", "bob") is None
    blocked = ai._rate_limit_error("1.2.3.4", "carol")     # same IP, a third account
    assert blocked is not None and blocked.status_code == 429
    assert ai._rate_limit_error("5.6.7.8", "carol") is None  # different IP, same account


def test_a_signed_out_caller_is_still_throttled_by_ip(monkeypatch):
    """Mock mode is reachable signed-out (S0-1), so the IP bucket is the only thing bounding
    it — and it must not key the user bucket on the IP, which would double-count."""
    monkeypatch.setattr(ai, "ai_ip_limiter", RateLimiter(1, 60))
    monkeypatch.setattr(ai, "ai_user_limiter", RateLimiter(1000, 60))
    assert ai._rate_limit_error("1.2.3.4", None) is None
    blocked = ai._rate_limit_error("1.2.3.4", None)
    assert blocked is not None and blocked.status_code == 429


def test_handlers_throttle_before_spending(monkeypatch):
    """End-to-end through the handler: once the bucket is spent, neither the provider nor the
    account lookup behind the subscription gate is reached."""
    monkeypatch.setattr(ai, "ai_ip_limiter", RateLimiter(0, 60))
    monkeypatch.setattr(ai, "ai_user_limiter", RateLimiter(1000, 60))
    for attr in ("touch_user_activity", "_proxy_to_gemini", "_proxy_to_anthropic",
                 "_mock_response", "_ai_access_error"):
        monkeypatch.setattr(ai, attr, lambda *a, **k: pytest.fail("reached past the throttle"))
    monkeypatch.setattr(ai, "client_ip", lambda _r: "1.2.3.4")

    for handler in (ai.handle_messages, ai.handle_messages_claude):
        resp = handler(request=None, raw_body=b"{}", user=None)
        assert resp.status_code == 429

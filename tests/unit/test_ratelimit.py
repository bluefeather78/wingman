"""Unit tests for app.auth.ratelimit — sliding-window RateLimiter.

Time is controlled by monkeypatching the module's `time.time`. A fresh RateLimiter
is built per test so there is no shared state to reset; the module singletons
(login_limiter/register_limiter) are asserted separately.
"""
import pytest

import app.auth.ratelimit as R
from app.auth.ratelimit import RateLimiter, login_limiter, register_limiter


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def clock(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(R.time, "time", c)
    return c


def test_within_limit_true(clock):
    rl = RateLimiter(max_hits=3, window_seconds=60)
    assert rl.allow("ip") is True
    assert rl.allow("ip") is True
    assert rl.allow("ip") is True  # 3rd hit still allowed


def test_at_max_hits_boundary_blocks(clock):
    rl = RateLimiter(max_hits=3, window_seconds=60)
    for _ in range(3):
        assert rl.allow("ip") is True
    # 4th within the window is over the limit
    assert rl.allow("ip") is False
    # ...and stays blocked while time doesn't advance
    assert rl.allow("ip") is False


def test_eviction_of_old_hits(clock):
    rl = RateLimiter(max_hits=2, window_seconds=60)
    assert rl.allow("ip") is True     # t=1000
    assert rl.allow("ip") is True     # t=1000
    assert rl.allow("ip") is False    # over limit
    # Advance past the window: old hits (<= now-window) are evicted.
    clock.t += 61
    assert rl.allow("ip") is True
    assert rl.allow("ip") is True
    assert rl.allow("ip") is False


def test_boundary_hit_exactly_at_window_edge_evicted(clock):
    # cutoff = now - window; eviction is `dq[0] <= cutoff`, so a hit exactly one
    # window old is evicted.
    rl = RateLimiter(max_hits=1, window_seconds=60)
    assert rl.allow("ip") is True     # t=1000
    assert rl.allow("ip") is False    # t=1000, over limit
    clock.t = 1060                    # now-window = 1000 == first hit -> evicted
    assert rl.allow("ip") is True


def test_per_key_isolation(clock):
    rl = RateLimiter(max_hits=1, window_seconds=60)
    assert rl.allow("ip-a") is True
    assert rl.allow("ip-a") is False
    # A different key has its own independent window.
    assert rl.allow("ip-b") is True
    assert rl.allow("ip-b") is False


def test_opportunistic_sweep_of_idle_keys(clock):
    # Fill past the 4096 cap with distinct keys, then advance past the window and add one
    # more key: the len>4096 branch fires and _sweep evicts the now-stale keys.
    rl = RateLimiter(max_hits=1, window_seconds=60)
    for i in range(4097):
        assert rl.allow(f"k{i}") is True
    assert len(rl._hits) == 4097
    clock.t += 61  # all recorded hits are now older than the window
    assert rl.allow("fresh") is True
    # Stale keys swept; only the fresh one remains.
    assert len(rl._hits) == 1
    assert "fresh" in rl._hits


def test_module_singletons_configured():
    assert isinstance(login_limiter, RateLimiter)
    assert (login_limiter.max_hits, login_limiter.window) == (10, 5 * 60)
    assert (register_limiter.max_hits, register_limiter.window) == (10, 60 * 60)

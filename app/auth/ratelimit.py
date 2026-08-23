"""A minimal in-process sliding-window rate limiter for the credential routes.

This is deliberately small: today /api/login and /api/register accept unlimited attempts,
so even a per-IP counter is a strict improvement over nothing. Two honest limitations,
recorded rather than hidden:
  * It is PER PROCESS. Scale to multiple uvicorn workers and each keeps its own window, so
    the effective limit multiplies by the worker count. A shared store (Redis/Postgres) is
    the real fix and is out of scope for this phase.
  * It keys on the client IP from the socket. Behind a proxy that is the proxy's IP unless
    the platform sets request.client from a forwarded header; on Render the app sees the
    real client. Good enough to blunt brute force, not a DDoS defence.
"""
import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self, max_hits, window_seconds):
        self.max_hits = max_hits
        self.window = window_seconds
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key):
        """Record an attempt for `key`; return True if it is within the limit."""
        now = time.time()
        with self._lock:
            dq = self._hits[key]
            cutoff = now - self.window
            while dq and dq[0] <= cutoff:
                dq.popleft()
            if len(dq) >= self.max_hits:
                return False
            dq.append(now)
            # Opportunistic cleanup so idle keys don't accumulate forever.
            if len(self._hits) > 4096:
                self._sweep(cutoff)
            return True

    def _sweep(self, cutoff):
        for k in [k for k, v in self._hits.items() if not v or v[-1] <= cutoff]:
            del self._hits[k]


# 10 sign-in attempts per IP per 5 minutes; 10 registrations per IP per hour. Generous
# enough that a real user fumbling their password is never stopped, tight enough that
# scripted guessing against the unlimited endpoints it replaces is throttled.
login_limiter = RateLimiter(10, 5 * 60)
register_limiter = RateLimiter(10, 60 * 60)

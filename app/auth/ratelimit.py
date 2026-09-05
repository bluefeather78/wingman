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

from app.config import (AI_RATE_LIMIT_PER_USER, AI_RATE_LIMIT_PER_IP,
                        AI_RATE_LIMIT_WINDOW_SECONDS)


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

    def retry_after(self, key):
        """Whole seconds until `key`'s oldest recorded hit falls out of the window, i.e. the
        soonest allow() could succeed again. This is the Retry-After value; the floor of 1
        keeps a client from being told to retry immediately."""
        now = time.time()
        with self._lock:
            dq = self._hits.get(key)
            if not dq:
                return 1
            return max(1, int(dq[0] + self.window - now) + 1)

    def _sweep(self, cutoff):
        for k in [k for k, v in self._hits.items() if not v or v[-1] <= cutoff]:
            del self._hits[k]


# Sign-in: 10 attempts per (IP, userid) per 5 minutes, plus a loose per-IP backstop.
#
# It used to be one bucket keyed on the IP alone (S0-7, finding H3). As deployed that was one
# bucket for the ENTIRE USER BASE, because uvicorn ignored X-Forwarded-For and every visitor
# on earth arrived as Render's load balancer — so ten POST /api/login bodies locked every
# student out of sign-in for five minutes, for free, repeatably.
#
# Keying on (IP, userid) is what stops one caller locking out other people's accounts. The
# per-IP backstop is kept because the narrow key alone lets one address rotate userids
# forever, which is credential stuffing; it is deliberately loose, since a school NAT puts a
# whole cohort of legitimate sign-ins behind one address at the start of a class.
login_limiter = RateLimiter(10, 5 * 60)
login_ip_limiter = RateLimiter(100, 5 * 60)
# 10 registrations per IP per hour, unchanged. Its site-wide-capacity problem was the same
# forwarded-IP bug, not the key.
register_limiter = RateLimiter(10, 60 * 60)

# The AI proxies (S0-2, findings D1/D4). Two buckets rather than one composite (ip, user)
# key: a per-(ip,user) bucket would let one attacker with N accounts, or one account across N
# addresses, multiply the ceiling by N. Checking both means neither dimension is free.
# Rationale for the numbers, including why the per-IP bucket is the loose one, is on the
# constants in app/config.py.
ai_user_limiter = RateLimiter(AI_RATE_LIMIT_PER_USER, AI_RATE_LIMIT_WINDOW_SECONDS)
ai_ip_limiter = RateLimiter(AI_RATE_LIMIT_PER_IP, AI_RATE_LIMIT_WINDOW_SECONDS)

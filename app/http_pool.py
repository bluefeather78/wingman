"""A pooled HTTP client for this service's Supabase calls — Phase 2 item 4.

Every Supabase read and write went through urllib.request.urlopen(), which opens a NEW TCP
connection and does a full TLS handshake per call and throws it away. A signed-in request
makes several of those, and each does perhaps 10-40ms of real work behind a handshake to
another region — so the setup, not the query, was the larger half of the bill.

SCOPE: Supabase only. Shama, 2026-09-05. The Anthropic call in app/routes/ai.py is
deliberately NOT pooled: it is a 10-30 second request, so a reused handshake saves well under
1% of it, and touching it would be a MARQUEE M9 change needing its own approval to buy almost
nothing. Google's OAuth calls are out of scope for the same "not where the win is" reason.

WHY A SHIM RATHER THAN A REWRITE. Eleven call sites build a urllib.request.Request and read
the response, and their error handling is load-bearing in ways that are easy to miss: the
catalog fetch retries specifically on a PostgREST 57014 by reading the body off an HTTPError,
select_user and get_user_account degrade to `SELECT *` on a 400, and the promo write reads a
representation back. Every one of those inspects urllib.error.HTTPError.code and .read().

So pooled_urlopen takes the SAME Request object those sites already build and raises the SAME
urllib.error.HTTPError they already catch. The diff at each site is one identifier. Rewriting
eleven callers onto a native httpx response — each with its own error semantics to re-derive —
is how a latency change turns into an outage.
"""
import io
import socket
import threading
import urllib.error

import httpx

# Sized against the anyio threadpool (40 slots), which is what bounds how many of these can be
# in flight in this process. max_connections above that would be capacity nothing can reach;
# below it would make the pool itself the queue. Keepalive is deliberately lower: idle
# connections cost memory on a 512 MB instance, and Supabase's own idle timeout is the real
# ceiling anyway — an expired connection is reopened transparently, it is not an error.
_LIMITS = httpx.Limits(max_connections=40, max_keepalive_connections=20,
                       keepalive_expiry=30.0)
_DEFAULT_TIMEOUT = 15.0

# Transport errors where the request did NOT get a response, so re-sending it on a fresh
# connection is safe. This is the errno-54 "connection reset by peer" that the ops Metrics
# poll kept hitting: the pool believed a keepalive connection was alive, but the peer
# (Supabase, or a proxy in front of it) had already idle-closed it — so the request never
# reached a live handler. httpcore discards the failed connection, so the retry gets a new
# one. keepalive_expiry alone cannot close this: the reset can arrive on a connection that
# has not yet expired, and lowering it far enough to help would cost a handshake on nearly
# every request under Render's steady traffic to save nothing there — the win is here, on a
# long-idle local process, and a single retry is the targeted fix.
#
# Read/write TIMEOUTS are deliberately excluded (they fall to the TimeoutException branch and
# are NOT retried): a timeout means the request may already have been received, so blindly
# re-sending a write could apply it twice. A connect-phase timeout IS safe and is included.
_RETRYABLE_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadError,
                     httpx.WriteError, httpx.RemoteProtocolError, httpx.PoolTimeout)

_client = None
_client_lock = threading.Lock()


def get_client():
    """The one process-wide pooled client, created on first use.

    Lazily rather than at import: app.core is imported by tooling and tests that never make a
    request, and an httpx.Client built at import time would open its transport in every one of
    them. Double-checked under a lock because worker threads race here on the first request
    after a cold start — the exact moment this is most likely to be hit concurrently.
    """
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = httpx.Client(limits=_LIMITS, timeout=_DEFAULT_TIMEOUT,
                                       follow_redirects=False)
    return _client


def reset_client():
    """Drop the pooled client and its connections. For tests, and for an ops-console reload."""
    global _client
    with _client_lock:
        if _client is not None:
            try:
                _client.close()
            except Exception:                                      # noqa: BLE001
                pass
        _client = None


class PooledResponse:
    """The parts of a urllib response the call sites actually use, over an httpx one."""

    def __init__(self, response):
        self._response = response

    def read(self):
        return self._response.content

    @property
    def status(self):
        return self._response.status_code

    @property
    def code(self):
        return self._response.status_code

    def getcode(self):
        return self._response.status_code

    @property
    def headers(self):
        return self._response.headers

    def info(self):
        return self._response.headers

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def pooled_urlopen(req, timeout=None):
    """urlopen() over the shared pool, for a urllib.request.Request.

    Raises urllib.error.HTTPError on a 4xx/5xx and urllib.error.URLError on a transport
    failure, exactly as urlopen does — the eleven Supabase call sites catch those and inspect
    `.code` and `.read()`, and all of that keeps working untouched.
    """
    client = get_client()
    body = req.data
    headers = dict(req.header_items())
    method = req.get_method()
    to = _DEFAULT_TIMEOUT if timeout is None else timeout
    attempt = 0
    while True:
        try:
            response = client.request(method, req.full_url, content=body,
                                      headers=headers, timeout=to)
            break
        except _RETRYABLE_ERRORS as e:
            # Retry ONCE, on a fresh connection, for a stale-connection failure. A second
            # failure is a real outage, not a dead pooled socket — surface it as urlopen would.
            if attempt == 0:
                attempt += 1
                continue
            if isinstance(e, (httpx.ConnectTimeout, httpx.PoolTimeout)):
                raise urllib.error.URLError(socket.timeout(str(e) or "timed out")) from e
            raise urllib.error.URLError(str(e) or type(e).__name__) from e
        except httpx.TimeoutException as e:
            # socket.timeout is what urlopen raises through URLError on a timeout, and some
            # callers distinguish it from a refused connection. A read/write timeout is NOT
            # retried above: the request may already have been received.
            raise urllib.error.URLError(socket.timeout(str(e) or "timed out")) from e
        except httpx.HTTPError as e:
            raise urllib.error.URLError(str(e) or type(e).__name__) from e

    if response.status_code >= 400:
        # fp is a real file-like object so HTTPError.read() returns the provider's body — the
        # catalog's 57014 retry and the two SELECT-* degrades both parse it.
        raise urllib.error.HTTPError(
            req.full_url, response.status_code,
            response.reason_phrase or "", response.headers,
            io.BytesIO(response.content))
    return PooledResponse(response)

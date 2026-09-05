"""Unit tests for the pooled Supabase client — Phase 2 item 4.

Every Supabase read and write opened a new TCP connection and did a full TLS handshake, then
threw it away. A signed-in request makes several, each doing 10-40ms of real work behind a
handshake to another region.

The point of these tests is the SHIM CONTRACT, not the pooling. Eleven call sites keep building
a urllib.request.Request and catching urllib.error.HTTPError, and their error handling is
load-bearing in ways that are quiet when broken:
  * app/services/opportunities.py retries a PostgREST 57014 by READING THE BODY off the error.
    If HTTPError.read() came back empty, the catalog would stop retrying statement timeouts and
    the "Search failed" bug this repo already shipped a fix for would come straight back.
  * select_user and get_user_account degrade to SELECT * on a 400, which they detect via .code.
Both must survive the move off urlopen, so both are pinned here.
"""
import urllib.error
import urllib.request

import httpx
import pytest

import app.http_pool as pool


class _FakeResponse:
    def __init__(self, status=200, content=b"", headers=None, reason="OK"):
        self.status_code = status
        self.content = content
        self.headers = headers or {}
        self.reason_phrase = reason


class _FakeClient:
    def __init__(self, response=None, raises=None):
        self._response = response
        self._raises = raises
        self.calls = []

    def request(self, method, url, content=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "content": content,
                           "headers": headers, "timeout": timeout})
        if self._raises:
            raise self._raises
        return self._response


def _req(url="https://db.example/rest/v1/users?select=userid", data=None, method=None):
    return urllib.request.Request(url, data=data, method=method,
                                  headers={"apikey": "svc", "Content-Type": "application/json"})


@pytest.fixture(autouse=True)
def _no_real_client():
    pool.reset_client()
    yield
    pool.reset_client()


# ---------- the success path ----------

def test_a_200_is_returned_as_a_readable_response(monkeypatch):
    client = _FakeClient(_FakeResponse(200, b'[{"userid":"alice"}]'))
    monkeypatch.setattr(pool, "get_client", lambda: client)
    with pool.pooled_urlopen(_req()) as resp:
        assert resp.read() == b'[{"userid":"alice"}]'
        assert resp.status == 200
        assert resp.getcode() == 200


def test_the_request_is_forwarded_verbatim(monkeypatch):
    """Method, URL, body and headers all have to survive — PostgREST is picky about all four."""
    client = _FakeClient(_FakeResponse(201))
    monkeypatch.setattr(pool, "get_client", lambda: client)
    pool.pooled_urlopen(_req(data=b'{"a":1}', method="PATCH"), timeout=7)
    call = client.calls[0]
    assert call["method"] == "PATCH"
    assert call["content"] == b'{"a":1}'
    assert call["timeout"] == 7
    assert call["headers"]["Apikey"] == "svc" or call["headers"]["apikey"] == "svc"


# ---------- the error contract, which is what the call sites depend on ----------

def test_a_400_raises_an_httperror_carrying_the_code(monkeypatch):
    """select_user and get_user_account degrade to SELECT * by reading .code off this."""
    client = _FakeClient(_FakeResponse(400, b'{"message":"column x does not exist"}'))
    monkeypatch.setattr(pool, "get_client", lambda: client)
    with pytest.raises(urllib.error.HTTPError) as exc:
        pool.pooled_urlopen(_req())
    assert exc.value.code == 400


def test_the_error_body_is_readable(monkeypatch):
    """The catalog's statement-timeout retry finds '57014' by reading this body. An empty
    read() here silently disables that retry and brings back a user-facing 'Search failed'."""
    client = _FakeClient(_FakeResponse(
        500, b'{"code":"57014","message":"canceling statement due to statement timeout"}'))
    monkeypatch.setattr(pool, "get_client", lambda: client)
    with pytest.raises(urllib.error.HTTPError) as exc:
        pool.pooled_urlopen(_req())
    assert b"57014" in exc.value.read()


def test_the_real_catalog_retry_predicate_still_recognises_a_pooled_error(monkeypatch):
    """End to end against the actual predicate, not a copy of it."""
    from app.services.opportunities import _is_statement_timeout
    client = _FakeClient(_FakeResponse(500, b'{"code":"57014","message":"canceling statement"}'))
    monkeypatch.setattr(pool, "get_client", lambda: client)
    with pytest.raises(urllib.error.HTTPError) as exc:
        pool.pooled_urlopen(_req())
    assert _is_statement_timeout(exc.value) is True


def test_a_timeout_becomes_a_urlerror(monkeypatch):
    client = _FakeClient(raises=httpx.ReadTimeout("too slow"))
    monkeypatch.setattr(pool, "get_client", lambda: client)
    with pytest.raises(urllib.error.URLError):
        pool.pooled_urlopen(_req())


def test_a_transport_failure_becomes_a_urlerror(monkeypatch):
    client = _FakeClient(raises=httpx.ConnectError("no route"))
    monkeypatch.setattr(pool, "get_client", lambda: client)
    with pytest.raises(urllib.error.URLError):
        pool.pooled_urlopen(_req())


# ---------- the pool itself ----------

def test_the_client_is_created_once_and_reused():
    """If a new client were built per call there would be no pool and no point."""
    first = pool.get_client()
    assert pool.get_client() is first


def test_reset_client_drops_it():
    first = pool.get_client()
    pool.reset_client()
    assert pool.get_client() is not first


def test_the_pool_is_sized_against_the_threadpool():
    """40 is the anyio threadpool's slot count — the real bound on in-flight requests here.
    Above it is capacity nothing can reach; below it makes the pool itself the queue."""
    assert pool._LIMITS.max_connections == 40
    assert pool._LIMITS.max_keepalive_connections <= pool._LIMITS.max_connections


def test_the_anthropic_call_is_not_pooled():
    """Shama, 2026-09-05: Supabase only. Pooling the provider call would be a MARQUEE M9
    change, and saves under 1% of a 10-30 second request."""
    import inspect
    import app.routes.ai as ai
    src = inspect.getsource(ai._anthropic_call)
    assert "urllib.request.urlopen" in src
    assert "pooled_urlopen" not in src

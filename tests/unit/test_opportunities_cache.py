"""The catalog and vector caches — Phase 2 item 5, plus the degrade/retry behaviour that
predates it. pooled_urlopen is monkeypatched so no real network is touched.

Item 5 split ONE cache holding every row WITH its 768-dim match_vector, on a 5-minute TTL,
into two with very different change rates: a vector-free catalog on the original short TTL,
and the embeddings behind a 24h backstop. That removed ~20MB pulled from Supabase every five
minutes to serve a column the browser never receives.

Everything the single cache used to guarantee still has a test here — it just moved to
whichever half now owns it:
  * a 400 naming match_vector latches the column off and the app keeps working -> fetch_vectors
  * a latched-off process never asks for the column again              -> fetch_vectors
  * a 57014 statement timeout on a page is retried, other 500s are not -> either fetch
  * a transient failure serves a stale cache; a first-ever failure raises -> both

And the new invariants, of which the first two are the ones that would break silently:
  * BROWSING NEVER PULLS VECTORS. If it ever does again, the split has quietly undone itself
    and the statement-timeout scar comes back with it.
  * A BUST CLEARS BOTH. Busting only the catalog would leave a newly-activated listing
    un-matchable for a day while looking perfectly fine in the browser.
"""
import io
import json
import urllib.error

import pytest

from app.services import opportunities as opp


class _FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _http_400(body):
    return urllib.error.HTTPError(
        url="http://x", code=400, msg="Bad Request", hdrs=None,
        fp=io.BytesIO(json.dumps(body).encode()))


def _http_500(body):
    return urllib.error.HTTPError(
        url="http://x", code=500, msg="Internal Server Error", hdrs=None,
        fp=io.BytesIO(json.dumps(body).encode()))


_STATEMENT_TIMEOUT = {"code": "57014", "message": "canceling statement due to statement timeout"}
_MISSING_COLUMN = {"code": "42703", "message": "column opportunities.match_vector does not exist"}


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    """Fresh caches and a re-armed latch for every test."""
    monkeypatch.setattr(opp, "_catalog_cache",
                        {"rows": None, "fetched_at": 0.0, "body": None,
                         "gzip": None, "etag": None})
    monkeypatch.setattr(opp, "_vector_cache", {"vectors": None, "fetched_at": 0.0})
    monkeypatch.setattr(opp, "_match_vector_available", True)
    monkeypatch.setattr(opp, "SUPABASE_URL", "http://supa", raising=False)
    monkeypatch.setattr(opp, "SUPABASE_ANON_KEY", "anon", raising=False)


def _serve(monkeypatch, rows, seen=None):
    def fake_urlopen(req, timeout=10):
        if seen is not None:
            seen.append(req.full_url)
        return _FakeResp(json.dumps(rows).encode())
    monkeypatch.setattr(opp, "pooled_urlopen", fake_urlopen)


# ---------- the split ----------

def test_browsing_never_pulls_the_vector_column(monkeypatch):
    """The whole point of item 5. A regression here is invisible until the catalog fetch
    starts timing out against Supabase again."""
    seen = []
    _serve(monkeypatch, [{"id": "a", "name": "N"}], seen)
    opp.fetch_opportunities()
    assert seen, "no request was made"
    assert all("match_vector" not in u for u in seen)


def test_matching_gets_the_vectors_attached(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout=10):
        calls.append(req.full_url)
        if "match_vector" in req.full_url:
            return _FakeResp(json.dumps([{"id": "a", "match_vector": [0.1, 0.2]}]).encode())
        return _FakeResp(json.dumps([{"id": "a", "name": "N"}]).encode())

    monkeypatch.setattr(opp, "pooled_urlopen", fake_urlopen)
    rows = opp.fetch_opportunities_with_vectors()
    assert rows == [{"id": "a", "name": "N", "match_vector": [0.1, 0.2]}]
    assert any("match_vector" in u for u in calls)


def test_a_bust_clears_both_caches(monkeypatch):
    """Busting only the catalog would leave a newly-activated listing un-matchable for a day
    while looking perfectly fine in the browser."""
    _serve(monkeypatch, [{"id": "a"}])
    opp.fetch_opportunities()
    opp.fetch_vectors()
    assert opp._catalog_cache["fetched_at"] > 0
    assert opp._vector_cache["fetched_at"] > 0
    opp.bust_catalog_cache()
    assert opp._catalog_cache["fetched_at"] == 0.0
    assert opp._vector_cache["fetched_at"] == 0.0


def test_the_ops_console_busts_through_the_function():
    """ops/core.py must not reach into a cache dict — a third cache added later would be
    forgotten by all four of its call sites."""
    import inspect
    import ops.core
    src = inspect.getsource(ops.core)
    assert "_opportunities_cache" not in src
    assert "bust_catalog_cache()" in src


def test_the_catalog_is_cached_between_calls(monkeypatch):
    seen = []
    _serve(monkeypatch, [{"id": "a"}], seen)
    opp.fetch_opportunities()
    opp.fetch_opportunities()
    assert len(seen) == 1


def test_the_vector_cache_is_reused_between_calls(monkeypatch):
    seen = []
    _serve(monkeypatch, [{"id": "a", "match_vector": [0.1]}], seen)
    opp.fetch_vectors()
    opp.fetch_vectors()
    assert len(seen) == 1


# ---------- the pre-serialised payload ----------

def test_the_payload_is_serialised_once_per_refresh(monkeypatch):
    _serve(monkeypatch, [{"id": "a", "name": "N"}])
    body, gzipped, etag = opp.catalog_payload()
    assert json.loads(body) == [{"id": "a", "name": "N"}]
    assert etag.startswith('"') and etag.endswith('"')
    import gzip as _gz
    assert _gz.decompress(gzipped) == body
    assert opp.catalog_payload()[2] == etag, "the ETag must be stable between requests"


def test_the_etag_changes_when_the_catalog_does(monkeypatch):
    _serve(monkeypatch, [{"id": "a"}])
    first = opp.catalog_payload()[2]
    opp.bust_catalog_cache()
    _serve(monkeypatch, [{"id": "a"}, {"id": "b"}])
    assert opp.catalog_payload()[2] != first


# ---------- degrade until migrated (moved to fetch_vectors) ----------

def test_degrades_when_match_vector_missing(monkeypatch):
    def fake_urlopen(req, timeout=10):
        raise _http_400(_MISSING_COLUMN)
    monkeypatch.setattr(opp, "pooled_urlopen", fake_urlopen)
    assert opp.fetch_vectors() == {}
    assert opp._match_vector_available is False


def test_the_catalog_still_serves_when_the_column_is_missing(monkeypatch):
    """A missing column must never take down /api/opportunities, which is the whole app's
    data source."""
    def fake_urlopen(req, timeout=10):
        if "match_vector" in req.full_url:
            raise _http_400(_MISSING_COLUMN)
        return _FakeResp(json.dumps([{"id": "a", "name": "N"}]).encode())
    monkeypatch.setattr(opp, "pooled_urlopen", fake_urlopen)
    assert opp.fetch_opportunities_with_vectors() == [{"id": "a", "name": "N"}]


def test_latched_off_skips_the_vector_select(monkeypatch):
    opp._match_vector_available = False
    seen = []
    _serve(monkeypatch, [{"id": "a"}], seen)
    assert opp.fetch_vectors() == {}
    assert seen == [], "a latched-off process must not ask for the column again"


def test_vectors_are_keyed_by_id(monkeypatch):
    _serve(monkeypatch, [{"id": "a", "match_vector": [0.1]},
                         {"id": "b", "match_vector": None}])
    assert opp.fetch_vectors() == {"a": [0.1]}, "a null vector is not a vector"


# ---------- transport failures ----------

def test_non_column_failure_with_no_cache_raises(monkeypatch):
    def fake_urlopen(req, timeout=10):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(opp, "pooled_urlopen", fake_urlopen)
    with pytest.raises(Exception):
        opp.fetch_opportunities()


def test_statement_timeout_page_is_retried(monkeypatch):
    """A 57014 is a transient DB-load spike, not a dead catalog — retrying it is what stopped
    the user-facing 'Search failed'."""
    monkeypatch.setattr(opp.time, "sleep", lambda *_: None)
    attempts = []

    def fake_urlopen(req, timeout=10):
        attempts.append(req.full_url)
        if len(attempts) == 1:
            raise _http_500(_STATEMENT_TIMEOUT)
        return _FakeResp(json.dumps([{"id": "a"}]).encode())

    monkeypatch.setattr(opp, "pooled_urlopen", fake_urlopen)
    assert opp.fetch_opportunities() == [{"id": "a"}]
    assert len(attempts) == 2


def test_non_timeout_500_is_not_retried(monkeypatch):
    monkeypatch.setattr(opp.time, "sleep", lambda *_: None)
    attempts = []

    def fake_urlopen(req, timeout=10):
        attempts.append(req.full_url)
        raise _http_500({"code": "42P01", "message": "some other server error"})

    monkeypatch.setattr(opp, "pooled_urlopen", fake_urlopen)
    with pytest.raises(urllib.error.HTTPError):
        opp.fetch_opportunities()
    assert len(attempts) == 1


def test_transient_failure_serves_a_stale_catalog(monkeypatch):
    opp._catalog_cache.update({"rows": [{"id": "cached"}], "fetched_at": 0.0})

    def fake_urlopen(req, timeout=10):
        raise urllib.error.URLError("temporary")
    monkeypatch.setattr(opp, "pooled_urlopen", fake_urlopen)
    assert opp.fetch_opportunities() == [{"id": "cached"}]


def test_transient_failure_serves_stale_vectors(monkeypatch):
    opp._vector_cache.update({"vectors": {"a": [0.1]}, "fetched_at": 0.0})

    def fake_urlopen(req, timeout=10):
        raise urllib.error.URLError("temporary")
    monkeypatch.setattr(opp, "pooled_urlopen", fake_urlopen)
    assert opp.fetch_vectors() == {"a": [0.1]}

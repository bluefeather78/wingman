"""fetch_opportunities' degrade-until-migrated behavior for match_vector. urllib.request.urlopen
is monkeypatched so no real network is touched (the conftest guard would block it anyway) —
what's pinned is that a 400 naming match_vector drops the column and refetches, keeping the
catalog endpoint alive, and that a genuine failure with no cache still raises.
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


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    # fresh cache + re-armed latch for every test
    monkeypatch.setattr(opp, "_opportunities_cache", {"data": None, "fetched_at": 0.0})
    monkeypatch.setattr(opp, "_match_vector_available", True)
    monkeypatch.setattr(opp, "SUPABASE_URL", "http://supa", raising=False)
    monkeypatch.setattr(opp, "SUPABASE_ANON_KEY", "anon", raising=False)


def test_degrades_when_match_vector_missing(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout=10):
        url = req.full_url
        calls.append(url)
        if "match_vector" in url:
            raise _http_400({"code": "42703",
                             "message": "column opportunities.match_vector does not exist"})
        # the retry select (no match_vector) succeeds with one short page
        return _FakeResp(json.dumps([{"id": "a", "name": "N"}]).encode())

    monkeypatch.setattr(opp.urllib.request, "urlopen", fake_urlopen)
    data = opp.fetch_opportunities()
    assert data == [{"id": "a", "name": "N"}]
    assert opp._match_vector_available is False          # latched off
    assert any("match_vector" in u for u in calls)        # tried full first
    assert any("match_vector" not in u for u in calls)    # then the stripped retry


def test_latched_off_skips_the_vector_select(monkeypatch):
    opp._match_vector_available = False
    seen = []

    def fake_urlopen(req, timeout=10):
        seen.append(req.full_url)
        return _FakeResp(json.dumps([{"id": "a"}]).encode())

    monkeypatch.setattr(opp.urllib.request, "urlopen", fake_urlopen)
    opp.fetch_opportunities()
    assert all("match_vector" not in u for u in seen)     # never even attempts it


def test_full_select_when_column_present(monkeypatch):
    def fake_urlopen(req, timeout=10):
        assert "match_vector" in req.full_url                # includes the vector
        return _FakeResp(json.dumps([{"id": "a", "match_vector": [0.1]}]).encode())

    monkeypatch.setattr(opp.urllib.request, "urlopen", fake_urlopen)
    data = opp.fetch_opportunities()
    assert data[0]["match_vector"] == [0.1]
    assert opp._match_vector_available is True


def test_non_column_failure_with_no_cache_raises(monkeypatch):
    def fake_urlopen(req, timeout=10):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(opp.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(Exception):
        opp.fetch_opportunities()


def test_statement_timeout_page_is_retried(monkeypatch):
    # A page that 500s with a statement timeout (57014) is a transient DB-load spike, not a
    # dead catalog — retry it rather than failing the whole fetch (that is what surfaced the
    # 502 to the app). First attempt times out, second succeeds.
    monkeypatch.setattr(opp.time, "sleep", lambda *_: None)  # no real backoff in the test
    attempts = []

    def fake_urlopen(req, timeout=10):
        attempts.append(req.full_url)
        if len(attempts) == 1:
            raise _http_500(_STATEMENT_TIMEOUT)
        return _FakeResp(json.dumps([{"id": "a", "match_vector": [0.1]}]).encode())

    monkeypatch.setattr(opp.urllib.request, "urlopen", fake_urlopen)
    data = opp.fetch_opportunities()
    assert data == [{"id": "a", "match_vector": [0.1]}]
    assert len(attempts) == 2  # retried once past the timeout


def test_non_timeout_500_is_not_retried(monkeypatch):
    # A 500 that is NOT a statement timeout is not a spike we can wait out — surface it (no
    # cache to fall back on here), and do not spend the retry budget on it.
    monkeypatch.setattr(opp.time, "sleep", lambda *_: None)
    attempts = []

    def fake_urlopen(req, timeout=10):
        attempts.append(req.full_url)
        raise _http_500({"code": "42P01", "message": "some other server error"})

    monkeypatch.setattr(opp.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(urllib.error.HTTPError):
        opp.fetch_opportunities()
    assert len(attempts) == 1  # no retry


def test_transient_failure_serves_stale_cache(monkeypatch):
    opp._opportunities_cache = {"data": [{"id": "cached"}], "fetched_at": 0.0}  # stale (age huge)

    def fake_urlopen(req, timeout=10):
        raise urllib.error.URLError("temporary")

    monkeypatch.setattr(opp.urllib.request, "urlopen", fake_urlopen)
    assert opp.fetch_opportunities() == [{"id": "cached"}]

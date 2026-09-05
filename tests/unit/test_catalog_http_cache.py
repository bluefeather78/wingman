"""ETag / gzip / 304 on GET /api/opportunities — Phase 2 item 5.

The catalog is by far the largest thing a student downloads, and it was re-serialised from
~1,700 dicts and sent in full on every single request, with no way for a returning browser to
say "I already have this". On 0.1 of a core that serialisation is real CPU, paid for bytes
identical between refreshes.

No TestClient — this environment cannot open the socketpair its event loop needs (see
test_ai_request_caps.py) — so the handler is called directly with the dependency values.
"""
import gzip
import json

import pytest

import app.routes.opportunities as opps


class _Req:
    def __init__(self, **headers):
        self.headers = {k.replace("_", "-"): v for k, v in headers.items()}


ROWS = [{"id": "a", "name": "Program"}]
BODY = json.dumps(ROWS).encode()
GZIP = gzip.compress(BODY, compresslevel=6)
ETAG = '"abc123"'


@pytest.fixture(autouse=True)
def _stub(monkeypatch):
    monkeypatch.setattr(opps, "SUPABASE_URL", "http://supa", raising=False)
    monkeypatch.setattr(opps, "SUPABASE_ANON_KEY", "anon", raising=False)
    monkeypatch.setattr(opps, "catalog_payload", lambda: (BODY, GZIP, ETAG))


# ---------- the If-None-Match parser ----------

def test_a_matching_etag_is_recognised():
    assert opps._etag_matches(ETAG, ETAG) is True


def test_a_weak_validator_is_recognised():
    """A revalidating browser sends W/"..."; comparing raw strings would miss it and turn every
    conditional request back into a full catalog download."""
    assert opps._etag_matches('W/' + ETAG, ETAG) is True


def test_one_of_several_candidates_is_recognised():
    assert opps._etag_matches('"other", ' + ETAG, ETAG) is True


def test_a_star_matches():
    assert opps._etag_matches("*", ETAG) is True


def test_a_different_etag_does_not_match():
    assert opps._etag_matches('"stale"', ETAG) is False


def test_no_header_does_not_match():
    assert opps._etag_matches(None, ETAG) is False
    assert opps._etag_matches("", ETAG) is False


# ---------- the route ----------

def test_a_fresh_request_gets_the_body_and_an_etag():
    resp = opps.handle_opportunities(_Req(), user=None)
    assert resp.status_code == 200
    assert resp.body == BODY
    assert resp.headers["ETag"] == ETAG
    assert "max-age" in resp.headers["Cache-Control"]


def test_the_cache_control_is_private():
    """The route 402s a lapsed account, so the response depends on who is asking — a shared
    proxy must never hand one student's 200 to a lapsed one."""
    resp = opps.handle_opportunities(_Req(), user=None)
    assert "private" in resp.headers["Cache-Control"]


def test_a_conditional_request_gets_a_304_with_no_body():
    resp = opps.handle_opportunities(_Req(if_none_match=ETAG), user=None)
    assert resp.status_code == 304
    assert resp.body == b""
    assert resp.headers["ETag"] == ETAG


def test_a_stale_conditional_request_gets_the_full_body():
    resp = opps.handle_opportunities(_Req(if_none_match='"old"'), user=None)
    assert resp.status_code == 200
    assert resp.body == BODY


def test_gzip_is_served_when_the_client_accepts_it():
    resp = opps.handle_opportunities(_Req(accept_encoding="gzip, deflate"), user=None)
    assert resp.headers["Content-Encoding"] == "gzip"
    assert gzip.decompress(resp.body) == BODY


def test_identity_is_served_when_gzip_is_not_accepted():
    resp = opps.handle_opportunities(_Req(accept_encoding="identity"), user=None)
    assert "Content-Encoding" not in resp.headers
    assert resp.body == BODY


def test_vary_accept_encoding_is_always_set():
    """Without it a cache can serve gzipped bytes to a client that cannot read them."""
    for req in (_Req(), _Req(accept_encoding="gzip"), _Req(if_none_match=ETAG)):
        assert opps.handle_opportunities(req, user=None).headers["Vary"] == "Accept-Encoding"


def test_the_304_is_decided_before_any_body_is_chosen(monkeypatch):
    """A 304 must cost neither the gzip nor the identity body."""
    resp = opps.handle_opportunities(_Req(if_none_match=ETAG, accept_encoding="gzip"), user=None)
    assert resp.status_code == 304
    assert resp.body == b""
    assert "Content-Encoding" not in resp.headers


def test_a_db_failure_is_still_an_opaque_502(monkeypatch):
    def boom():
        raise RuntimeError("supabase down")
    monkeypatch.setattr(opps, "catalog_payload", boom)
    resp = opps.handle_opportunities(_Req(), user=None)
    assert resp.status_code == 502
    assert b"supabase down" not in resp.body

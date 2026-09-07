"""Calendar handoff nonce (S1-3 / M3) and calendar sync input handling (S1-14 / L6)."""
import datetime
import inspect
import json
import time
import urllib.error

import pytest

import app.routes.google_oauth as gr
import app.services.google_oauth as g
import app.services.handoff_store as hs


# ================= S1-3: the handoff nonce =================

@pytest.fixture(autouse=True)
def _empty_handoff_store(monkeypatch):
    """A clean, in-process handoff store.

    Phase 4 moved these nonces onto app.services.handoff_store, which prefers the
    `auth_handoffs` table when Supabase creds are configured. A dev machine has a real
    SUPABASE_URL in .env and CI does not, so the backend is pinned here rather than left to
    whichever machine runs the suite — otherwise these tests would take the DB path locally
    (and be refused by conftest's socket guard) and the memory path in CI.
    """
    monkeypatch.setattr(hs, "SUPABASE_URL", "")
    hs._reset_for_tests()
    yield
    hs._reset_for_tests()


def test_a_nonce_resolves_to_its_userid_exactly_once():
    """Single-use: the URL reaches browser history and the Referer of whatever the OAuth
    flow touches, so it must not resolve twice."""
    nonce = g.mint_calendar_handoff("alice")
    assert g.take_calendar_handoff(nonce) == "alice"
    assert g.take_calendar_handoff(nonce) is None


def test_an_unknown_nonce_resolves_to_nothing():
    assert g.take_calendar_handoff("not-a-real-nonce") is None
    assert g.take_calendar_handoff("") is None


def test_a_nonce_expires():
    nonce = g.mint_calendar_handoff("alice")
    hs._memory[(g.KIND_CALENDAR_HANDOFF, hs._hash(nonce))]["expires_at"] = time.time() - 1
    assert g.take_calendar_handoff(nonce) is None


def test_the_ttl_is_a_minute_not_the_five_of_the_signin_token():
    """The only gap it has to survive is one POST followed by one navigation."""
    assert g.CALENDAR_HANDOFF_TTL_SECONDS == 60
    assert g.CALENDAR_HANDOFF_TTL_SECONDS < g.GOOGLE_TOKEN_TTL_SECONDS


def test_the_nonce_carries_no_credential():
    """The whole point: what lands in the access log stands for a userid, and is not a
    token that can be presented anywhere else."""
    nonce = g.mint_calendar_handoff("alice")
    entry = hs._memory[(g.KIND_CALENDAR_HANDOFF, hs._hash(nonce))]
    assert set(entry["payload"]) == {"userid"}


def test_nonces_are_unguessable():
    assert len({g.mint_calendar_handoff("alice") for _ in range(50)}) == 50
    assert len(g.mint_calendar_handoff("alice")) >= 32


def test_expired_nonces_are_pruned_rather_than_accumulating():
    stale = g.mint_calendar_handoff("alice")
    hs._memory[(g.KIND_CALENDAR_HANDOFF, hs._hash(stale))]["expires_at"] = time.time() - 1
    hs._last_prune = 0.0            # the sweep is throttled to once a minute per process
    g.mint_calendar_handoff("bob")
    assert (g.KIND_CALENDAR_HANDOFF, hs._hash(stale)) not in hs._memory


def test_the_nonce_itself_is_never_what_is_stored():
    """Phase 4: for the 60 seconds it lives, a nonce is a live credential. It is only ever
    compared for equality, so only its sha256 is kept — a database dump cannot hand anybody
    a working calendar-connect link."""
    nonce = g.mint_calendar_handoff("alice")
    assert (g.KIND_CALENDAR_HANDOFF, nonce) not in hs._memory
    assert (g.KIND_CALENDAR_HANDOFF, hs._hash(nonce)) in hs._memory


# ---------------- the two routes ----------------

class _User:
    id = "alice"


def test_the_handoff_route_takes_its_bearer_from_a_dependency_not_the_url():
    sig = inspect.signature(gr.handle_google_calendar_handoff)
    assert sig.parameters["user"].default.dependency is gr.require_subscription
    # No Request parameter at all, so there is nowhere for a query-string credential to be
    # read from.
    assert "request" not in sig.parameters


def test_the_handoff_route_answers_a_nonce():
    resp = gr.handle_google_calendar_handoff(user=_User())
    body = json.loads(resp.body)
    assert g.take_calendar_handoff(body["nonce"]) == "alice"
    assert body["expires_in"] == 60


class _Req:
    def __init__(self, params):
        self.query_params = params
        self.headers = {"Host": "localhost:8000"}
        self.url = type("U", (), {"scheme": "http"})()


def test_start_no_longer_accepts_an_access_token_in_the_url(monkeypatch):
    """Leaving `token=` as a fallback would leave the leak exactly where it was."""
    monkeypatch.setattr(gr, "_canonicalize_loopback", lambda _r: None)
    monkeypatch.setattr(gr, "user_exists", lambda _u: True)
    monkeypatch.setattr(gr, "GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setattr(gr, "GOOGLE_CLIENT_SECRET", "csec")
    resp = gr.handle_google_calendar_start(_Req({"token": "a.real.jwt"}))
    assert resp.status_code == 401


def test_start_source_does_not_verify_an_access_token_any_more():
    src = inspect.getsource(gr.handle_google_calendar_start)
    assert "verify_access_token" not in src
    assert "take_calendar_handoff" in src


def test_start_accepts_a_fresh_nonce(monkeypatch):
    monkeypatch.setattr(gr, "_canonicalize_loopback", lambda _r: None)
    monkeypatch.setattr(gr, "user_exists", lambda _u: True)
    monkeypatch.setattr(gr, "GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setattr(gr, "GOOGLE_CLIENT_SECRET", "csec")
    nonce = g.mint_calendar_handoff("alice")
    resp = gr.handle_google_calendar_start(_Req({"nonce": nonce}))
    assert resp.status_code in (302, 307)
    assert "accounts.google.com" in resp.headers["location"]


def test_a_replayed_nonce_is_refused(monkeypatch):
    monkeypatch.setattr(gr, "_canonicalize_loopback", lambda _r: None)
    monkeypatch.setattr(gr, "user_exists", lambda _u: True)
    monkeypatch.setattr(gr, "GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setattr(gr, "GOOGLE_CLIENT_SECRET", "csec")
    nonce = g.mint_calendar_handoff("alice")
    gr.handle_google_calendar_start(_Req({"nonce": nonce}))
    assert gr.handle_google_calendar_start(_Req({"nonce": nonce})).status_code == 401


# ================= S1-14: sync input handling =================

def _sync(events, monkeypatch, requests=None):
    """Drive handle_calendar_sync with Google stubbed out."""
    monkeypatch.setattr(gr.g, "get_google_calendar_access_token", lambda _u: "tok")
    monkeypatch.setattr(gr.g, "ensure_wingman_calendar", lambda *a: "cal@group")
    monkeypatch.setattr(gr, "select_user", lambda _u, _c: {"userid": "alice"})
    monkeypatch.setattr(gr, "_existing_event_map", lambda *a: ({}, []))
    monkeypatch.setattr(gr, "_sweep_stale_events", lambda *a, **k: (0, []))

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps({"id": "gid", "htmlLink": "https://cal"}).encode()

    def _open(req, timeout=None):
        if requests is not None:
            requests.append(req)
        return _Resp()

    monkeypatch.setattr(gr.urllib.request, "urlopen", _open)
    return gr.handle_calendar_sync(body={"events": events}, user=_User())


def test_a_revoked_token_returns_the_reconnect_marker(monkeypatch):
    """A present-but-revoked Google refresh token (invalid_grant) raises out of
    get_google_calendar_access_token, and the sync must answer 502 with the stable
    machine-readable code "calendar_reconnect" — that is what the client branches on to
    route the user back through Google's consent page instead of a dead-end error. The
    human `error` string is NOT the contract (it carries a volatile "(ref …)" suffix)."""
    def _raise(_u):
        raise urllib.error.HTTPError("https://oauth2.googleapis.com/token", 400,
                                     "Bad Request", {}, None)
    monkeypatch.setattr(gr.g, "get_google_calendar_access_token", _raise)
    resp = gr.handle_calendar_sync(body={"events": [{"id": "a", "title": "T",
                                                     "dateISO": "2026-11-01"}]}, user=_User())
    assert resp.status_code == 502
    assert json.loads(resp.body)["code"] == "calendar_reconnect"


def test_a_malformed_date_no_longer_kills_the_whole_sync(monkeypatch):
    """`year, month, day = date_iso.split("-")` raised out of the loop and 500'd
    everything. Tracker dates come from a model extraction, so malformed is a normal
    input here, not an attack."""
    resp = _sync([{"id": "a", "title": "Bad", "dateISO": "not-a-date"},
                  {"id": "b", "title": "Good", "dateISO": "2026-11-01"}], monkeypatch)
    assert resp.status_code == 200
    results = {r["id"]: r for r in json.loads(resp.body)["results"]}
    assert results["a"]["status"] == "error"
    assert results["b"]["status"] == "ok"


@pytest.mark.parametrize("bad", ["2026-13-45", "2026-11", "", "2026/11/01", "x-y-z",
                                 "2026-11-01T10:00:00Z"])
def test_every_shape_of_bad_date_is_reported_not_raised(monkeypatch, bad):
    resp = _sync([{"id": "a", "title": "T", "dateISO": bad}], monkeypatch)
    assert resp.status_code in (200, 400)


def test_the_start_date_sent_to_google_is_normalized(monkeypatch):
    """date.fromisoformat also accepts "20261101"; Google's all-day field wants
    YYYY-MM-DD, and start/end must not be in different formats."""
    reqs = []
    _sync([{"id": "a", "title": "T", "dateISO": "20261101"}], monkeypatch, reqs)
    body = json.loads(reqs[0].data)
    assert body["start"]["date"] == "2026-11-01"
    assert body["end"]["date"] == "2026-11-02"


def test_a_google_event_id_cannot_retarget_the_request(monkeypatch):
    """An unquoted `/` or `?` in the client's googleEventId re-pointed the PATCH at a
    different Google API path."""
    reqs = []
    _sync([{"id": "a", "title": "T", "dateISO": "2026-11-01",
            "googleEventId": "../../calendars/primary/events/victim?x=1"}],
          monkeypatch, reqs)
    url = reqs[0].full_url
    assert "?" not in url.split("/events/")[-1]
    assert "/events/" in url
    assert url.split("/events/")[-1].count("%2F") >= 2


def test_an_ordinary_event_id_still_works(monkeypatch):
    reqs = []
    _sync([{"id": "a", "title": "T", "dateISO": "2026-11-01", "googleEventId": "abc123"}],
          monkeypatch, reqs)
    assert reqs[0].full_url.endswith("/events/abc123")
    assert reqs[0].get_method() == "PATCH"

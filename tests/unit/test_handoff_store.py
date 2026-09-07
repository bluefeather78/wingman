"""The shared handoff store — PRODUCTION_READINESS_PLAN.md Phase 4, perf_report finding 11.

Four Google OAuth stores were module-level dicts: written by one request, read by a later and
separate one. On two uvicorn workers the second request lands on the other worker about half
the time, finds nothing, and tells the student their sign-in link expired — on a link that is
perfectly valid. app/services/handoff_store.py moves them into `auth_handoffs`.

What these pin, in the order they would hurt:

  * SINGLE-USE SURVIVES. S1-3 made the calendar handoff single-use so a URL in browser history
    or a Referer header is inert on the second click. A shared store that lost that property
    would be a security regression dressed as a scaling fix — which is exactly why this is a
    table and not the stateless HMAC token the perf report also offered.
  * TWO WORKERS SEE ONE STORE, and of two concurrent spenders exactly one wins.
  * THE NONCE IS NEVER STORED IN THE CLEAR.
  * A MISSING TABLE FALLS BACK rather than breaking sign-in, because that is the state of
    every checkout until somebody opens the Supabase SQL editor.
"""
import time
import urllib.error

import pytest

import app.services.handoff_store as hs


@pytest.fixture
def memory(monkeypatch):
    """The in-process backend, pinned.

    A dev machine has a real SUPABASE_URL in .env and CI does not, so leaving the backend to
    whichever machine runs the suite would mean these tests take the DB path locally (and get
    refused by conftest's socket guard) and the memory path in CI.
    """
    monkeypatch.setattr(hs, "SUPABASE_URL", "")
    hs._reset_for_tests()
    yield hs
    hs._reset_for_tests()


class _FakeDB:
    """A stand-in PostgREST holding (kind, token_hash) -> row, with DELETE-returning.

    Deliberately models the ONE property the real thing is relied on for: a DELETE returns
    the rows it actually removed, so two concurrent spenders cannot both get the payload.
    """

    def __init__(self):
        self.rows = {}
        self.calls = []

    def __call__(self, table, method="GET", params=None, data=None, extra_headers=None):
        self.calls.append((table, method, params, data))
        params = params or {}
        if method == "POST":
            key = (data["kind"], data["token_hash"])
            if key in self.rows:
                raise _http_error(409, {"code": "23505"})
            self.rows[key] = dict(data)
            return []
        if method == "DELETE":
            if "expires_at" in params:                       # the prune sweep
                cutoff = params["expires_at"].split("lt.", 1)[-1]
                for k in [k for k, r in self.rows.items() if r["expires_at"] < cutoff]:
                    del self.rows[k]
                return []
            key = (params["kind"].split("eq.", 1)[-1],
                   params["token_hash"].split("eq.", 1)[-1])
            row = self.rows.pop(key, None)
            return [row] if row else []
        if method == "GET":                                  # peek: read without removing
            key = (params["kind"].split("eq.", 1)[-1],
                   params["token_hash"].split("eq.", 1)[-1])
            row = self.rows.get(key)
            return [row] if row else []
        return []


def _http_error(code, body):
    import io
    import json
    return urllib.error.HTTPError(
        "https://x", code, "err", {}, io.BytesIO(json.dumps(body).encode()))


@pytest.fixture
def db(monkeypatch):
    fake = _FakeDB()
    monkeypatch.setattr(hs, "SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr(hs, "SUPABASE_SERVICE_KEY", "service-key")
    monkeypatch.setattr(hs, "_supabase_request_strict", fake)
    hs._reset_for_tests()
    yield fake
    hs._reset_for_tests()


# ---------- single-use, on both backends ----------

@pytest.mark.parametrize("backend", ["memory", "db"])
def test_a_payload_resolves_exactly_once(backend, request):
    request.getfixturevalue(backend)
    assert hs.put("k", "tok", {"userid": "alice"}, 60) is True
    assert hs.take("k", "tok") == {"userid": "alice"}
    assert hs.take("k", "tok") is None


@pytest.mark.parametrize("backend", ["memory", "db"])
def test_an_unknown_or_empty_token_resolves_to_nothing(backend, request):
    request.getfixturevalue(backend)
    assert hs.take("k", "never-minted") is None
    assert hs.take("k", "") is None
    assert hs.take("k", None) is None


@pytest.mark.parametrize("backend", ["memory", "db"])
def test_kinds_do_not_collide(backend, request):
    """The calendar `state` and the sign-in `state` are both OAuth states and could be the
    same string. Spending one must not spend the other."""
    request.getfixturevalue(backend)
    hs.put("calendar_state", "s", {"userid": "alice"}, 60)
    hs.put("login_redirect", "s", {"app_redirect": "wingman://"}, 60)
    assert hs.take("calendar_state", "s") == {"userid": "alice"}
    assert hs.take("login_redirect", "s") == {"app_redirect": "wingman://"}


# ---------- peek: read without consuming (the Google new-signup resolve step) ----------

@pytest.mark.parametrize("backend", ["memory", "db"])
def test_peek_reads_without_consuming(backend, request):
    """A brand-new Google signup mints ONE token that both /session (peek) and /finish (take)
    must read. If peek consumed it, finish would find nothing and strand every new account at
    'this sign-in link has expired' on a token minted seconds earlier."""
    request.getfixturevalue(backend)
    hs.put("google_session", "tok", {"kind": "pending", "email": "a@b.com"}, 60)
    assert hs.peek("google_session", "tok") == {"kind": "pending", "email": "a@b.com"}
    assert hs.peek("google_session", "tok") == {"kind": "pending", "email": "a@b.com"}
    # ...and it is still spendable exactly once afterwards.
    assert hs.take("google_session", "tok") == {"kind": "pending", "email": "a@b.com"}
    assert hs.take("google_session", "tok") is None


@pytest.mark.parametrize("backend", ["memory", "db"])
def test_peek_refuses_unknown_empty_and_expired(backend, request):
    request.getfixturevalue(backend)
    assert hs.peek("google_session", "never-minted") is None
    assert hs.peek("google_session", "") is None
    assert hs.peek("google_session", None) is None
    hs.put("google_session", "tok", {"kind": "pending"}, 60)
    if backend == "memory":
        hs._memory[("google_session", hs._hash("tok"))]["expires_at"] = time.time() - 1
    else:
        list(request.getfixturevalue("db").rows.values())[0]["expires_at"] = \
            "2020-01-01T00:00:00+00:00"
    assert hs.peek("google_session", "tok") is None


def test_peek_does_not_mutate_the_stored_payload(memory):
    """peek returns a copy, so a caller mutating the dict cannot corrupt the stored nonce."""
    hs.put("google_session", "tok", {"kind": "pending", "email": "a@b.com"}, 60)
    got = hs.peek("google_session", "tok")
    got["email"] = "tampered"
    assert hs.peek("google_session", "tok")["email"] == "a@b.com"


def test_two_concurrent_spenders_and_only_one_wins(db):
    """The reason this is a DELETE-returning and not a read-then-delete: with two workers a
    read-then-delete leaves a window in which both see the nonce."""
    hs.put("k", "tok", {"userid": "alice"}, 60)
    first = hs.take("k", "tok")
    second = hs.take("k", "tok")
    assert [bool(first), bool(second)] == [True, False]


def test_the_nonce_is_stored_hashed_never_in_the_clear(db):
    hs.put("k", "the-real-nonce", {"userid": "alice"}, 60)
    stored = list(db.rows.values())[0]
    assert stored["token_hash"] == hs._hash("the-real-nonce")
    assert "the-real-nonce" not in str(stored)


def test_the_memory_backend_hashes_too(memory):
    hs.put("k", "the-real-nonce", {"userid": "alice"}, 60)
    assert ("k", "the-real-nonce") not in hs._memory
    assert ("k", hs._hash("the-real-nonce")) in hs._memory


# ---------- expiry ----------

def test_an_expired_payload_is_refused_on_the_memory_backend(memory):
    hs.put("k", "tok", {"userid": "alice"}, 60)
    hs._memory[("k", hs._hash("tok"))]["expires_at"] = time.time() - 1
    assert hs.take("k", "tok") is None


def test_an_expired_row_is_refused_even_before_the_sweep_reaches_it(db):
    """Expiry is enforced on READ. Leaving it to the prune sweep would quietly extend every
    nonce's real lifetime to 'until somebody tidies up'."""
    hs.put("k", "tok", {"userid": "alice"}, 60)
    row = list(db.rows.values())[0]
    row["expires_at"] = "2020-01-01T00:00:00+00:00"
    assert hs.take("k", "tok") is None


def test_the_sweep_is_throttled_rather_than_running_on_every_mint(db):
    hs.put("k", "a", {}, 60)
    hs.put("k", "b", {}, 60)
    sweeps = [c for c in db.calls if c[1] == "DELETE" and "expires_at" in (c[2] or {})]
    assert len(sweeps) == 1, "minting a nonce should not cost a sweep every time"


def test_the_sweep_removes_expired_rows(db):
    hs.put("k", "stale", {}, 60)
    list(db.rows.values())[0]["expires_at"] = "2020-01-01T00:00:00+00:00"
    hs._last_prune = 0.0
    hs.put("k", "fresh", {}, 60)
    assert ("k", hs._hash("stale")) not in db.rows
    assert ("k", hs._hash("fresh")) in db.rows


# ---------- the un-migrated database ----------

def test_a_missing_table_falls_back_to_memory_and_still_signs_people_in(monkeypatch, capsys):
    """The state of every checkout until somebody runs the .sql file. Phase 3's lesson was
    that 'the migration has not been run yet' is the untested case, so it is tested here."""
    def missing(*a, **k):
        raise _http_error(404, {"code": "PGRST205"})
    monkeypatch.setattr(hs, "SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr(hs, "SUPABASE_SERVICE_KEY", "service-key")
    monkeypatch.setattr(hs, "_supabase_request_strict", missing)
    hs._reset_for_tests()
    try:
        assert hs.put("k", "tok", {"userid": "alice"}, 60) is True
        assert hs.take("k", "tok") == {"userid": "alice"}
        assert "db/auth_handoffs_schema.sql" in capsys.readouterr().out
    finally:
        hs._reset_for_tests()


def test_the_fallback_warning_is_printed_once_not_per_signin(monkeypatch, capsys):
    def missing(*a, **k):
        raise _http_error(404, {"code": "PGRST205"})
    monkeypatch.setattr(hs, "SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr(hs, "SUPABASE_SERVICE_KEY", "service-key")
    monkeypatch.setattr(hs, "_supabase_request_strict", missing)
    hs._reset_for_tests()
    try:
        for _ in range(5):
            hs.put("k", f"tok{_}", {}, 60)
        assert capsys.readouterr().out.count("auth_handoffs unavailable") == 1
    finally:
        hs._reset_for_tests()


def test_a_transient_failure_reports_false_rather_than_minting_a_dead_nonce(monkeypatch):
    """A nonce the store never kept is a sign-in that dead-ends one redirect later. The
    routes turn False into an honest 503; silently returning a token would not."""
    def boom(*a, **k):
        raise RuntimeError("supabase unreachable")
    monkeypatch.setattr(hs, "SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr(hs, "SUPABASE_SERVICE_KEY", "service-key")
    monkeypatch.setattr(hs, "_supabase_request_strict", boom)
    hs._reset_for_tests()
    try:
        assert hs.put("k", "tok", {}, 60) is False
        assert hs._db_available is True, "a transient blip must not latch off the DB backend"
    finally:
        hs._reset_for_tests()


def test_a_collision_refuses_rather_than_overwriting(db):
    """secrets.token_urlsafe(32) colliding is not a thing that happens; if it somehow did,
    overwriting would let one flow steal another's nonce."""
    hs.put("k", "tok", {"userid": "alice"}, 60)
    assert hs.put("k", "tok", {"userid": "mallory"}, 60) is False
    assert hs.take("k", "tok") == {"userid": "alice"}


def test_no_supabase_credentials_means_memory_without_a_missing_table_warning(monkeypatch,
                                                                             capsys):
    """Offline dev (CLAUDE.md's standing constraint) is not a broken migration."""
    monkeypatch.setattr(hs, "SUPABASE_URL", "")
    monkeypatch.setattr(hs, "SUPABASE_SERVICE_KEY", "")
    hs._reset_for_tests()
    try:
        assert hs.put("k", "tok", {"userid": "alice"}, 60) is True
        assert hs.take("k", "tok") == {"userid": "alice"}
        assert "auth_handoffs unavailable" not in capsys.readouterr().out
    finally:
        hs._reset_for_tests()


# ---------- the actual finding: a second worker ----------

def test_a_second_worker_can_spend_what_the_first_minted(db):
    """The finding, restated as a test. Worker A mints; worker B — a different process, so
    none of A's in-process state — spends it. On the old dicts B found nothing and told the
    student their sign-in link had expired."""
    hs.put("google_session", "tok", {"kind": "login", "userid": "alice"}, 300)
    hs._memory.clear()                       # worker B shares the database, not the memory
    assert hs.take("google_session", "tok") == {"kind": "login", "userid": "alice"}


def test_no_oauth_store_is_a_module_level_dict_any_more():
    """A regression guard with teeth: the failure mode here is not a crash, it is somebody
    adding a fifth store as a plain dict because that is what the file used to look like.
    Such a store works perfectly on one worker and breaks sign-in on two."""
    import inspect
    import re
    from app.services import google_oauth
    src = inspect.getsource(google_oauth)
    offenders = re.findall(r"^(_?\w*(?:token|handoff|state|redirect)\w*)\s*=\s*\{\}\s*$",
                           src, re.I | re.M)
    assert not offenders, (
        f"these are process-local again, which is perf_report finding 11: {offenders}. "
        f"Put them on app.services.handoff_store instead.")


def test_every_google_oauth_store_goes_through_the_shared_store():
    """Guards the guard above: if the four stores were deleted rather than migrated, the
    regex test would pass on nothing."""
    import inspect
    from app.services import google_oauth
    src = inspect.getsource(google_oauth)
    for kind in ("KIND_SESSION", "KIND_CALENDAR_HANDOFF", "KIND_CALENDAR_STATE",
                 "KIND_LOGIN_REDIRECT",
                 # Delete-account re-auth for Google-only accounts (DATA_DELETION_EXPORT_PLAN.md).
                 "KIND_DELETE_REAUTH_HANDOFF", "KIND_DELETE_REAUTH_STATE",
                 "KIND_DELETE_REAUTH_PROOF"):
        assert kind in src, f"{kind} is gone — was a store dropped rather than migrated?"
    # 4 sign-in/calendar stores + 3 delete-reauth stores (handoff, state, proof).
    assert src.count("handoff_store.put") == 7
    assert src.count("handoff_store.take") == 7

"""Refresh-token rotation with reuse detection — S1-2, finding M2.

Before this: a 30-day refresh token, never rotated. /api/auth/refresh checked `ver`,
returned a NEW pair, and left the presented token valid until its own exp. The client's
logout() only called forgetSession() locally — there was no server call at all. So a token
copied off a shared school computer, out of a proxy log, or from a compromised device kept
minting access tokens FOR 30 DAYS, INCLUDING AFTER THE STUDENT PRESSED "LOG OUT", and
nothing signalled that two parties were refreshing the same lineage.
"""
import json
import time
import urllib.error
import urllib.parse

import pytest

import app.core as core
import app.deps as deps
import app.routes.auth as auth
from app.auth.tokens import issue_tokens


# ================= core: the jti store =================

class _Store:
    """A stand-in for users.refresh_jtis, exercised through _users_request."""
    def __init__(self, jtis=None, has_column=True):
        self.jtis = list(jtis or [])
        self.has_column = has_column
        self.writes = []

    def __call__(self, method, query="", data=None, prefer=None):
        params = dict(urllib.parse.parse_qsl(query.lstrip("?")))
        if method == "GET":
            if not self.has_column and "refresh_jtis" in params.get("select", ""):
                raise urllib.error.HTTPError("u", 400, "Bad Request", {}, None)
            row = {"userid": "alice"}
            if self.has_column:
                row["refresh_jtis"] = list(self.jtis)
            return [row]
        self.writes.append(data)
        self.jtis = list(data.get("refresh_jtis", self.jtis))
        return [{"userid": "alice"}]


@pytest.fixture(autouse=True)
def _clean_grace(monkeypatch):
    monkeypatch.setattr(core, "_refresh_grace", {})


def test_rotation_replaces_the_presented_lineage(monkeypatch):
    store = _Store(["old-jti"])
    monkeypatch.setattr(core, "_users_request", store)
    assert core.rotate_refresh_jti("alice", "new-jti", replaces="old-jti") is True
    assert store.jtis == ["new-jti"]


def test_a_second_device_gets_its_own_lineage(monkeypatch):
    """A single stored value would make a laptop and a phone log each other out on every
    refresh."""
    store = _Store(["laptop"])
    monkeypatch.setattr(core, "_users_request", store)
    core.rotate_refresh_jti("alice", "phone")
    assert set(store.jtis) == {"laptop", "phone"}


def test_the_lineage_list_is_capped_oldest_evicted(monkeypatch):
    store = _Store([f"d{i}" for i in range(core.REFRESH_JTI_MAX)])
    monkeypatch.setattr(core, "_users_request", store)
    core.rotate_refresh_jti("alice", "newest")
    assert len(store.jtis) == core.REFRESH_JTI_MAX
    assert store.jtis[0] == "newest"


def test_the_write_is_conditional_on_what_was_read(monkeypatch):
    """Two devices refreshing in the same instant must not clobber each other."""
    seen = []

    def _req(method, query="", data=None, prefer=None):
        if method == "GET":
            return [{"userid": "alice", "refresh_jtis": ["a", "b"]}]
        seen.append(query)
        return [{"userid": "alice"}]

    monkeypatch.setattr(core, "_users_request", _req)
    core.rotate_refresh_jti("alice", "c", replaces="a")
    params = urllib.parse.parse_qsl(seen[0].lstrip("?"))
    assert ("refresh_jtis", "eq.{a,b}") in params


def test_losing_the_race_twice_still_records_the_new_lineage(monkeypatch):
    """Leaving this device holding a jti the server does not know would make its NEXT
    refresh look like a stolen token — the opposite of the intent."""
    writes = []

    def _req(method, query="", data=None, prefer=None):
        if method == "GET":
            return [{"userid": "alice", "refresh_jtis": ["a"]}]
        writes.append((query, data))
        return [] if "refresh_jtis=eq" in query or "or=" in query else [{"ok": 1}]

    monkeypatch.setattr(core, "_users_request", _req)
    assert core.rotate_refresh_jti("alice", "c", replaces="a") is True
    assert writes[-1][1] == {"refresh_jtis": ["c"]}


def test_a_missing_column_reports_false_rather_than_raising(monkeypatch):
    """That False is the whole degrade story: rotation is simply off until the migration
    runs, instead of every refresh token being refused."""
    monkeypatch.setattr(core, "_users_request", _Store(has_column=False))
    assert core.rotate_refresh_jti("alice", "new") is False


def test_forget_drops_only_that_device(monkeypatch):
    store = _Store(["laptop", "phone"])
    monkeypatch.setattr(core, "_users_request", store)
    assert core.forget_refresh_jti("alice", "laptop") is True
    assert store.jtis == ["phone"]


def test_forget_reports_false_before_the_migration(monkeypatch):
    monkeypatch.setattr(core, "_users_request", _Store(has_column=False))
    assert core.forget_refresh_jti("alice", "x") is False


# ---------------- the grace window ----------------

def test_a_just_rotated_jti_maps_to_its_successor(monkeypatch):
    store = _Store(["old"])
    monkeypatch.setattr(core, "_users_request", store)
    core.rotate_refresh_jti("alice", "new", replaces="old")
    assert core.refresh_grace_successor("old") == "new"


def test_the_grace_expires(monkeypatch):
    store = _Store(["old"])
    monkeypatch.setattr(core, "_users_request", store)
    core.rotate_refresh_jti("alice", "new", replaces="old")
    core._refresh_grace["old"] = ("new", time.time() - 1)
    assert core.refresh_grace_successor("old") is None


def test_an_unknown_jti_has_no_grace():
    assert core.refresh_grace_successor("never-seen") is None
    assert core.refresh_grace_successor(None) is None


# ================= the refresh route =================

@pytest.fixture
def _account(monkeypatch):
    state = {"jtis": ["live-jti"], "bumped": [], "forgot": []}
    monkeypatch.setattr(auth, "get_user_account",
                        lambda _u: {"userid": "alice", "token_version": 0})
    monkeypatch.setattr(auth, "_live_jtis", lambda _u: state["jtis"])
    monkeypatch.setattr(auth, "bump_token_version",
                        lambda u: state["bumped"].append(u))
    monkeypatch.setattr(auth, "forget_refresh_jti",
                        lambda u, j: state["forgot"].append(j) or True)
    monkeypatch.setattr(auth, "login_response",
                        lambda rec, replaces_jti=None: state.__setitem__(
                            "replaced", replaces_jti) or {"ok": True})
    return state


def _refresh_with(jti, ver=0):
    pair = issue_tokens("alice", token_version=ver, refresh_jti=jti)
    return auth.handle_refresh(body={"refresh_token": pair["refresh_token"]})


def test_a_live_token_refreshes_and_is_rotated_away(_account):
    resp = _refresh_with("live-jti")
    assert resp.status_code == 200
    assert _account["replaced"] == "live-jti"


def test_a_superseded_token_revokes_the_whole_account(_account):
    """Two parties hold the same lineage and there is no way to tell which is asking. One
    forced sign-in beats a live intruder."""
    resp = _refresh_with("stolen-and-already-used")
    assert resp.status_code == 401
    assert _account["bumped"] == ["alice"]


def test_a_superseded_token_within_the_grace_window_is_not_theft(monkeypatch, _account):
    """The realistic false positive is a dropped response on a school wifi, not a thief —
    the client is retrying the very same call. Signing a student out of every device for
    having bad reception is not a security win."""
    monkeypatch.setattr(auth, "refresh_grace_successor",
                        lambda j: "successor" if j == "retried" else None)
    resp = _refresh_with("retried")
    assert resp.status_code == 200
    assert _account["bumped"] == []
    assert _account["replaced"] == "successor"


def _legacy_refresh_token(userid="alice", ver=0):
    """A refresh token in the pre-S1-2 shape: no `jti` claim at all."""
    import datetime
    import jwt
    from app.config import JWT_SECRET, JWT_ALGORITHM, REFRESH_TOKEN_TTL_SECONDS
    now = datetime.datetime.now(datetime.timezone.utc)
    return jwt.encode({"sub": userid, "type": "refresh", "ver": ver, "iat": now,
                       "exp": now + datetime.timedelta(seconds=REFRESH_TOKEN_TTL_SECONDS)},
                      JWT_SECRET, algorithm=JWT_ALGORITHM)


def test_a_legacy_token_with_no_jti_is_adopted_not_accused(_account):
    """Every token minted before this shipped has no jti. Treating those as reuse would
    sign out every logged-in student the moment it deploys."""
    resp = auth.handle_refresh(body={"refresh_token": _legacy_refresh_token()})
    assert resp.status_code == 200
    assert _account["bumped"] == []
    assert _account["replaced"] is None


def test_an_account_with_no_recorded_lineage_is_adopted(monkeypatch, _account):
    """It has simply not refreshed since the migration ran."""
    monkeypatch.setattr(auth, "_live_jtis", lambda _u: [])
    resp = _refresh_with("some-jti")
    assert resp.status_code == 200
    assert _account["bumped"] == []


def test_rotation_is_off_when_the_column_is_missing(monkeypatch, _account):
    monkeypatch.setattr(auth, "_live_jtis", lambda _u: None)
    resp = _refresh_with("anything-at-all")
    assert resp.status_code == 200
    assert _account["bumped"] == []
    assert _account["replaced"] is None


def test_the_token_version_check_still_comes_first(monkeypatch, _account):
    monkeypatch.setattr(auth, "get_user_account",
                        lambda _u: {"userid": "alice", "token_version": 9})
    resp = _refresh_with("live-jti", ver=0)
    assert resp.status_code == 401
    assert _account["bumped"] == []       # already revoked; nothing more to do


# ================= logout =================

def test_logout_drops_this_device_server_side(monkeypatch):
    forgot = []
    monkeypatch.setattr(auth, "forget_refresh_jti",
                        lambda u, j: forgot.append((u, j)) or True)
    pair = issue_tokens("alice", refresh_jti="this-device")
    resp = auth.handle_logout(body={"refresh_token": pair["refresh_token"]})
    assert json.loads(resp.body) == {"ok": True, "revoked": True}
    assert forgot == [("alice", "this-device")]


def test_logout_of_an_unreadable_token_is_a_quiet_200(monkeypatch):
    """It is already unusable, and this must not become a way to probe which tokens are
    valid."""
    monkeypatch.setattr(auth, "forget_refresh_jti",
                        lambda u, j: pytest.fail("nothing to revoke"))
    resp = auth.handle_logout(body={"refresh_token": "not-a-jwt"})
    assert resp.status_code == 200
    assert json.loads(resp.body)["revoked"] is False


def test_logout_reports_honestly_when_the_column_is_missing(monkeypatch):
    monkeypatch.setattr(auth, "forget_refresh_jti", lambda u, j: False)
    pair = issue_tokens("alice", refresh_jti="x")
    resp = auth.handle_logout(body={"refresh_token": pair["refresh_token"]})
    assert json.loads(resp.body)["revoked"] is False


def test_a_broken_store_never_fails_the_logout(monkeypatch):
    """The local session is dropped either way; a logout that errors is a logout the
    student will assume did not happen."""
    def _boom(u, j):
        raise RuntimeError("supabase down")
    monkeypatch.setattr(auth, "forget_refresh_jti", _boom)
    pair = issue_tokens("alice", refresh_jti="x")
    assert auth.handle_logout(body={"refresh_token": pair["refresh_token"]}).status_code == 200


# ================= the jti never reaches the client =================

def test_the_jti_is_not_in_the_login_payload(monkeypatch):
    """A jti on the wire would hand a thief the exact string to look for."""
    monkeypatch.setattr(deps, "rotate_refresh_jti", lambda *a, **k: True)
    payload = deps.login_response({"userid": "alice", "first_name": "A", "last_name": "B",
                                   "email": "a@b.c", "token_version": 0})
    assert "refresh_jti" not in payload
    assert payload["refresh_token"]


def test_a_failed_rotation_never_fails_the_sign_in(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(deps, "rotate_refresh_jti", _boom)
    payload = deps.login_response({"userid": "alice", "first_name": "A", "last_name": "B",
                                   "email": "a@b.c", "token_version": 0})
    assert payload["token"]

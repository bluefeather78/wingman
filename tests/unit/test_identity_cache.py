"""Unit tests for the identity cache — Phase 2 item 3.

Every signed-in request runs the subscription gate, and the gate read a users row from
Supabase to do it: ~150ms measured, on every click by every signed-in student, to answer a
question whose answer changes about twice in an account's lifetime.

What these pin, in order of how badly each would hurt:
  * a users WRITE busts the cache. The accepted staleness is one-directional — a lapse may
    enforce up to a minute late — but a student who has just PAID must never wait, and the
    same goes for a promo redemption or a fresh signup.
  * an INSERT busts it too. Registration calls user_exists() first, which caches a None; if
    the insert did not clear that, the account would be created and then treated as
    nonexistent for the rest of the TTL — a student locked out of the app they just joined.
  * what lands in the cache is the five narrow columns, never a `SELECT *` with the 37KB
    `data` blob, even on the degraded read path where select_user falls back.
"""
import pytest

import app.core as core
import app.deps as deps


@pytest.fixture(autouse=True)
def _clean():
    core.clear_identity_cache()
    yield
    core.clear_identity_cache()


def _stub_select(monkeypatch, rows, calls):
    def fake(userid, columns):
        calls.append((userid, columns))
        return rows.get(userid)
    monkeypatch.setattr(core, "select_user", fake)


# ---------- the cache itself ----------

def test_a_second_read_inside_the_ttl_does_not_hit_supabase(monkeypatch):
    calls = []
    _stub_select(monkeypatch, {"alice": {"userid": "alice", "subscription_status": "active"}}, calls)
    first = core.get_user_subscription("alice")
    second = core.get_user_subscription("alice")
    assert first == second
    assert len(calls) == 1, "the second read went to the wire"


def test_it_reads_only_the_narrow_column_set(monkeypatch):
    calls = []
    _stub_select(monkeypatch, {"alice": {"userid": "alice"}}, calls)
    core.get_user_subscription("alice")
    assert calls[0][1] == core._SUBSCRIPTION_COLUMNS
    assert "password_hash" not in calls[0][1]
    assert "data" not in calls[0][1].split(",")


def test_a_degraded_wide_read_is_projected_before_it_is_cached(monkeypatch):
    """select_user falls back to SELECT * when a column has not been migrated in. Caching
    that would put every student's `data` blob in memory for a minute."""
    calls = []
    _stub_select(monkeypatch, {"alice": {
        "userid": "alice", "subscription_status": "active", "password_hash": "secret",
        "data": {"tracker": "x" * 1000}}}, calls)
    record = core.get_user_subscription("alice")
    assert "data" not in record
    assert "password_hash" not in record
    assert record["subscription_status"] == "active"


def test_a_missing_account_is_cached_too(monkeypatch):
    """A signed token for a deleted account would otherwise re-ask Supabase forever."""
    calls = []
    _stub_select(monkeypatch, {}, calls)
    assert core.get_user_subscription("ghost") is None
    assert core.get_user_subscription("ghost") is None
    assert len(calls) == 1


def test_a_zero_ttl_disables_the_cache(monkeypatch):
    calls = []
    _stub_select(monkeypatch, {"alice": {"userid": "alice"}}, calls)
    core.get_user_subscription("alice", ttl=0)
    core.get_user_subscription("alice", ttl=0)
    assert len(calls) == 2


def test_the_userid_is_normalised(monkeypatch):
    calls = []
    _stub_select(monkeypatch, {"alice": {"userid": "alice"}}, calls)
    core.get_user_subscription("Alice")
    core.get_user_subscription("  alice ")
    assert len(calls) == 1


# ---------- invalidation at the choke point ----------

def test_a_patch_busts_the_cache_for_that_user(monkeypatch):
    calls = []
    _stub_select(monkeypatch, {"alice": {"userid": "alice", "subscription_status": "trial"}}, calls)
    core.get_user_subscription("alice")
    core._invalidate_identity_for_write("?userid=eq.alice", {"subscription_status": "active"})
    core.get_user_subscription("alice")
    assert len(calls) == 2, "an upgrade would have taken up to a minute to be seen"


def test_a_patch_leaves_other_users_cached(monkeypatch):
    calls = []
    _stub_select(monkeypatch, {"alice": {"userid": "alice"}, "bob": {"userid": "bob"}}, calls)
    core.get_user_subscription("alice")
    core.get_user_subscription("bob")
    core._invalidate_identity_for_write("?userid=eq.alice", {"x": 1})
    core.get_user_subscription("bob")
    assert len(calls) == 2


def test_an_insert_busts_the_negative_entry_from_signup(monkeypatch):
    """user_exists() caches a None during registration; the insert must clear it."""
    rows = {}
    calls = []
    _stub_select(monkeypatch, rows, calls)
    assert core.get_user_subscription("newbie") is None
    core._invalidate_identity_for_write("", [{"userid": "newbie", "email": "n@x.com"}])
    rows["newbie"] = {"userid": "newbie", "subscription_status": "trial"}
    assert core.get_user_subscription("newbie") is not None


def test_an_unparseable_write_clears_everything_rather_than_guessing(monkeypatch):
    calls = []
    _stub_select(monkeypatch, {"alice": {"userid": "alice"}}, calls)
    core.get_user_subscription("alice")
    core._invalidate_identity_for_write("?email=eq.a@b.com", None)
    core.get_user_subscription("alice")
    assert len(calls) == 2, "a write it could not attribute must not leave stale rows"


def test_every_users_write_goes_through_the_invalidating_choke_point():
    """The reason the bust can be trusted: a new write path cannot reach the table without
    passing the invalidation."""
    import inspect
    src = inspect.getsource(core._users_request)
    assert 'if method != "GET":' in src
    assert "_invalidate_identity_for_write(query, data)" in src


# ---------- the gate uses it ----------

def test_the_subscription_gate_reads_no_account_at_all():
    # Two-tier model: there is no app-access lockout, so subscription_block_reason short-
    # circuits to None and reads NEITHER the wide account nor the narrow subscription. The
    # important invariant that survives is that it must never fall back onto the wide read.
    import inspect
    src = inspect.getsource(deps.subscription_block_reason)
    code = "\n".join(l.split("#")[0] for l in src.splitlines())
    assert "get_user_account(" not in code, "the gate is back on the wide read"
    assert "get_user_subscription(" not in code, "the gate no longer reads the subscription"

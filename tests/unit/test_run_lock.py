"""The catalog-insert run lock — audit finding 4.2, and Phase 3's headline exit test:
"two agents at once refuse to overlap".

Both backends are exercised. The file backend runs for real against tmp_path; the DB backend
runs against a fake PostgREST that enforces the primary key, because that PK is the entire
reason the DB path is atomic — a fake that let both inserts through would test nothing.
"""
import datetime
import io
import json
import urllib.error

import pytest

from wingman import run_lock as rl


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------

def _http_error(code, status=400):
    body = json.dumps({"code": code, "message": code}).encode("utf-8")
    return urllib.error.HTTPError("http://x", status, code, {}, io.BytesIO(body))


@pytest.fixture(autouse=True)
def _lock_files_in_tmp(tmp_path, monkeypatch):
    """Never touch the real repo root — a stray .agent_catalog_insert.lock would wedge a
    developer's next scrape run for the length of a lease."""
    monkeypatch.setattr(rl, "REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(rl, "_file_lock_path",
                        lambda name: str(tmp_path / f".agent_{name}.lock"))


class FakeDB:
    """A PostgREST stand-in for agent_locks that actually enforces the primary key."""

    def __init__(self, missing_table=False):
        self.rows = {}
        self.missing_table = missing_table

    def get(self, url, table, params, key, **kw):
        if self.missing_table:
            raise _http_error("42P01")
        name = (params.get("name") or "").replace("eq.", "")
        row = self.rows.get(name)
        return [row] if row else []

    def post(self, url, table, rows, key, **kw):
        if self.missing_table:
            raise _http_error("42P01")
        for r in rows:
            if r["name"] in self.rows:
                raise _http_error("23505", status=409)   # the PK doing its job
            self.rows[r["name"]] = dict(r)

    def patch(self, url, table, params, body, key):
        name = (params.get("name") or "").replace("eq.", "")
        token = (params.get("token") or "").replace("eq.", "")
        row = self.rows.get(name)
        if row and (not token or row.get("token") == token):
            row.update(body)

    def delete(self, url, table, params, key):
        name = (params.get("name") or "").replace("eq.", "")
        row = self.rows.get(name)
        if not row:
            return
        if "token" in params and row.get("token") != params["token"].replace("eq.", ""):
            return
        if "expires_at" in params:      # guarded takeover: only delete if really expired
            cutoff = params["expires_at"].replace("lt.", "")
            if not row.get("expires_at", "") < cutoff:
                return
        self.rows.pop(name, None)


@pytest.fixture
def db(monkeypatch):
    fake = FakeDB()
    monkeypatch.setattr(rl, "supabase_get", fake.get)
    monkeypatch.setattr(rl, "supabase_post", fake.post)
    monkeypatch.setattr(rl, "supabase_patch", fake.patch)
    monkeypatch.setattr(rl, "supabase_delete", fake.delete)
    return fake


def _past():
    return (rl._now() - datetime.timedelta(seconds=60)).isoformat()


# --------------------------------------------------------------------------------------
# THE exit test
# --------------------------------------------------------------------------------------

def test_two_agents_at_once_refuse_to_overlap_db(db):
    """Phase 3 exit test, DB backend: the second acquirer is refused and told who holds it."""
    first = rl.RunLock(rl.CATALOG_INSERT, "scraper", "http://db", "svc").acquire()
    assert first.backend == "db"

    second = rl.RunLock(rl.CATALOG_INSERT, "hub_miner", "http://db", "svc")
    with pytest.raises(rl.LockBusy) as e:
        second.acquire()
    assert "scraper" in str(e.value)

    first.release()
    third = rl.RunLock(rl.CATALOG_INSERT, "hub_miner", "http://db", "svc").acquire()
    assert third.backend == "db"     # released cleanly, the next run gets it
    third.release()


def test_two_agents_at_once_refuse_to_overlap_file():
    """Same test with no database at all — the fallback must still exclude."""
    first = rl.RunLock(rl.CATALOG_INSERT, "scraper").acquire()
    assert first.backend == "file"
    with pytest.raises(rl.LockBusy):
        rl.RunLock(rl.CATALOG_INSERT, "harvest_names").acquire()
    first.release()
    rl.RunLock(rl.CATALOG_INSERT, "harvest_names").acquire().release()


def test_guard_refuses_with_exit_2_and_never_runs_the_agent(db):
    """What an operator actually sees: a one-line reason, not a traceback, and main() not run."""
    holder = rl.RunLock(rl.CATALOG_INSERT, "scraper", "http://db", "svc").acquire()
    ran = []
    with pytest.raises(SystemExit) as e:
        rl.guard_catalog_writes("hub_miner", lambda: ran.append(1), argv=[])
    assert e.value.code == 2
    assert ran == []
    holder.release()


def test_guard_runs_and_releases_on_success(db):
    ran = []
    rl.guard_catalog_writes("scraper", lambda: ran.append(1), argv=[])
    assert ran == [1]
    assert rl.current_holder("http://db", "svc") is None   # released


def test_guard_releases_the_lock_when_the_agent_raises(db):
    """A crashed run must not wedge the lock for the whole lease."""
    with pytest.raises(ValueError):
        rl.guard_catalog_writes("scraper", lambda: (_ for _ in ()).throw(ValueError("boom")),
                                argv=[])
    assert rl.current_holder("http://db", "svc") is None


def test_preview_never_takes_the_lock(db):
    """--preview is free and read-only; blocking (or being blocked) would be pure friction."""
    holder = rl.RunLock(rl.CATALOG_INSERT, "scraper", "http://db", "svc").acquire()
    ran = []
    rl.guard_catalog_writes("hub_miner", lambda: ran.append(1), argv=["--preview"])
    assert ran == [1]
    holder.release()


def test_env_escape_hatch_skips_the_lock(db, monkeypatch, capsys):
    monkeypatch.setenv("WINGMAN_NO_RUN_LOCK", "1")
    holder = rl.RunLock(rl.CATALOG_INSERT, "scraper", "http://db", "svc").acquire()
    ran = []
    rl.guard_catalog_writes("hub_miner", lambda: ran.append(1), argv=[])
    assert ran == [1]
    assert "WITHOUT the catalog lock" in capsys.readouterr().out
    holder.release()


# --------------------------------------------------------------------------------------
# leases, takeover, and the fallback
# --------------------------------------------------------------------------------------

def test_expired_lease_is_taken_over_db(db):
    db.rows[rl.CATALOG_INSERT] = {"name": rl.CATALOG_INSERT, "holder": "a dead run",
                                  "token": "old", "expires_at": _past()}
    lock = rl.RunLock(rl.CATALOG_INSERT, "scraper", "http://db", "svc").acquire()
    assert lock.backend == "db"
    assert db.rows[rl.CATALOG_INSERT]["holder"].startswith("scraper")
    lock.release()


def test_expired_lease_is_taken_over_file(tmp_path):
    path = tmp_path / f".agent_{rl.CATALOG_INSERT}.lock"
    path.write_text(json.dumps({"holder": "a dead run", "expires_at": _past()}))
    rl.RunLock(rl.CATALOG_INSERT, "scraper").acquire().release()


def test_unreadable_lock_file_is_a_crashed_run_not_a_holder(tmp_path):
    """A run killed mid-write leaves a truncated file; that must not wedge the pipeline."""
    (tmp_path / f".agent_{rl.CATALOG_INSERT}.lock").write_text("{ truncated")
    rl.RunLock(rl.CATALOG_INSERT, "scraper").acquire().release()


def test_release_is_guarded_on_the_token(db):
    """A run whose lease was stolen must not delete its successor's lock."""
    stale = rl.RunLock(rl.CATALOG_INSERT, "scraper", "http://db", "svc").acquire()
    db.rows[rl.CATALOG_INSERT] = {"name": rl.CATALOG_INSERT, "holder": "successor",
                                  "token": "different",
                                  "expires_at": (rl._now() + datetime.timedelta(
                                      seconds=900)).isoformat()}
    stale.release()
    assert rl.CATALOG_INSERT in db.rows, "released a lock it did not hold"


def test_missing_table_falls_back_to_the_file_lock(monkeypatch, capsys):
    fake = FakeDB(missing_table=True)
    monkeypatch.setattr(rl, "supabase_get", fake.get)
    monkeypatch.setattr(rl, "supabase_post", fake.post)
    lock = rl.RunLock(rl.CATALOG_INSERT, "scraper", "http://db", "svc").acquire()
    assert lock.backend == "file"
    assert "agent_locks not found" in capsys.readouterr().out
    # and it still excludes, on this machine
    with pytest.raises(rl.LockBusy):
        rl.RunLock(rl.CATALOG_INSERT, "hub_miner", "http://db", "svc").acquire()
    lock.release()


def test_current_holder_is_none_when_free_and_never_raises(db):
    assert rl.current_holder("http://db", "svc") is None
    assert rl.current_holder(None, None) is None


# --------------------------------------------------------------------------------------
# id minting
# --------------------------------------------------------------------------------------

def test_mint_ids_uses_the_sequence_when_present(monkeypatch):
    monkeypatch.setattr(rl, "supabase_rpc",
                        lambda url, fn, payload, key, **kw: [f"ec{9000 + i}"
                                                             for i in range(payload["n"])])
    fallback = iter(["NEVER"])
    assert rl.mint_ids("http://db", "svc", 3, fallback) == ["ec9000", "ec9001", "ec9002"]


def test_mint_ids_falls_back_when_the_function_is_absent(monkeypatch):
    def boom(*a, **kw):
        raise _http_error("PGRST202", status=404)
    monkeypatch.setattr(rl, "supabase_rpc", boom)
    fallback = iter(["ec1", "ec2"])
    assert rl.mint_ids("http://db", "svc", 2, fallback) == ["ec1", "ec2"]


def test_mint_ids_falls_back_on_a_short_result(monkeypatch):
    """A partial answer is not usable — two rows sharing an id is the bug being fixed."""
    monkeypatch.setattr(rl, "supabase_rpc", lambda *a, **kw: ["ec9000"])
    fallback = iter(["ec1", "ec2", "ec3"])
    assert rl.mint_ids("http://db", "svc", 3, fallback) == ["ec1", "ec2", "ec3"]


# --------------------------------------------------------------------------------------
# the wiring itself — an unwired agent is the whole bug back again
# --------------------------------------------------------------------------------------

_INSERTING_AGENTS = ["agents/scrape_opportunities.py", "agents/mine_hub_pages.py",
                     "agents/harvest_names.py", "agents/refind_dead_links.py"]


@pytest.mark.parametrize("path", _INSERTING_AGENTS)
def test_every_inserting_agent_takes_the_lock(path):
    import os
    from wingman import REPO_ROOT
    with open(os.path.join(REPO_ROOT, path), encoding="utf-8") as f:
        src = f.read()
    assert "guard_catalog_writes" in src, f"{path} inserts into opportunities without the lock"
    assert 'if __name__ == "__main__":\n    main()\n' not in src, (
        f"{path} still calls main() unguarded")


def test_ops_console_knows_the_same_four_scripts():
    """If a fifth inserting agent is added, both lists must learn about it."""
    import os
    os.environ.setdefault("WINGMAN_ENABLE_OPS", "1")
    from ops import core
    assert set(core.CATALOG_INSERT_SCRIPTS) == set(_INSERTING_AGENTS)

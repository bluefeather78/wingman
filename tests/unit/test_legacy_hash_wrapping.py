"""Legacy password hashes, and the client-hash shape — S1-11, finding L1.

Rows that have not logged in since Phase 2 still hold the bare client SHA-256. The client
sends sha256(password) UNSALTED, and the server compares it to that stored value directly —
so the stored value IS the credential on the wire. A database read lets an attacker sign in
as those accounts with no cracking at all: paste the column into the login request.
"""
import importlib.util
import json
import pathlib

import pytest

import app.routes.account as account
from app.auth.passwords import (hash_password, is_legacy_hash, is_valid_client_hash,
                                verify_password)
from app.auth.ratelimit import RateLimiter


SHA = "a" * 64


# ---------------- the wrap ----------------

def test_a_wrapped_legacy_hash_verifies_through_the_existing_path():
    """argon2(sha256hex) is the whole migration: the same value the browser sends still
    verifies, it just no longer sits in the database in replayable form."""
    wrapped = hash_password(SHA)
    ok, needs_upgrade = verify_password(wrapped, SHA)
    assert ok is True
    assert needs_upgrade is False        # already argon2; nothing left to upgrade


def test_the_wrapped_value_is_no_longer_replayable():
    wrapped = hash_password(SHA)
    assert not is_legacy_hash(wrapped)
    assert wrapped != SHA
    # Presenting the STORED value as the password must not work any more. That it did is
    # the finding.
    assert verify_password(wrapped, wrapped) == (False, False)


def test_the_unwrapped_row_is_exactly_the_replay_attack():
    """The before picture, asserted so the fix is measured against something real."""
    assert verify_password(SHA, SHA) == (True, True)


def test_wrapping_is_idempotent():
    once = hash_password(SHA)
    twice = hash_password(once)
    assert verify_password(once, SHA)[0] is True
    assert verify_password(twice, SHA)[0] is False   # double-wrapped is a different secret
    assert not is_legacy_hash(once)


def test_the_login_time_upgrade_still_asks_for_a_wrap():
    """The script exists for accounts that never log in again; the login path handles the
    rest, and must keep doing so."""
    assert verify_password(SHA, SHA)[1] is True


# ---------------- the migration script ----------------

def _load_script():
    path = pathlib.Path("scripts/one-off/wrap_legacy_password_hashes.py")
    spec = importlib.util.spec_from_file_location("wrap_legacy", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_script_previews_by_default(monkeypatch, capsys):
    mod = _load_script()
    monkeypatch.setenv("SUPABASE_URL", "https://db.example")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "svc")
    monkeypatch.setattr(mod, "load_dotenv", lambda *a: None)
    monkeypatch.setattr(mod, "supabase_get",
                        lambda *a, **k: [{"userid": "alice", "password_hash": SHA}])
    monkeypatch.setattr(mod, "supabase_patch",
                        lambda *a, **k: pytest.fail("preview must not write"))
    monkeypatch.setattr(mod.sys, "argv", ["wrap"])
    assert mod.main() == 0
    assert "PREVIEW" in capsys.readouterr().out


def test_the_script_wraps_only_legacy_rows(monkeypatch):
    mod = _load_script()
    written = []
    monkeypatch.setenv("SUPABASE_URL", "https://db.example")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "svc")
    monkeypatch.setattr(mod, "load_dotenv", lambda *a: None)
    monkeypatch.setattr(mod, "supabase_get", lambda *a, **k: [
        {"userid": "legacy", "password_hash": SHA},
        {"userid": "modern", "password_hash": hash_password(SHA)},
        {"userid": "google-only", "password_hash": None},
    ])
    monkeypatch.setattr(mod, "supabase_patch",
                        lambda url, table, params, body, key: written.append(
                            (params, body)))
    monkeypatch.setattr(mod.sys, "argv", ["wrap", "--commit"])
    assert mod.main() == 0
    assert len(written) == 1
    assert written[0][0] == {"userid": "eq.legacy"}
    assert verify_password(written[0][1]["password_hash"], SHA)[0] is True


def test_the_script_reads_only_two_columns(monkeypatch):
    """No reason for a migration to hold anybody's email, name or app data in memory."""
    mod = _load_script()
    seen = {}
    monkeypatch.setenv("SUPABASE_URL", "https://db.example")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "svc")
    monkeypatch.setattr(mod, "load_dotenv", lambda *a: None)
    monkeypatch.setattr(mod, "supabase_get",
                        lambda url, table, params, key: seen.update(params) or [])
    monkeypatch.setattr(mod.sys, "argv", ["wrap"])
    mod.main()
    assert seen["select"] == "userid,password_hash"


def test_the_script_refuses_without_the_service_key(monkeypatch, capsys):
    mod = _load_script()
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    monkeypatch.setattr(mod, "load_dotenv", lambda *a: None)
    monkeypatch.setattr(mod.sys, "argv", ["wrap", "--commit"])
    assert mod.main() == 1


# ---------------- the client-hash shape ----------------

@pytest.mark.parametrize("value", ["", None, "x", "A" * 64, "g" * 64, "a" * 63,
                                   "a" * 65, 12345, "a" * 32])
def test_a_value_that_is_not_a_sha256_hex_is_refused(value):
    assert is_valid_client_hash(value) is False


def test_a_real_client_hash_is_accepted():
    assert is_valid_client_hash(SHA) is True


@pytest.fixture(autouse=True)
def _fresh_limiters(monkeypatch):
    monkeypatch.setattr(account, "login_limiter", RateLimiter(100, 300))
    monkeypatch.setattr(account, "login_ip_limiter", RateLimiter(100, 300))
    monkeypatch.setattr(account, "register_limiter", RateLimiter(100, 3600))
    monkeypatch.setattr(account, "register_email_limiter", RateLimiter(100, 3600))
    monkeypatch.setattr(account, "client_ip", lambda _r: "1.2.3.4")


def test_register_refuses_a_malformed_password_hash(monkeypatch):
    """A caller sending a one-character 'hash' got an account whose password-equivalent
    secret was one character. The browser's hashing was the only thing preventing it."""
    monkeypatch.setattr(account, "create_user",
                        lambda *a, **k: pytest.fail("must not create the account"))
    resp = account.handle_register(request=None, body={
        "firstName": "A", "lastName": "B", "email": "a@example.com", "userid": "u",
        "passwordHash": "x", "isAdult": True, "acceptedTerms": True,
    })
    assert resp.status_code == 400


def test_login_answers_the_ordinary_failure_for_a_malformed_hash(monkeypatch):
    """Not a distinct 400: telling a caller their input was the wrong SHAPE is one bit more
    than they need on a route anyone can reach."""
    monkeypatch.setattr(account, "get_user_account",
                        lambda _k: pytest.fail("must not reach Supabase"))
    resp = account.handle_login(request=None,
                                body={"userid": "alice", "passwordHash": "x"})
    assert resp.status_code == 401
    assert json.loads(resp.body)["error"] == account.LOGIN_FAILED


def test_a_valid_shape_still_reaches_the_lookup(monkeypatch):
    reached = []
    monkeypatch.setattr(account, "get_user_account",
                        lambda k: reached.append(k) or None)
    account.handle_login(request=None, body={"userid": "alice", "passwordHash": SHA})
    assert reached == ["alice"]

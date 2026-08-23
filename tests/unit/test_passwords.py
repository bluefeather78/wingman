"""Unit tests for app.auth.passwords — argon2 over the client SHA-256, legacy upgrade.

argon2 output is non-deterministic (random salt), so we verify, never compare hashes.
"""
import hashlib

import pytest

from app.auth.passwords import is_legacy_hash, hash_password, verify_password


def _sha256_hex(s):
    return hashlib.sha256(s.encode()).hexdigest()


# ---------- is_legacy_hash: 64-char lowercase hex boundary ----------

@pytest.mark.parametrize("value,expected", [
    ("a" * 64, True),                       # 64 lowercase hex
    (_sha256_hex("hunter2"), True),         # a real client SHA-256
    ("0123456789abcdef" * 4, True),         # exactly 64 hex
    ("A" * 64, False),                      # uppercase hex not matched (client lowercases)
    ("a" * 63, False),                      # too short
    ("a" * 65, False),                      # too long
    ("g" * 64, False),                      # non-hex char
    ("", False),                            # empty
    (None, False),                          # None
    ("$argon2id$v=19$m=65536,t=3,p=4$abc", False),  # argon2 hash
])
def test_is_legacy_hash(value, expected):
    assert is_legacy_hash(value) is expected


# ---------- hash + verify round-trip ----------

def test_hash_password_is_argon2_and_nondeterministic():
    ch = _sha256_hex("pw")
    h1 = hash_password(ch)
    h2 = hash_password(ch)
    assert h1.startswith("$argon2")
    assert h1 != h2  # random salt


def test_verify_argon2_roundtrip():
    ch = _sha256_hex("correct horse")
    stored = hash_password(ch)
    ok, needs_upgrade = verify_password(stored, ch)
    assert ok is True
    # Fresh hash at default params should not need a rehash.
    assert needs_upgrade is False


def test_verify_argon2_wrong_password():
    stored = hash_password(_sha256_hex("right"))
    ok, needs_upgrade = verify_password(stored, _sha256_hex("wrong"))
    assert (ok, needs_upgrade) == (False, False)


# ---------- legacy SHA-256 path ----------

def test_legacy_match_flags_needs_upgrade():
    ch = _sha256_hex("legacypw")
    ok, needs_upgrade = verify_password(ch, ch)   # stored == bare SHA-256
    assert ok is True
    assert needs_upgrade is True


def test_legacy_wrong_password():
    stored = _sha256_hex("legacypw")
    ok, needs_upgrade = verify_password(stored, _sha256_hex("nope"))
    assert (ok, needs_upgrade) == (False, False)


# ---------- empty stored / empty hash (Google-only accounts) ----------

@pytest.mark.parametrize("stored,client", [
    ("", _sha256_hex("x")),   # Google-only account: no password stored
    (None, _sha256_hex("x")),
    (_sha256_hex("x"), ""),   # no incoming hash
    (_sha256_hex("x"), None),
    ("", ""),
    (None, None),
])
def test_empty_never_matches(stored, client):
    assert verify_password(stored, client) == (False, False)


# ---------- any exception -> (False, False) ----------

def test_malformed_argon2_hash_returns_false():
    # Not a legacy hash (has non-hex chars / wrong length) and not valid argon2 ->
    # _ph.verify raises InvalidHashError, which is swallowed.
    ok, needs_upgrade = verify_password("$argon2id$garbage", _sha256_hex("x"))
    assert (ok, needs_upgrade) == (False, False)

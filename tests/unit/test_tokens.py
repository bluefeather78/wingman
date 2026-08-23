"""Unit tests for app.auth.tokens — issue/verify JWT access+refresh pair.

Seams frozen: the module reads JWT_SECRET at import time (it does
`from app.config import JWT_SECRET`), so `app.auth.tokens.JWT_SECRET` is the name
tests monkeypatch, not the config one. Time is controlled via the module's `_now`.
"""
import datetime

import jwt
import pytest

from app.auth import tokens as T
from app.auth.tokens import (
    issue_tokens, verify_access_token, verify_refresh_token,
    AuthError, AuthConfigError,
)
from app.config import (
    ACCESS_TOKEN_TTL_SECONDS, REFRESH_TOKEN_TTL_SECONDS, JWT_ALGORITHM,
)


def _raw_claims(token):
    """Decode without verifying exp, to read claim contents directly.

    Uses the module's own live secret (T.JWT_SECRET) rather than a hardcoded one, so
    the test doesn't assume what JWT_SECRET resolved to (conftest sets it, but .env or a
    real env var can win over setdefault).
    """
    return jwt.decode(token, T.JWT_SECRET, algorithms=[JWT_ALGORITHM],
                      options={"verify_exp": False})


# ---------- issue_tokens: claim contents & shape ----------

def test_issue_tokens_shape():
    pair = issue_tokens("alice")
    assert set(pair) == {"token", "refresh_token", "token_type", "expires_in"}
    assert pair["token_type"] == "Bearer"
    assert pair["expires_in"] == ACCESS_TOKEN_TTL_SECONDS


def test_access_claims_type_ver_iat_exp():
    pair = issue_tokens("alice", token_version=3)
    claims = _raw_claims(pair["token"])
    assert claims["type"] == "access"
    assert claims["sub"] == "alice"
    assert claims["ver"] == 3
    assert "iat" in claims and "exp" in claims
    # exp is TTL seconds after iat
    assert claims["exp"] - claims["iat"] == ACCESS_TOKEN_TTL_SECONDS


def test_refresh_claims_type_ver_exp():
    pair = issue_tokens("alice", token_version=3)
    claims = _raw_claims(pair["refresh_token"])
    assert claims["type"] == "refresh"
    assert claims["ver"] == 3
    assert claims["exp"] - claims["iat"] == REFRESH_TOKEN_TTL_SECONDS


def test_userid_lowercased_and_stripped():
    pair = issue_tokens("  MixedCase  ", token_version=0)
    assert _raw_claims(pair["token"])["sub"] == "mixedcase"


def test_token_version_defaults_and_none_coerces_to_zero():
    assert _raw_claims(issue_tokens("bob")["token"])["ver"] == 0
    # token_version=None -> int(0)
    assert _raw_claims(issue_tokens("bob", None)["token"])["ver"] == 0


# ---------- round-trip issue -> verify ----------

def test_roundtrip_access():
    pair = issue_tokens("Alice")
    assert verify_access_token(pair["token"]) == "alice"


def test_roundtrip_refresh_returns_userid_and_ver():
    pair = issue_tokens("Alice", token_version=7)
    sub, ver = verify_refresh_token(pair["refresh_token"])
    assert sub == "alice"
    assert ver == 7


# ---------- wrong type (critical) ----------

def test_access_presented_as_refresh_rejected():
    pair = issue_tokens("alice")
    with pytest.raises(AuthError, match="Wrong token type"):
        verify_refresh_token(pair["token"])


def test_refresh_presented_as_access_rejected():
    pair = issue_tokens("alice")
    with pytest.raises(AuthError, match="Wrong token type"):
        verify_access_token(pair["refresh_token"])


# ---------- expired ----------

def test_expired_access_token(monkeypatch):
    # Issue with _now far in the past so exp is already behind real now.
    past = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
    monkeypatch.setattr(T, "_now", lambda: past)
    pair = issue_tokens("alice")
    monkeypatch.undo()  # decode uses real clock
    with pytest.raises(AuthError, match="expired"):
        verify_access_token(pair["token"])


def test_expired_refresh_token(monkeypatch):
    past = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=60)
    monkeypatch.setattr(T, "_now", lambda: past)
    pair = issue_tokens("alice")
    monkeypatch.undo()
    with pytest.raises(AuthError, match="expired"):
        verify_refresh_token(pair["refresh_token"])


# ---------- invalid signature ----------

def test_invalid_signature_rejected():
    now = datetime.datetime.now(datetime.timezone.utc)
    forged = jwt.encode(
        {"sub": "alice", "type": "access", "ver": 0,
         "iat": now, "exp": now + datetime.timedelta(hours=1)},
        "a-different-secret", algorithm=JWT_ALGORITHM,
    )
    with pytest.raises(AuthError, match="Invalid token"):
        verify_access_token(forged)


def test_garbage_token_rejected():
    with pytest.raises(AuthError, match="Invalid token"):
        verify_access_token("not.a.jwt")


# ---------- empty sub ----------

def test_empty_sub_rejected():
    # userid "   " strips to "" -> a valid-shaped token with no subject.
    pair = issue_tokens("   ")
    assert _raw_claims(pair["token"])["sub"] == ""
    with pytest.raises(AuthError, match="no subject"):
        verify_access_token(pair["token"])


# ---------- missing token ----------

def test_missing_token_rejected():
    with pytest.raises(AuthError, match="Missing token"):
        verify_access_token("")


# ---------- unset secret -> AuthConfigError (NOT AuthError) ----------

def test_issue_without_secret_raises_config_error(monkeypatch):
    monkeypatch.setattr(T, "JWT_SECRET", "")
    with pytest.raises(AuthConfigError):
        issue_tokens("alice")


def test_verify_without_secret_raises_config_error(monkeypatch):
    pair = issue_tokens("alice")  # minted while secret is set
    monkeypatch.setattr(T, "JWT_SECRET", "")
    with pytest.raises(AuthConfigError):
        verify_access_token(pair["token"])


def test_config_error_is_not_auth_error():
    # A misconfiguration must surface distinctly from a bad login.
    assert not issubclass(AuthConfigError, AuthError)

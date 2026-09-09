"""Sign in with Apple — native iOS endpoint (App Store 4.8).

Two halves:
  * _verify_identity_token — the RS256 / aud / iss / exp / required-claims gate. Signed with a
    throwaway RSA key and a fake JWKS client, so no network and no real Apple key.
  * handle_apple_native — account resolution, mirroring the Google findings: link ONLY on a
    verified email (S0-8/H1), pending->consent->create, and log-in-on-race.

Handlers are called directly with `body=...` (the same style as test_login_enumeration.py), and
the app.core / deps names the route imported are monkeypatched on the route module.
"""
import datetime
import json

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.exceptions import PyJWKClientConnectionError

import app.config as config
import app.routes.apple_oauth as apple


# ---------------------------------------------------------------- token verification

_PRIV = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PUB = _PRIV.public_key()


class _FakeKey:
    def __init__(self, key):
        self.key = key


class _FakeClient:
    """Stands in for PyJWKClient: always returns our throwaway public key."""
    def __init__(self, key):
        self._key = key

    def get_signing_key_from_jwt(self, _token):
        return _FakeKey(self._key)


def _sign(claims, key=_PRIV):
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "testkey"})


def _good_claims(**over):
    now = datetime.datetime.now(datetime.timezone.utc)
    c = {
        "iss": config.APPLE_ISSUER,
        "aud": config.APPLE_CLIENT_ID,
        "sub": "000123.abcdef.0001",
        "exp": int((now + datetime.timedelta(minutes=5)).timestamp()),
        "iat": int(now.timestamp()),
        "email": "kid@icloud.com",
        "email_verified": "true",
    }
    c.update(over)
    return c


@pytest.fixture
def use_pub(monkeypatch):
    monkeypatch.setattr(apple, "_apple_jwks_client", lambda: _FakeClient(_PUB))


def test_a_valid_token_verifies_and_returns_claims(use_pub):
    claims = apple._verify_identity_token(_sign(_good_claims()))
    assert claims["sub"] == "000123.abcdef.0001"
    assert claims["email"] == "kid@icloud.com"


def test_wrong_audience_is_rejected(use_pub):
    with pytest.raises(apple._AppleTokenError):
        apple._verify_identity_token(_sign(_good_claims(aud="com.someone.else.app")))


def test_wrong_issuer_is_rejected(use_pub):
    with pytest.raises(apple._AppleTokenError):
        apple._verify_identity_token(_sign(_good_claims(iss="https://accounts.google.com")))


def test_an_expired_token_is_rejected(use_pub):
    past = int((datetime.datetime.now(datetime.timezone.utc)
                - datetime.timedelta(hours=1)).timestamp())
    with pytest.raises(apple._AppleTokenError):
        apple._verify_identity_token(_sign(_good_claims(exp=past)))


def test_a_token_missing_sub_is_rejected(use_pub):
    claims = _good_claims()
    del claims["sub"]
    with pytest.raises(apple._AppleTokenError):
        apple._verify_identity_token(_sign(claims))


def test_a_token_signed_by_a_different_key_is_rejected(use_pub):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(apple._AppleTokenError):
        apple._verify_identity_token(_sign(_good_claims(), key=other))


def test_an_empty_token_is_rejected(use_pub):
    with pytest.raises(apple._AppleTokenError):
        apple._verify_identity_token("")


def test_unreachable_apple_keys_is_a_503_class_error(monkeypatch):
    class _Down:
        def get_signing_key_from_jwt(self, _t):
            raise PyJWKClientConnectionError("cannot reach appleid.apple.com")
    monkeypatch.setattr(apple, "_apple_jwks_client", lambda: _Down())
    with pytest.raises(apple._AppleKeysUnavailable):
        apple._verify_identity_token(_sign(_good_claims()))


# ---------------------------------------------------------------- endpoint resolution

@pytest.fixture(autouse=True)
def _no_email(monkeypatch):
    # The welcome email would otherwise try to reach Supabase/Resend.
    monkeypatch.setattr(apple, "send_lifecycle_email_async", lambda *a, **k: None)
    # login_response mints tokens and records refresh lineage (a DB write); the tests here are
    # about WHICH branch runs, so stub it to a recognizable payload.
    monkeypatch.setattr(apple, "login_response",
                        lambda record, *a, **k: {"ok": True, "userid": record["userid"]})


def _verify_ok(monkeypatch, **claims_over):
    monkeypatch.setattr(apple, "_verify_identity_token",
                        lambda _t: _good_claims(**claims_over))


def _body(json_resp):
    return json.loads(json_resp.body)


def test_bad_token_is_401(monkeypatch):
    monkeypatch.setattr(apple, "_verify_identity_token",
                        lambda _t: (_ for _ in ()).throw(apple._AppleTokenError("nope")))
    resp = apple.handle_apple_native(body={"identity_token": "x"})
    assert resp.status_code == 401


def test_jwks_outage_is_503(monkeypatch):
    monkeypatch.setattr(apple, "_verify_identity_token",
                        lambda _t: (_ for _ in ()).throw(apple._AppleKeysUnavailable("down")))
    resp = apple.handle_apple_native(body={"identity_token": "x"})
    assert resp.status_code == 503


def test_existing_apple_account_logs_straight_in(monkeypatch):
    _verify_ok(monkeypatch)
    monkeypatch.setattr(apple, "get_user_by_apple_id",
                        lambda sub: {"userid": "alice"} if sub == "000123.abcdef.0001" else None)
    monkeypatch.setattr(apple, "get_user_account", lambda uid: {"userid": uid})
    resp = apple.handle_apple_native(body={"identity_token": "x"})
    assert resp.status_code == 200
    assert _body(resp) == {"ok": True, "userid": "alice"}


def test_a_verified_email_links_to_an_existing_account(monkeypatch):
    _verify_ok(monkeypatch, email="known@icloud.com", email_verified="true")
    monkeypatch.setattr(apple, "get_user_by_apple_id", lambda sub: None)
    monkeypatch.setattr(apple, "get_user_by_email", lambda e: {"userid": "bob"})
    monkeypatch.setattr(apple, "get_user_account", lambda uid: {"userid": uid})
    patched = {}
    monkeypatch.setattr(apple, "_users_request",
                        lambda method, q, data=None: patched.update({"m": method, "data": data}))
    resp = apple.handle_apple_native(body={"identity_token": "x"})
    assert resp.status_code == 200
    assert _body(resp)["userid"] == "bob"
    # The link actually happened, and it set apple_id.
    assert patched["m"] == "PATCH"
    assert patched["data"] == {"apple_id": "000123.abcdef.0001"}


def test_an_unverified_email_is_never_linked(monkeypatch):
    """S0-8/H1 parity: an unverified address is an unproven claim, so it must not attach the
    Apple id to someone else's account. With no consent it falls through to pending."""
    _verify_ok(monkeypatch, email="victim@icloud.com", email_verified="false")
    monkeypatch.setattr(apple, "get_user_by_apple_id", lambda sub: None)
    called = {"by_email": 0}
    def _by_email(_e):
        called["by_email"] += 1
        return {"userid": "victim"}
    monkeypatch.setattr(apple, "get_user_by_email", _by_email)
    resp = apple.handle_apple_native(body={"identity_token": "x"})
    assert called["by_email"] == 0            # never even looked the address up
    assert _body(resp)["pending"] is True


def test_a_new_account_without_consent_returns_pending(monkeypatch):
    _verify_ok(monkeypatch, email="new@icloud.com")
    monkeypatch.setattr(apple, "get_user_by_apple_id", lambda sub: None)
    monkeypatch.setattr(apple, "get_user_by_email", lambda e: None)
    resp = apple.handle_apple_native(body={"identity_token": "x",
                                           "first_name": "Sam", "last_name": "Lee"})
    assert resp.status_code == 200
    body = _body(resp)
    assert body["pending"] is True
    assert body["email"] == "new@icloud.com"
    assert body["firstName"] == "Sam" and body["lastName"] == "Lee"


def test_a_new_account_with_consent_is_created_with_the_apple_id(monkeypatch):
    _verify_ok(monkeypatch, email="new@icloud.com")
    monkeypatch.setattr(apple, "get_user_by_apple_id", lambda sub: None)
    monkeypatch.setattr(apple, "get_user_by_email", lambda e: None)
    monkeypatch.setattr(apple, "_unique_userid_from_email", lambda e: "new1")
    created = {}
    def _create(userid, first, last, email, pw, **kw):
        created.update(userid=userid, email=email, pw=pw, kw=kw)
    monkeypatch.setattr(apple, "create_user", _create)
    monkeypatch.setattr(apple, "get_user", lambda uid: {"userid": uid})
    resp = apple.handle_apple_native(body={
        "identity_token": "x", "first_name": "Sam", "last_name": "Lee",
        "isAdult": True, "acceptedTerms": True,
    })
    assert resp.status_code == 200
    assert _body(resp)["userid"] == "new1"
    # Password-less account, apple_id passed through (not google_id).
    assert created["pw"] is None
    assert created["kw"]["apple_id"] == "000123.abcdef.0001"


def test_consent_without_accepted_terms_is_refused(monkeypatch):
    _verify_ok(monkeypatch, email="new@icloud.com")
    monkeypatch.setattr(apple, "get_user_by_apple_id", lambda sub: None)
    monkeypatch.setattr(apple, "get_user_by_email", lambda e: None)
    # A create attempt would be a bug — fail loudly if the gate is skipped.
    monkeypatch.setattr(apple, "create_user",
                        lambda *a, **k: pytest.fail("create_user must not run without consent"))
    resp = apple.handle_apple_native(body={"identity_token": "x", "isAdult": True,
                                           "acceptedTerms": False})
    assert resp.status_code == 400


def test_a_minor_without_parental_consent_is_refused(monkeypatch):
    _verify_ok(monkeypatch, email="new@icloud.com")
    monkeypatch.setattr(apple, "get_user_by_apple_id", lambda sub: None)
    monkeypatch.setattr(apple, "get_user_by_email", lambda e: None)
    monkeypatch.setattr(apple, "create_user",
                        lambda *a, **k: pytest.fail("create_user must not run without consent"))
    resp = apple.handle_apple_native(body={"identity_token": "x", "isAdult": False,
                                           "parentalConsent": False, "acceptedTerms": True})
    assert resp.status_code == 400


def test_consent_but_no_verified_email_cannot_create(monkeypatch):
    """A consent POST for an account with no verified email has nothing to key on."""
    _verify_ok(monkeypatch, email="", email_verified="false")
    monkeypatch.setattr(apple, "get_user_by_apple_id", lambda sub: None)
    monkeypatch.setattr(apple, "create_user",
                        lambda *a, **k: pytest.fail("must not create without a verified email"))
    resp = apple.handle_apple_native(body={"identity_token": "x", "isAdult": True,
                                           "acceptedTerms": True})
    assert resp.status_code == 400


def test_missing_apple_columns_names_the_migration(monkeypatch):
    _verify_ok(monkeypatch, email="new@icloud.com")
    monkeypatch.setattr(apple, "get_user_by_apple_id", lambda sub: None)
    monkeypatch.setattr(apple, "get_user_by_email", lambda e: None)
    monkeypatch.setattr(apple, "_unique_userid_from_email", lambda e: "new1")
    def _create(*a, **k):
        raise apple.MissingUserColumns()
    monkeypatch.setattr(apple, "create_user", _create)
    resp = apple.handle_apple_native(body={"identity_token": "x", "isAdult": True,
                                           "acceptedTerms": True})
    assert resp.status_code == 503
    assert "db/apple_auth_schema.sql" in _body(resp)["error"]

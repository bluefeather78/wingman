"""Sign-in and sign-up must not enumerate accounts — S1-7, finding M7.

Login answered 404 "No account found with that user ID." for an unknown userid and 401
"Incorrect password." for a wrong password, so a valid userid was free to confirm. Register
answers a distinct 409 for a taken email. The population here is largely minors, so a list
of "these addresses have accounts" is itself sensitive.
"""
import json

import pytest

import app.routes.account as account
from app.auth.ratelimit import RateLimiter

# A well-formed client hash: 64 lowercase hex. S1-11 rejects anything else before the
# lookup, so these tests have to send the real shape to exercise the paths they are about.
VALID_HASH = "ab12" * 16


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(account, "login_limiter", RateLimiter(100, 300))
    monkeypatch.setattr(account, "login_ip_limiter", RateLimiter(100, 300))
    monkeypatch.setattr(account, "register_limiter", RateLimiter(100, 3600))
    monkeypatch.setattr(account, "register_email_limiter", RateLimiter(3, 3600))
    monkeypatch.setattr(account, "client_ip", lambda _r: "1.2.3.4")


def _login(userid, monkeypatch, record=None, password_ok=False):
    monkeypatch.setattr(account, "get_user_account", lambda _k: record)
    monkeypatch.setattr(account, "verify_password", lambda *a: (password_ok, False))
    return account.handle_login(request=None,
                                body={"userid": userid, "passwordHash": VALID_HASH})


# ---------------- login ----------------

def test_an_unknown_userid_and_a_wrong_password_are_indistinguishable(monkeypatch):
    missing = _login("ghost", monkeypatch, record=None)
    wrong = _login("alice", monkeypatch, record={"userid": "alice", "password_hash": "x"},
                   password_ok=False)
    assert missing.status_code == wrong.status_code == 401
    assert json.loads(missing.body) == json.loads(wrong.body)


def test_the_status_converges_too_not_just_the_wording(monkeypatch):
    """404 vs 401 told the two apart on its own, so changing only the message would have
    left the oracle intact."""
    assert _login("ghost", monkeypatch, record=None).status_code == 401


def test_the_message_names_both_fields(monkeypatch):
    body = json.loads(_login("ghost", monkeypatch, record=None).body)
    assert body["error"] == account.LOGIN_FAILED
    assert "user ID" in body["error"] and "password" in body["error"]


def test_the_two_call_sites_share_one_constant():
    """They drifted apart once; a named constant is what stops it happening twice."""
    import inspect
    code = "\n".join(line for line in inspect.getsource(account.handle_login).split("\n")
                     if not line.lstrip().startswith("#"))
    assert code.count("LOGIN_FAILED") == 3   # unknown user, wrong password,
                                            # and S1-11's malformed-hash path
    assert "Incorrect password." not in code
    assert "No account found with that user ID." not in code


def test_a_real_sign_in_still_succeeds(monkeypatch):
    monkeypatch.setattr(account, "ensure_trial_started", lambda _k, r: r)
    monkeypatch.setattr(account, "touch_user_activity", lambda *a: None)
    monkeypatch.setattr(account, "login_response", lambda r: {"ok": True})
    resp = _login("alice", monkeypatch,
                  record={"userid": "alice", "password_hash": "x"}, password_ok=True)
    assert resp.status_code == 200


def test_a_supabase_failure_is_still_a_502_not_a_disguised_401(monkeypatch):
    """Converging the failure messages must not swallow a real outage into 'wrong
    password', which would send every student to reset a password that works."""
    def _boom(_k):
        raise RuntimeError("down")
    monkeypatch.setattr(account, "get_user_account", _boom)
    resp = account.handle_login(request=None, body={"userid": "a", "passwordHash": VALID_HASH})
    assert resp.status_code == 502


# ---------------- register ----------------

def _register(email, monkeypatch, taken_email=False):
    monkeypatch.setattr(account, "get_user_account", lambda _k: None)
    monkeypatch.setattr(account, "get_user_by_email",
                        lambda _e: {"userid": "someone"} if taken_email else None)
    monkeypatch.setattr(account, "create_user", lambda *a, **k: None)
    monkeypatch.setattr(account, "send_lifecycle_email_async", lambda *a, **k: None)
    monkeypatch.setattr(account, "login_response", lambda r: {"ok": True})
    monkeypatch.setattr(account, "hash_password", lambda h: "argon2$" + h)
    return account.handle_register(request=None, body={
        "firstName": "A", "lastName": "B", "email": email, "userid": "newuser",
        "passwordHash": VALID_HASH, "isAdult": True, "acceptedTerms": True,
    })


def test_probing_one_address_runs_out_of_budget(monkeypatch):
    """The oracle stays — it cannot be removed without a signup flow that does not hand
    back tokens inline — but reading it is no longer free."""
    for _ in range(3):
        assert _register("taken@example.com", monkeypatch, taken_email=True).status_code == 409
    assert _register("taken@example.com", monkeypatch, taken_email=True).status_code == 429


def test_the_budget_is_per_address_not_per_ip(monkeypatch):
    """Per-IP would let a script walk a list from one machine as fast as it likes."""
    for _ in range(3):
        _register("first@example.com", monkeypatch, taken_email=True)
    assert _register("second@example.com", monkeypatch, taken_email=True).status_code == 409


def test_case_and_whitespace_cannot_buy_a_fresh_budget(monkeypatch):
    for _ in range(3):
        _register("taken@example.com", monkeypatch, taken_email=True)
    assert _register("  TAKEN@Example.COM  ", monkeypatch,
                     taken_email=True).status_code == 429


def test_a_malformed_address_does_not_spend_a_real_one_s_budget(monkeypatch):
    for _ in range(5):
        assert _register("not-an-email", monkeypatch).status_code == 400
    assert _register("fresh@example.com", monkeypatch).status_code == 200


def test_an_ordinary_registration_still_works(monkeypatch):
    assert _register("new@example.com", monkeypatch).status_code == 200


def test_the_userid_conflict_is_deliberately_left_alone(monkeypatch):
    """The user chose it and it is visible to other students; only the EMAIL is the
    sensitive half."""
    monkeypatch.setattr(account, "get_user_account", lambda _k: {"userid": "newuser"})
    monkeypatch.setattr(account, "get_user_by_email", lambda _e: None)
    resp = account.handle_register(request=None, body={
        "firstName": "A", "lastName": "B", "email": "x@example.com", "userid": "newuser",
        "passwordHash": VALID_HASH, "isAdult": True, "acceptedTerms": True,
    })
    assert resp.status_code == 409
    assert "user ID" in json.loads(resp.body)["error"]

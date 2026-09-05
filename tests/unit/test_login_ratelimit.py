"""Unit tests for the sign-in rate limiter's key — S0-7 in SECURITY_HARDENING_PLAN.md,
finding H3.

The bug: login_limiter was keyed on client_ip alone, and uvicorn 0.52 defaults to
forwarded_allow_ips=None (effectively 127.0.0.1). Render's load balancer connects from the
private network, not loopback, so X-Forwarded-For was ignored and request.client.host was the
proxy's address for every visitor on earth. RateLimiter(10, 5*60) was therefore ONE BUCKET
FOR THE ENTIRE USER BASE: ten POST /api/login bodies locked every student out of sign-in for
five minutes, for free, repeatably.
"""
import pytest

from app.auth.ratelimit import RateLimiter
import app.routes.account as account


@pytest.fixture(autouse=True)
def _fresh_limiters(monkeypatch):
    monkeypatch.setattr(account, "login_limiter", RateLimiter(2, 300))
    monkeypatch.setattr(account, "login_ip_limiter", RateLimiter(100, 300))
    monkeypatch.setattr(account, "client_ip", lambda _r: "1.2.3.4")
    monkeypatch.setattr(account, "get_user_account", lambda _k: None)


def _login(userid):
    return account.handle_login(request=None, body={"userid": userid, "passwordHash": "x"})


def test_one_caller_cannot_lock_out_other_accounts():
    """The whole point of the (IP, userid) key: exhausting alice's bucket must leave bob able
    to sign in from the same address."""
    assert _login("alice").status_code != 429
    assert _login("alice").status_code != 429
    assert _login("alice").status_code == 429
    assert _login("bob").status_code != 429


def test_the_narrow_bucket_still_throttles_one_account():
    assert _login("alice").status_code != 429
    assert _login("alice").status_code != 429
    assert _login("alice").status_code == 429


def test_the_per_ip_backstop_catches_userid_rotation(monkeypatch):
    """The narrow key alone lets one address rotate userids forever, which is credential
    stuffing. The backstop is loose on purpose — a school NAT puts a whole cohort behind one
    address — but it is not absent."""
    monkeypatch.setattr(account, "login_ip_limiter", RateLimiter(3, 300))
    for i in range(3):
        assert _login(f"user{i}").status_code != 429
    assert _login("user99").status_code == 429


def test_the_userid_is_normalised_into_the_key():
    """Or 'Alice' and 'alice' would get a bucket each, doubling the ceiling for one account —
    userids are lowercased everywhere else for exactly this reason."""
    assert _login("Alice").status_code != 429
    assert _login("alice").status_code != 429
    assert _login("ALICE").status_code == 429


def test_render_does_not_trust_every_forwarded_header():
    """`--forwarded-allow-ips *` would be a BYPASS, not a fix: with always_trust uvicorn
    returns the LEFTMOST X-Forwarded-For entry, which is whatever the client sent, since
    proxies append. Pin that the start command never does that."""
    from wingman import REPO_ROOT
    import os

    render_yaml = open(os.path.join(REPO_ROOT, "render.yaml")).read()
    start = [l for l in render_yaml.splitlines() if "startCommand:" in l and "uvicorn" in l]
    assert start, "no uvicorn start command found"
    assert "--forwarded-allow-ips" in start[0]
    assert '"*"' not in start[0] and "'*'" not in start[0]
    assert "--forwarded-allow-ips *" not in start[0]


def test_uvicorn_really_does_take_the_leftmost_entry_when_it_trusts_everything():
    """The reason for the test above, asserted against the installed uvicorn rather than
    taken on trust — if a future version changes this, the `*` prohibition can be revisited
    instead of being cargo-culted."""
    from uvicorn.middleware.proxy_headers import _TrustedHosts

    spoofed_then_real = "1.2.3.4, 203.0.113.9"
    assert _TrustedHosts("*").get_trusted_client_address(spoofed_then_real)[0] == "1.2.3.4"
    # With a real trusted list it walks in reverse and returns the rightmost untrusted host.
    trusted = _TrustedHosts(["10.0.0.0/8", "127.0.0.1"])
    assert trusted.get_trusted_client_address("1.2.3.4, 203.0.113.9")[0] == "203.0.113.9"

"""Unit tests for the two Google-OAuth account-takeover findings — S0-8 (H1) and S0-9 (H2)
in SECURITY_HARDENING_PLAN.md.

These are the two findings that lose a student's ACCOUNT rather than money, so they are
tested by the exact strings from the security report's exploit walkthroughs.
"""
import pytest

import app.routes.google_oauth as goauth


# ---------- S0-9 / H2: the redirect allowlist was a string prefix match ----------

@pytest.fixture
def allow(monkeypatch):
    """Pin a production-shaped allowlist: one https origin, the native scheme, one dev port."""
    origins, schemes = goauth._parse_allowlist([
        "https://highschoolwingman.com", "wingman://", "http://localhost:8081",
    ])
    monkeypatch.setattr(goauth, "_ALLOWED_ORIGINS", origins)
    monkeypatch.setattr(goauth, "_ALLOWED_SCHEMES", schemes)
    return goauth._is_allowed_app_redirect


@pytest.mark.parametrize("uri", [
    # The exploit from the report, verbatim: the student sees the REAL Google consent screen,
    # signs in, and is 302'd to evil.tld carrying ?google_token=. The attacker then calls
    # /api/auth/google/session?token=... within five minutes and gets their access AND
    # refresh tokens.
    "https://highschoolwingman.com.evil.tld/",
    "https://highschoolwingman.com@evil.tld/",
    "http://localhost:8081.evil.tld",
    "http://localhost:8081@evil.tld/",
    "https://highschoolwingman.com.evil.tld",
    "https://evil.tld/https://highschoolwingman.com",
])
def test_the_lookalike_hosts_are_refused(allow, uri):
    assert allow(uri) is False


@pytest.mark.parametrize("uri", [
    "https://highschoolwingman.com",
    "https://highschoolwingman.com/",
    "https://highschoolwingman.com/tracker?x=1",
    "https://HighSchoolWingman.com",          # host comparison is case-insensitive
    "http://localhost:8081",
    "http://localhost:8081/tracker",
    "wingman://tracker",                      # native deep link, resolved on-device
])
def test_the_real_destinations_still_work(allow, uri):
    assert allow(uri) is True


@pytest.mark.parametrize("uri", [
    "http://highschoolwingman.com",           # scheme must match: https != http
    "https://localhost:8081",                 # scheme must match the other way too
    "http://localhost:8082",                  # port is part of the origin
    "http://localhost",                       # ...including its absence
    "https://evil.tld",
    "//evil.tld",                             # scheme-relative: no scheme at all
    "/tracker",
    "",
    "javascript:alert(1)",
])
def test_everything_else_is_refused(allow, uri):
    assert allow(uri) is False


def test_header_injection_characters_are_refused(allow):
    """The value ends up in a Location header."""
    for uri in ("https://highschoolwingman.com\r\nX-Evil: 1",
                "https://highschoolwingman.com\nX-Evil: 1",
                "https:\\\\evil.tld"):
        assert allow(uri) is False


def test_a_malformed_port_does_not_raise(allow):
    """urlsplit defers parsing the port, so .port raises ValueError on host:evil — an
    unhandled one here would 500 the sign-in route rather than refusing the redirect."""
    assert allow("http://localhost:evil") is False


def test_a_host_less_http_entry_is_dropped_from_the_allowlist():
    """A bare `http://` entry would mean 'any host on http' — the exact open redirect this
    allowlist exists to prevent — so it is ignored rather than honoured."""
    origins, schemes = goauth._parse_allowlist(["http://", "https://", "wingman://"])
    assert origins == set()
    assert schemes == {"wingman"}


def test_a_custom_scheme_entry_keeps_deep_links_working():
    """wingman:// is resolved by the device's app registry, not over the network, so there is
    no remote host for an attacker to point it at — which is why it matches by scheme."""
    origins, schemes = goauth._parse_allowlist(["wingman://"])
    assert schemes == {"wingman"} and origins == set()


def test_the_calendar_redirect_uses_the_same_check():
    """It only leaks calendar_connected=1, but it is the same function and must not drift."""
    import inspect
    src = inspect.getsource(goauth.handle_google_calendar_start)
    assert "_is_allowed_app_redirect" in src


# ---------- S0-8 / H1: email_verified was never read ----------

def _callback_source():
    import inspect
    return inspect.getsource(goauth.handle_google_callback)


def test_email_verified_is_actually_read():
    """The old code linked by email under a comment asserting "Google has verified this
    address" while never reading email_verified — only sub, email, given_name, family_name."""
    src = _callback_source()
    assert 'profile.get("email_verified")' in src


def test_the_check_gates_the_pending_signup_too():
    """It must refuse BEFORE the account lookup, not just before the link: gating only the
    link lets the same claim create the account first and wait for the victim to arrive."""
    src = _callback_source()
    verified_at = src.index('profile.get("email_verified")')
    assert verified_at < src.index("get_user_by_google_id")
    assert verified_at < src.index("get_user_by_email")


@pytest.mark.parametrize("value,accepted", [
    (True, True),
    ("true", True),       # tolerated: a strict `is True` would refuse EVERY sign-in if
    ("True", True),       # Google ever returned the field as a JSON string
    (False, False),
    ("false", False),
    (None, False),
    ("", False),
    (1, False),           # not a truthy check — only an explicit affirmative passes
])
def test_only_an_explicit_affirmative_passes(value, accepted):
    v = value
    passes = v is True or str(v).strip().lower() == "true"
    assert passes is accepted

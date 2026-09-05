"""SSRF containment — S1-4, findings M1 and M10.

The exploit: submit {"url": "http://10.0.0.5:8080/"} with NO token, then call
GET /api/opportunities/<id>/deadline?refresh=1 from a trial account. The deadline check
has no is_active filter by design, so it fetches the attacker's URL — robots.txt and every
guessed sitemap path — from inside Render's private network. The same rows are later
fetched by the FREE agents, which run on the operator's laptop.

url_guard is the one shared answer. Every case below is the URL an attacker would actually
send, not a synthetic one.
"""
import socket
import urllib.error
import urllib.request

import pytest

from wingman import url_guard
from wingman.url_guard import (BlockedURLError, safe_urlopen, url_block_reason,
                               url_is_public)


@pytest.fixture(autouse=True)
def _no_real_dns(monkeypatch):
    """conftest blocks sockets, not getaddrinfo. Resolve deterministically instead."""
    table = {
        "example.com": ["93.184.216.34"],
        "attacker.test": ["203.0.113.9"],          # public-looking, attacker-controlled
        "internal.example.com": ["10.0.0.5"],      # a public NAME for a private address
        "split.example.com": ["93.184.216.34", "10.0.0.5"],
        "metadata.example.com": ["169.254.169.254"],
        "v6.example.com": ["2001:4860:4860::8888"],
    }

    def _resolve(host, port, *a, **kw):
        if host not in table:
            raise socket.gaierror(-2, "Name or service not known")
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port))
                for ip in table[host]]

    monkeypatch.setattr(url_guard.socket, "getaddrinfo", _resolve)
    return table


# ---------------- literals ----------------

@pytest.mark.parametrize("url", [
    "http://10.0.0.5:8080/",                     # the report's own payload
    "http://127.0.0.1:8000/admin",               # the ops console
    "http://169.254.169.254/latest/meta-data/",  # cloud metadata
    "http://192.168.1.1/",
    "http://172.16.0.1/",
    "http://[::1]:8000/",
    "http://[fc00::1]/",
    "http://[fe80::1]/",
    "http://0.0.0.0/",
    "http://100.64.0.1/",                        # carrier-grade NAT
    "http://[::ffff:10.0.0.1]/",                 # IPv4-mapped IPv6
    "http://[64:ff9b::a00:1]/",                  # NAT64 embedding 10.0.0.1
    "http://[2002:a00:1::1]/",                   # 6to4 embedding 10.0.0.1
])
def test_private_literals_are_refused(url):
    assert url_is_public(url) is False


def test_a_public_literal_is_allowed():
    assert url_is_public("https://93.184.216.34/programs") is True


# ---------------- schemes ----------------

@pytest.mark.parametrize("url", ["file:///etc/passwd", "gopher://x/", "ftp://x.com/",
                                 "dict://127.0.0.1:11211/", "data:text/html,x", ""])
def test_only_http_and_https_are_fetchable(url):
    assert url_is_public(url) is False


def test_a_scheme_relative_or_bare_host_is_refused_rather_than_guessed():
    """Callers store absolute URLs. Guessing https for 'example.com/x' would be guessing
    what to fetch."""
    assert url_is_public("example.com/x") is False
    assert url_is_public("//example.com/x") is False


# ---------------- names ----------------

def test_a_public_name_resolving_to_a_private_address_is_refused():
    """The whole reason the check resolves rather than pattern-matching the host."""
    assert url_is_public("http://internal.example.com/") is False
    assert "10.0.0.5" in url_block_reason("http://internal.example.com/")


def test_every_answer_is_checked_not_just_the_first():
    """A host with one public A record and one private one is the oldest way around a
    check that only looks at [0]."""
    assert url_is_public("http://split.example.com/") is False


def test_a_public_name_is_allowed():
    assert url_is_public("https://example.com/summer-program") is True
    assert url_is_public("https://v6.example.com/") is True


def test_a_host_that_does_not_resolve_says_so_rather_than_being_allowed():
    reason = url_block_reason("https://nope.invalid/")
    assert reason and "does not resolve" in reason


def test_the_reason_distinguishes_the_two_failures():
    """'not a public address' and 'does not exist' are different problems for a reviewer."""
    assert "not a public address" in url_block_reason("http://10.0.0.5/")
    assert "does not resolve" in url_block_reason("https://nope.invalid/")


# ---------------- safe_urlopen ----------------

def test_safe_urlopen_refuses_before_opening_anything(monkeypatch):
    monkeypatch.setattr(url_guard, "safe_opener",
                        lambda: pytest.fail("must not reach the network"))
    with pytest.raises(BlockedURLError):
        safe_urlopen("http://10.0.0.5/robots.txt", timeout=1)


def test_safe_urlopen_checks_a_request_object_too(monkeypatch):
    monkeypatch.setattr(url_guard, "safe_opener",
                        lambda: pytest.fail("must not reach the network"))
    req = urllib.request.Request("http://169.254.169.254/", headers={"User-Agent": "x"})
    with pytest.raises(BlockedURLError):
        safe_urlopen(req, timeout=1)


def test_a_blocked_url_error_is_a_value_error():
    """Every fetch site in this repo already swallows fetch failures, and they must keep
    swallowing this one — a blocked address is 'could not fetch', not a new crash."""
    assert issubclass(BlockedURLError, ValueError)


def test_safe_urlopen_opens_a_public_url(monkeypatch):
    opened = {}

    class _Opener:
        def open(self, target, timeout=None, **kw):
            opened["target"] = target
            opened["timeout"] = timeout
            return "response"

    monkeypatch.setattr(url_guard, "safe_opener", lambda: _Opener())
    assert safe_urlopen("https://example.com/x", timeout=9) == "response"
    assert opened["timeout"] == 9


# ---------------- redirects ----------------

def _redirect_handler():
    return url_guard._PublicOnlyRedirectHandler()


def test_a_redirect_into_the_private_network_is_refused():
    """Without this the front-door check is decorative: the attacker controls a public
    host, so they just answer 302 and name any internal address they like."""
    handler = _redirect_handler()
    req = urllib.request.Request("https://attacker.test/")
    with pytest.raises(BlockedURLError):
        handler.redirect_request(req, None, 302, "Found", {}, "http://10.0.0.5:8080/")


def test_a_redirect_to_another_public_url_is_still_followed():
    handler = _redirect_handler()
    req = urllib.request.Request("https://attacker.test/")
    out = handler.redirect_request(req, None, 302, "Found", {},
                                   "https://example.com/real")
    assert out is not None
    assert out.full_url == "https://example.com/real"


def test_a_redirect_to_a_non_http_scheme_is_refused():
    handler = _redirect_handler()
    req = urllib.request.Request("https://attacker.test/")
    with pytest.raises(BlockedURLError):
        handler.redirect_request(req, None, 302, "Found", {}, "file:///etc/passwd")


# ---------------- the fetch sites actually use it ----------------

@pytest.mark.parametrize("module,attr", [
    ("wingman.sitemap_common", "default_fetch"),
    ("wingman.page_text", "_fetch_urllib"),
    ("wingman.url_repair", "_fetch"),
    ("wingman.mailing_list_common", "fetch_page"),
    ("wingman.url_validate", "check_url"),
])
def test_every_named_sink_goes_through_safe_urlopen(module, attr):
    """A regression guard with teeth: the finding is that these five reach the network with
    an attacker-supplied URL, so a future edit reintroducing a bare urlopen must fail here."""
    import importlib
    import inspect
    src = inspect.getsource(getattr(importlib.import_module(module), attr))
    assert "safe_urlopen(" in src, f"{module}.{attr} no longer uses safe_urlopen"
    assert "urllib.request.urlopen(" not in src, f"{module}.{attr} has a bare urlopen again"

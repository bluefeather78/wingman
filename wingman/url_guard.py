#!/usr/bin/env python3
"""One shared answer to "may this process fetch that URL?" — SSRF containment.

SECURITY_HARDENING_PLAN.md S1-4, findings M1 + M10.

**The exploit this closes.** `POST /api/user-submitted-opportunities` used to take a row
from anybody with no token at all, and `get_opportunity_for_deadline_check` deliberately has
no `is_active` filter — so any subscriber could then trigger a check against an
attacker-submitted row. That check runs `sitemap_common.default_fetch`, a plain `urlopen`
against `origin + "/robots.txt"` and the probed sitemap paths, with no address filter and
redirects followed. Submit `{"url": "http://10.0.0.5:8080/"}`, then
`GET /api/opportunities/<id>/deadline?refresh=1`, and the server probes inside Render's
private network. Bodies are not echoed back, but timing and the resulting status leak
reachability, and a redirect on the attacker's own host steers the follow-up GET anywhere.

Worse, the same rows are later fetched by the FREE agents (`agents/check_links.py`,
`wingman/url_repair.py`, `agents/find_mailing_lists.py`) — which run on the operator's
laptop. That turns a stranger's catalog submission into an SSRF against your home LAN.

**The rule.** A high-school opportunity lives on the public internet. Nothing in this repo
has any business fetching a loopback, RFC1918, link-local, carrier-grade-NAT, or otherwise
non-global address, so `url_is_public()` refuses every one of them rather than trying to
enumerate the interesting ones. `ipaddress`'s own `is_global` is the predicate: it is False
for 127/8, 10/8, 172.16/12, 192.168/16, 169.254/16 (which is where the cloud metadata
service lives, 169.254.169.254), 100.64/10, 0/8, 192.0.0/24, 198.18/15, 240/4, ::1, fc00::/7,
fe80::/10, :: and 2002::/16 embedding any of them.

**Redirects are re-checked, not trusted.** A public host answering `302 -> http://10.0.0.5/`
would otherwise walk the fetch straight past the front-door check, so `safe_urlopen()` runs
the same test on every hop.

**Residual, stated honestly: DNS rebinding.** We resolve the host to decide, then urllib
resolves it again to connect. A hostile resolver answering with a public address the first
time and a private one the second defeats the check. Closing that means connecting to the
address we validated and carrying the Host header ourselves, which means owning the
connection setup for every fetch site in the repo. Not done here. What IS closed is the
whole class of "the URL plainly says 10.0.0.5" and "the redirect says 10.0.0.5", which is
what an attacker can arrange without controlling a nameserver.
"""
import ipaddress
import socket
import urllib.error
import urllib.parse
import urllib.request

__all__ = ["BlockedURLError", "url_block_reason", "url_is_public", "safe_urlopen",
           "safe_opener"]

# The NAT64 well-known prefix embeds an IPv4 address in its low 32 bits, and `is_global`
# says True for the whole /96 — so 64:ff9b::a00:1 would sail through while 10.0.0.1 does
# not. Unwrap it and judge the address it actually reaches.
_NAT64_WELL_KNOWN = ipaddress.ip_network("64:ff9b::/96")

ALLOWED_SCHEMES = ("http", "https")

# Resolution has to be bounded: a hostile hostname pointed at a black-holed nameserver
# would otherwise hang the request for the resolver's own timeout, which on Linux is 5s x
# 2 attempts x every nameserver in resolv.conf.
RESOLVE_TIMEOUT = 5


class BlockedURLError(ValueError):
    """Raised by safe_urlopen when a URL — or a redirect target — is not publicly routable."""


def _address_reason(raw):
    """Why this IP may not be fetched, or None if it is fine."""
    try:
        addr = ipaddress.ip_address(raw)
    except ValueError:
        return f"{raw!r} is not an IP address"

    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        addr = mapped
    elif addr.version == 6 and addr in _NAT64_WELL_KNOWN:
        addr = ipaddress.ip_address(int(addr) & 0xFFFFFFFF)

    if not addr.is_global:
        return f"{addr.compressed} is not a public address"
    return None


def _host_addresses(host, port):
    """Every address `host` resolves to, as strings. Raises socket.gaierror if it does not.

    EVERY answer is checked, not just the first: a host with one public A record and one
    private one is the oldest way around a check that only looks at `[0]`.
    """
    previous = socket.getdefaulttimeout()
    socket.setdefaulttimeout(RESOLVE_TIMEOUT)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    finally:
        socket.setdefaulttimeout(previous)
    return [info[4][0] for info in infos]


def url_block_reason(url):
    """Why this URL may not be fetched, or None if it may.

    Returns a reason string rather than a bare False so callers can log and surface
    something a reviewer can act on — "not a public address" is a very different problem
    from "that host does not exist".
    """
    text = str(url or "").strip()
    if not text:
        return "no URL given"

    try:
        parts = urllib.parse.urlsplit(text)
    except ValueError as e:
        return f"unparseable URL ({e})"

    scheme = (parts.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        # file://, gopher://, ftp:// and friends. A missing scheme lands here too, which is
        # correct: callers store absolute URLs, and "example.com/x" parses with no host at
        # all, so guessing https for it would be guessing what to fetch.
        return f"scheme {scheme or '(none)'!r} is not http or https"

    host = parts.hostname
    if not host:
        return "URL has no host"

    port = parts.port or (443 if scheme == "https" else 80)

    # An IP literal never needs resolving, and must not be given the benefit of a DNS
    # answer — http://[::ffff:10.0.0.1]/ is a literal, not a name.
    literal = _address_reason(host)
    if literal is None:
        return None                       # a public IP literal
    if "is not an IP address" not in literal:
        return literal                    # a literal, and a blocked one

    try:
        addresses = _host_addresses(host, port)
    except (socket.gaierror, socket.timeout, UnicodeError, OSError) as e:
        return f"host {host!r} does not resolve ({e})"
    if not addresses:
        return f"host {host!r} does not resolve"

    for raw in addresses:
        reason = _address_reason(raw)
        if reason:
            return f"host {host!r} resolves to {reason}"
    return None


def url_is_public(url):
    """True if this URL is http(s) and every address it resolves to is publicly routable."""
    return url_block_reason(url) is None


class _PublicOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Applies url_block_reason to every redirect target.

    Without this the front-door check is decorative: an attacker controls a public host,
    so they can simply answer 302 and name any internal address they like.
    """
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        reason = url_block_reason(newurl)
        if reason:
            raise BlockedURLError(f"refusing redirect to {newurl!r}: {reason}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def safe_opener():
    """A fresh opener that re-checks every redirect hop.

    Deliberately built per call rather than cached at import: `urllib.request` openers hold
    a cookie-less but otherwise stateful handler chain, and the agents run this from several
    threads.
    """
    return urllib.request.build_opener(_PublicOnlyRedirectHandler)


def safe_urlopen(url_or_request, timeout=None, **kwargs):
    """`urlopen`, with the public-address check on the initial URL and on every redirect.

    Raises BlockedURLError (a ValueError) when the check fails, so callers that already
    swallow fetch errors — which is most of them, since a missing page is a normal outcome
    here — degrade to "could not fetch" rather than gaining a new failure mode.
    """
    target = (url_or_request.full_url
              if isinstance(url_or_request, urllib.request.Request)
              else str(url_or_request))
    reason = url_block_reason(target)
    if reason:
        raise BlockedURLError(f"refusing to fetch {target!r}: {reason}")
    return safe_opener().open(url_or_request, timeout=timeout, **kwargs)

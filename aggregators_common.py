#!/usr/bin/env python3
"""Shared read-side of the trusted-domain allowlist (P5) — the ONE place that turns a URL
into a trust tier for BOTH the deadline escalation loop (rung 4) and the task aggregator
tier (P6).

Stdlib-only, repo root, like the other shared offline libs (supabase_common, url_dedupe):
`app/`, `ops/` and the root-level agents (`check_deadlines.py`) all import it.

VERIFICATION != TRUST (architecture decision 5). Code elsewhere proves *this source said
this*; this module answers *how authoritative is the source* — an operator policy read from
the `trusted_aggregators` table:

    'trusted'  a domain (or a subdomain of one) the operator approved  -> tier 2
    'blocked'  a domain the operator declined                          -> dropped
    'pending'  any other domain, INCLUDING when the table is absent     -> withheld/parked

Pending is the safe default: an unknown domain never ships to a student. So a missing table,
an unreachable Supabase, or a domain nobody has ruled on all collapse to the same
withhold-by-default behaviour — degrade-not-break, exactly what DEADLINE_AND_TASK_PLAN.md §5
asks for.
"""
import time
import urllib.error
from urllib.parse import urlparse

from supabase_common import supabase_get

TABLE = "trusted_aggregators"
VALID_STATUSES = ("trusted", "blocked")

# Process-level cache. The deadline path may call the policy on every hard row and the task
# pass on every row, seconds apart; the allowlist changes only when an operator clicks. A
# short TTL keeps an approval visibly live (a Sources-tab write also invalidates explicitly,
# below) while collapsing a burst of checks to one Supabase read. Mirrors app.core's
# _runs_cache shape.
_CACHE = {"at": 0.0, "policy": None, "key": None}
CACHE_TTL_SECS = 300


def normalize_domain(url_or_domain):
    """A bare, comparable domain: lowercased, scheme/path/port/query stripped, leading
    'www.' removed. Accepts a full URL or a bare host. Empty string when nothing usable is
    present (a relative path, a mailto:, junk) — the callers treat '' as 'pending', so an
    unparseable source is withheld, never trusted.

    NOT a registrable-domain (public-suffix) reduction — that needs a suffix list this repo
    does not carry, and over-reducing would make 'foo.github.io' collapse to 'github.io' and
    trust every GitHub Pages site once one was approved. Subdomain matching is handled in
    AggregatorPolicy.classify instead, by suffix, which is the safe direction.
    """
    if not url_or_domain:
        return ""
    s = str(url_or_domain).strip()
    if not s:
        return ""
    try:
        parsed = urlparse(s)
    except ValueError:
        return ""
    if parsed.scheme in ("http", "https") or ("//" in s and parsed.netloc):
        host = parsed.netloc or ""
    elif parsed.scheme:
        # A non-web scheme (mailto:, tel:, ftp:, javascript:) is not a page source — return
        # "" so it classifies as pending and is never trusted, rather than parsing the part
        # after the scheme as a host.
        return ""
    else:
        # A bare "Example.com/path" has no scheme and puts the host in .path; re-parse with a
        # leading "//" so urlparse finds the netloc.
        try:
            host = urlparse("//" + s).netloc or ""
        except ValueError:
            return ""
    host = host.split("@")[-1]          # drop any user:pass@
    host = host.split(":")[0]           # drop :port
    host = host.strip().strip(".").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def domain_matches(url_or_domain, allowed_domains):
    """True when url_or_domain is one of allowed_domains OR a subdomain of one. The single
    suffix rule shared by AggregatorPolicy.classify and the deadline rung-4 source filter, so
    "does this page belong to a trusted domain" is decided in exactly one place. Never the
    reverse — approving a subdomain does not trust its parent."""
    domain = normalize_domain(url_or_domain)
    if not domain:
        return False
    return any(domain == a or domain.endswith("." + a) for a in allowed_domains)


class AggregatorPolicy:
    """An immutable snapshot of the allowlist. `present` is False when the table could not be
    read (absent / unreachable) — the classification is identical (everything pending), but
    the console uses `present` to show the setup step rather than an empty allowlist.
    """

    __slots__ = ("trusted", "blocked", "present", "error")

    def __init__(self, statuses=None, present=True, error=None):
        statuses = statuses or {}
        self.trusted = frozenset(d for d, s in statuses.items() if s == "trusted")
        self.blocked = frozenset(d for d, s in statuses.items() if s == "blocked")
        self.present = present
        self.error = error

    def classify(self, url_or_domain):
        """'trusted' | 'blocked' | 'pending'. Blocked wins over trusted, so blocking a domain
        overrides an accidental parent-trust."""
        if not normalize_domain(url_or_domain):
            return "pending"
        if domain_matches(url_or_domain, self.blocked):
            return "blocked"
        if domain_matches(url_or_domain, self.trusted):
            return "trusted"
        return "pending"

    def is_trusted(self, url_or_domain):
        return self.classify(url_or_domain) == "trusted"

    def trusted_domains(self):
        """Sorted list, for injecting into the deadline rung-4 search focus."""
        return sorted(self.trusted)


def load_aggregator_policy(supabase_url, service_key):
    """Read the allowlist and return an AggregatorPolicy. NEVER raises — a missing table, an
    unreachable Supabase, or missing credentials all return an empty policy (everything
    pending), the table-absent case additionally flagged present=False so the console can tell
    "nothing approved yet" from "the table isn't there".
    """
    if not supabase_url or not service_key:
        return AggregatorPolicy(present=False, error="Supabase not configured")
    try:
        rows = supabase_get(supabase_url.rstrip("/"), TABLE,
                            {"select": "domain,status"}, service_key)
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        # 404 (table not created) or PostgREST's undefined-table code -> present=False so the
        # console shows the setup step. Any other HTTP error degrades the same way (withhold
        # everything) but is surfaced as an error rather than a clean "not set up".
        if e.code == 404 or "42P01" in body or "does not exist" in body:
            return AggregatorPolicy(present=False, error=None)
        return AggregatorPolicy(present=False, error=f"HTTP {e.code}: {body[:160]}")
    except Exception as e:
        return AggregatorPolicy(present=False, error=str(e)[:160])

    statuses = {}
    for row in rows or []:
        domain = normalize_domain(row.get("domain"))
        status = row.get("status")
        if domain and status in VALID_STATUSES:
            statuses[domain] = status
    return AggregatorPolicy(statuses, present=True)


def get_policy(supabase_url, service_key, force=False, ttl=CACHE_TTL_SECS):
    """Cached load_aggregator_policy. The read side both features actually call on a hot path.
    A Sources-tab write should follow with invalidate_policy_cache() so an approval is live at
    once; otherwise it takes at most `ttl` seconds."""
    now = time.monotonic()
    key = supabase_url
    cached = _CACHE["policy"]
    if (not force and cached is not None and _CACHE["key"] == key
            and (now - _CACHE["at"]) < ttl):
        return cached
    policy = load_aggregator_policy(supabase_url, service_key)
    # Only cache a real read. A transient error (present=False, error set) should not pin the
    # allowlist off for the whole TTL — the next call re-tries. A clean "table absent"
    # (present=False, error=None) IS cached: it is a stable state, not a blip.
    if policy.present or policy.error is None:
        _CACHE.update(at=now, policy=policy, key=key)
    return policy


def invalidate_policy_cache():
    _CACHE["at"] = 0.0
    _CACHE["policy"] = None

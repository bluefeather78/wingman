#!/usr/bin/env python3
"""Sitemap-first page discovery (G-D1, phases D0-D5; DEADLINE_AND_TASK_PLAN.md §4/§9).

THE PRINCIPLE: enumerate a site's OWN pages, then choose — never guess page NAMES. The task
capture and the deadline ladder both hunt for pages named How-to-Apply / FAQ / Requirements /
Key-Dates via `web_search`, so a program that files its steps under "The Program" / "How It
Works" / "Participant Timeline" is missed and the pipeline settles for the homepage (live case
ec18244, Congressional Award: `/the-program/` carries a full verifiable step list `web_search`
never reached). Reading the site's real page list removes the guess.

FREE, stdlib-only, and injectable-fetch so the whole brain is unit-testable offline with no
network (fixtures in tests/fixtures/sitemaps). Public surface:

    discover_candidate_pages(opp, fetch=default_fetch) -> list[Candidate{url, score, lastmod}]

returns `[]` cleanly whenever nothing usable is found — that empty list is the FALLBACK TRIGGER:
the caller drops back to today's `web_search` discovery, unchanged, so this can only ADD recall,
never regress a working row. ~63% of catalog hosts expose a usable sitemap (measured 2026-08-27);
the rest fall through to `web_search`.

Pipeline: LOCATE (robots.txt Sitemap: -> probe common paths) -> PARSE (sitemapindex recursion
ONE level, gzip, CDATA, <lastmod>; hard caps so a 50k-URL host cannot hang) -> SCOPE (single-
program host keeps all; multi-program host filters to the stored URL's path prefix OR the
opportunity's name tokens) -> RANK (slug scorer: positive apply/program tokens, negative chrome
tokens, shallow-depth + name-overlap + recent-lastmod bonuses).
"""
import gzip
import re
import urllib.request
from urllib.parse import urljoin, urlparse

import aggregators_common

# ---------- bounds (a hostile/huge host must never hang or blow memory) ----------
DEFAULT_TIMEOUT = 8.0
MAX_BYTES = 5_000_000          # K — per-file read cap
MAX_CHILD_SITEMAPS = 25        # N — children of a <sitemapindex> we will fetch
MAX_TOTAL_URLS = 20_000        # M — total <url> entries we will hold
TOP_N = 5                      # candidates returned by default
# A host whose full page list is at or under this many URLs is treated as a single-program
# site (keep everything); above it, scope to the program. congressionalaward.org is 271 pages
# (kept whole); an nyu.edu-scale host is filtered. See scope().
SINGLE_PROGRAM_MAX_URLS = 400

_COMMON_SITEMAP_PATHS = ("/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml")

# Slug scorer. Stems (matched as substrings of the lowercased path) so plural/verb forms are
# caught without a word list: "appl" -> apply/application/applicant; "regist" -> register/
# registration. Positive = pages that carry apply-steps or dates; negative = site chrome.
_POS = ("program", "appl", "regist", "how-to", "howto", "prospective", "participant",
        "eligib", "requirement", "deadline", "dates", "guidelin", "admission", "admiss",
        "get-started", "getstarted", "steps", "timeline", "overview", "faq", "enroll",
        "join", "prepare", "checklist", "instructions")
_NEG = ("leadership", "donor", "giving", "sponsor", "news", "blog", "press", "event",
        "summit", "alumni", "staff", "board", "photos", "gallery", "podcast", "job",
        "career", "contact", "privacy", "terms", "cookie", "login", "account", "cart",
        "shop", "store", "history", "mission", "about-us")

# Generic words dropped when deriving name tokens — they match half a catalog and cannot scope.
_NAME_STOP = {"the", "a", "an", "of", "and", "for", "to", "in", "on", "at", "program",
              "programs", "summer", "high", "school", "students", "student", "national",
              "international", "academy", "institute", "center", "centre", "project",
              "competition", "internship", "research", "scholars", "scholarship", "award",
              "awards", "young", "youth", "college", "university", "annual"}


class Candidate:
    __slots__ = ("url", "score", "lastmod")

    def __init__(self, url, score, lastmod=None):
        self.url = url
        self.score = score
        self.lastmod = lastmod

    def __repr__(self):
        return f"Candidate({self.url!r}, score={self.score:.1f}, lastmod={self.lastmod!r})"


# ---------- fetch (the one place that touches the network; injectable) ----------

def default_fetch(url, timeout=DEFAULT_TIMEOUT):
    """Return the raw body bytes of `url` (gunzipped if it is a .gz / gzip-magic file), or
    raise on any error. Bounded read. A student's browser would fetch these same public pages;
    we send a plain UA and never request compression, so only explicit .gz needs gunzip."""
    req = urllib.request.Request(url, headers={"User-Agent": "WingmanBot/1.0 (+sitemap)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raw = raw[:MAX_BYTES]
    if url.lower().endswith(".gz") or raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(raw)
        except OSError:
            pass
    return raw


def _safe_fetch(fetch, url):
    """fetch(url) -> bytes | None, swallowing every error (a missing/blocked/garbage sitemap is
    a fallback, not a failure)."""
    try:
        body = fetch(url)
    except Exception:
        return None
    if body is None:
        return None
    if isinstance(body, str):
        body = body.encode("utf-8", "replace")
    # Gunzip by magic bytes here (not only in default_fetch) so a .gz sitemap is handled
    # regardless of which fetch implementation the caller injected.
    if body[:2] == b"\x1f\x8b":
        try:
            body = gzip.decompress(body)
        except OSError:
            pass
    return body


# ---------- locate ----------

def _origin(url):
    """scheme://netloc of the stored URL (keeps the real host, incl. www / subdomain — robots
    and sitemaps are per-host)."""
    p = urlparse(url if "//" in str(url or "") else "//" + str(url or ""), scheme="https")
    if not p.netloc:
        return ""
    scheme = p.scheme if p.scheme in ("http", "https") else "https"
    return f"{scheme}://{p.netloc}"


_ROBOTS_SITEMAP_RE = re.compile(rb"(?im)^\s*sitemap:\s*(\S+)")


def sitemap_urls_from_robots(origin, fetch):
    """Every `Sitemap:` line in the host's robots.txt (absolute or resolved-relative)."""
    body = _safe_fetch(fetch, origin + "/robots.txt")
    if not body:
        return []
    out = []
    for m in _ROBOTS_SITEMAP_RE.finditer(body):
        loc = m.group(1).decode("utf-8", "replace").strip()
        out.append(urljoin(origin + "/", loc))
    return list(dict.fromkeys(out))


def robots_disallows(origin, fetch):
    """Disallow path prefixes under `User-agent: *` — a small courtesy filter on candidates.
    We only READ pages a student's browser would; an explicit Disallow is honoured."""
    body = _safe_fetch(fetch, origin + "/robots.txt")
    if not body:
        return []
    dis, applies = [], False
    for line in body.decode("utf-8", "replace").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith("user-agent:"):
            applies = line.split(":", 1)[1].strip() == "*"
        elif applies and low.startswith("disallow:"):
            path = line.split(":", 1)[1].strip()
            if path:
                dis.append(path)
    return dis


def locate_sitemaps(url, fetch):
    """Sitemap URLs for the stored URL's host: robots.txt first, else probe the common paths
    and keep whichever actually parse as a sitemap. [] when the host publishes none."""
    origin = _origin(url)
    if not origin:
        return []
    found = sitemap_urls_from_robots(origin, fetch)
    if found:
        return found
    out = []
    for path in _COMMON_SITEMAP_PATHS:
        cand = origin + path
        body = _safe_fetch(fetch, cand)
        if body and _looks_like_sitemap(body):
            out.append(cand)
    return out


# ---------- parse ----------

def _looks_like_sitemap(body):
    head = body[:4000].lower()
    return b"<sitemapindex" in head or b"<urlset" in head


_LOC_RE = re.compile(r"<loc>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</loc>",
                     re.IGNORECASE | re.DOTALL)


def _iter_loc_lastmod(xml_text):
    """Yield (loc, lastmod|None) for each <url>/<sitemap> entry, in document order. Regex, not
    a DOM parse: sitemaps are flat and often megabytes, and we only need <loc>/<lastmod>."""
    # Split on entry boundaries so a <lastmod> is paired with the <loc> in the same block.
    for chunk in re.split(r"</(?:url|sitemap)>", xml_text, flags=re.IGNORECASE):
        mloc = _LOC_RE.search(chunk)
        if not mloc:
            continue
        loc = mloc.group(1).strip()
        if not loc:
            continue
        mlm = re.search(r"<lastmod>\s*(.*?)\s*</lastmod>", chunk, re.IGNORECASE | re.DOTALL)
        yield loc, (mlm.group(1).strip() if mlm else None)


def parse_sitemap(body):
    """(kind, entries) where kind is 'index' or 'urlset' and entries is [(loc, lastmod), ...].
    Empty entries on anything unparseable."""
    if not body:
        return "urlset", []
    text = body.decode("utf-8", "replace")
    kind = "index" if re.search(r"<sitemapindex", text, re.IGNORECASE) else "urlset"
    return kind, list(_iter_loc_lastmod(text))


def collect_urls(sitemap_urls, fetch):
    """Fetch each sitemap, parse it, recurse ONE level into a <sitemapindex>'s children.
    Returns [(loc, lastmod)] deduped by loc, bounded by MAX_CHILD_SITEMAPS / MAX_TOTAL_URLS."""
    seen, out = set(), []
    child_budget = MAX_CHILD_SITEMAPS

    def _add(loc, lastmod):
        if loc in seen:
            return False
        seen.add(loc)
        out.append((loc, lastmod))
        return len(out) < MAX_TOTAL_URLS

    for sm in sitemap_urls[:MAX_CHILD_SITEMAPS]:
        body = _safe_fetch(fetch, sm)
        if not body:
            continue
        kind, entries = parse_sitemap(body)
        if kind == "index":
            for child_loc, _lm in entries:
                if child_budget <= 0 or len(out) >= MAX_TOTAL_URLS:
                    break
                child_budget -= 1
                cbody = _safe_fetch(fetch, child_loc)
                if not cbody:
                    continue
                _, curls = parse_sitemap(cbody)   # ONE level only — no deeper recursion
                for loc, lastmod in curls:
                    if not _add(loc, lastmod):
                        break
        else:
            for loc, lastmod in entries:
                if not _add(loc, lastmod):
                    break
        if len(out) >= MAX_TOTAL_URLS:
            break
    return out


# ---------- scope ----------

def name_tokens(opp):
    """Distinctive lowercased tokens from the opportunity's name (and org), generic words
    dropped — the same idea url_repair uses to separate a program's identity from its category."""
    text = f"{opp.get('name') or ''} {opp.get('org') or ''}".lower()
    toks = re.findall(r"[a-z0-9]+", text)
    return {t for t in toks if len(t) > 2 and t not in _NAME_STOP}


def _path_prefix(url):
    """First path segment of the stored URL, e.g. '/programs' — the family a multi-program host
    files this opportunity under. '' when the stored URL is a bare homepage."""
    segs = [s for s in urlparse("//" + url if "//" not in str(url or "") else url).path.split("/") if s]
    return "/" + segs[0] if segs else ""


def scope(opp, entries):
    """A single-program host (few pages) keeps everything; a big multi-program host is filtered
    to the stored URL's path prefix OR the opportunity's name tokens, so ranking is not run over
    a 50k-page tree. Falls back to keeping all if filtering would empty the set (better to rank
    broadly than to discover nothing)."""
    if len(entries) <= SINGLE_PROGRAM_MAX_URLS:
        return entries
    prefix = _path_prefix(opp.get("url") or "")
    toks = name_tokens(opp)
    kept = []
    for loc, lastmod in entries:
        path = urlparse(loc).path.lower()
        slug_words = set(re.findall(r"[a-z0-9]+", path))
        if (prefix and path.startswith(prefix.lower())) or (toks & slug_words):
            kept.append((loc, lastmod))
    return kept or entries


# ---------- rank ----------

def score_slug(url, toks):
    """Deterministic slug score: positive apply/program stems, negative chrome stems, a
    shallow-depth bonus, and a name-token-overlap bonus. lastmod recency is added by rank()."""
    path = urlparse(url).path.lower()
    score = 0.0
    for stem in _POS:
        if stem in path:
            score += 2.0
    for stem in _NEG:
        if stem in path:
            score -= 3.0
    depth = len([s for s in path.split("/") if s])
    score += max(0.0, 3.0 - depth)          # shallow pages preferred
    slug_words = set(re.findall(r"[a-z0-9]+", path))
    score += 1.5 * len(toks & slug_words)   # this program's own name in the slug
    return score


_LASTMOD_YEAR_RE = re.compile(r"(20\d{2})")


def rank(opp, entries, top_n=TOP_N):
    """entries [(loc, lastmod)] -> top_n Candidates, best first. A recent <lastmod> is a small
    tie-breaker (a page updated this year is likelier the live cycle's)."""
    toks = name_tokens(opp)
    cands = []
    for loc, lastmod in entries:
        s = score_slug(loc, toks)
        m = _LASTMOD_YEAR_RE.search(lastmod or "")
        if m and int(m.group(1)) >= 2025:
            s += 0.5
        cands.append(Candidate(loc, s, lastmod))
    # Stable sort by score desc, then shallow path, then url for determinism.
    cands.sort(key=lambda c: (-c.score, urlparse(c.url).path.count("/"), c.url))
    return cands[:top_n]


# ---------- public entry point ----------

def discover_candidate_pages(opp, fetch=None, top_n=TOP_N):
    """The whole brain. Locate -> collect -> scope -> rank. Returns up to `top_n` Candidates,
    best first, or `[]` when the host has no usable sitemap (the caller's fallback trigger).
    Never raises: any fetch/parse error degrades to `[]`."""
    if fetch is None:
        fetch = default_fetch
    url = opp.get("url") or ""
    if not url:
        return []
    try:
        sitemap_urls = locate_sitemaps(url, fetch)
        if not sitemap_urls:
            return []
        entries = collect_urls(sitemap_urls, fetch)
        if not entries:
            return []
        origin = _origin(url)
        disallowed = tuple(robots_disallows(origin, fetch)) if origin else ()
        if disallowed:
            entries = [(loc, lm) for loc, lm in entries
                       if not urlparse(loc).path.startswith(disallowed)]
        entries = scope(opp, entries)
        if not entries:
            return []
        return rank(opp, entries, top_n)
    except Exception:
        return []

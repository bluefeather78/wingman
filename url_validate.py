#!/usr/bin/env python3
"""Resolving Gemini grounding-redirect URIs, and checking whether a URL is actually live.

Both halves are FREE — ordinary HTTP, no API calls, no keys. They exist because of one
measured failure (2026-08-23): when Gemini answers without searching, it writes URLs from
memory, and they come back with the right host and a path off by one segment. In
`scrape_review_national_20260820.json`, 30 of 116 URLs were hard 404s (26%) and every single
one was a constructed deep path — never a bare domain. Head to head, 4/4 model-typed URLs
404'd where 4/4 grounding-resolved URLs returned 200.

resolve_grounding_chunks()
    Turns `groundingMetadata.groundingChunks[].web.uri` — which is always a
    `vertexaisearch.cloud.google.com/grounding-api-redirect/…` link, never the source URL —
    into the real destination, by following ONE redirect hop. `web.title` is only a bare
    domain string ("nih.gov") and `web.domain` does not exist on `v1beta/generateContent`,
    so this hop is the only way to learn the actual page the model read.

check_urls()
    Concurrent liveness check. Distinguishes DEAD (404/410, a malformed URL, or a hostname
    that does not resolve — the page genuinely is not there) from UNVERIFIED (403/429,
    TLS failures, timeouts — the site refused or defeated our client but is almost
    certainly real). That distinction is the whole point: 403 is extremely common on
    university and .org sites, and treating it as death would throw away good rows.
    Measured on the live catalog, `wingman-seed` rows return 403 at ~9%.

    Measured over all 1374 active rows on 2026-08-23: 1029 live, 137 dead (10.0%, all
    404/410), 208 unverified. Re-checking the dead set minutes later moved 135 of 137 not
    at all — a 404 here is highly reproducible, which is what makes check_links.py's
    two-pass confirmation cheap enough to be worth doing and honest enough to act on.

Nothing here ever decides to discard a row. Callers turn these results into review flags —
see scrape_opportunities.py's FLAG_* constants and SCRAPER_PLAN.md's "discard almost
nothing, explain everything" rule.
"""
import concurrent.futures
import re
import socket
import urllib.error
import urllib.parse
import urllib.request

# url_dedupe owns registrable_domain() and the rest of this repo's URL-matching rules.
# It imports nothing from here, so this direction is safe; keep it that way.
import url_dedupe

# A browser UA is required, not cosmetic: many university sites 403 the default
# urllib agent, which would misreport live pages as unverified.
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

GROUNDING_REDIRECT_HOST = "vertexaisearch.cloud.google.com"

DEAD_CODES = {404, 410}          # the page is not there
BLOCKED_CODES = {401, 403, 429}  # the site refused us, not evidence of absence

# A hostname that does not resolve is evidence of absence in the same sense a 404 is —
# the host was decommissioned, not merely unreachable. Measured over the live catalog on
# 2026-08-23, all 8 rows failing this way pointed at retired university subdomains
# (`smysp.stanford.edu`, `bri.ucla.edu`, `globalyouthprogram.wharton.upenn.edu`); every
# one still fails to resolve on a second pass minutes later.
#
# It is kept SEPARATE from every other socket-level failure on purpose. Of the 54 rows
# that raised URLError in that same pass, 41 were TLS problems (certificate chains this
# Python's trust store rejects, `TLSV1_UNRECOGNIZED_NAME`, handshake failures) and the
# rest were timeouts and connection resets. Those are OUR client failing, not the page
# being gone — a student's browser carries a different root store and usually loads them
# fine — so they must stay UNVERIFIED. Reading "the connection failed" as "the page is
# gone" would have deactivated ~41 working catalog rows.
DNS_FAILURE = "NXDOMAIN"

MAX_WORKERS = 12
DEFAULT_TIMEOUT = 15

# Status classifications returned by check_urls().
LIVE = "live"
DEAD = "dead"
UNVERIFIED = "unverified"

# Hosts that write ABOUT programs but are never a program's OWN page — SEO listicles, blogs,
# video, forums, encyclopedias. Measured round 1 (2026-08-23): every URL on these hosts that
# reached review was rejected ~100% of the time. A mill URL can never be the stored URL; the
# real page it links to is recovered by url_repair.extract_primary_link instead. Compared on
# the registrable domain, so www./blog./old. subdomains all match.
CONTENT_MILL_HOSTS = {
    "lumiere-education.com", "admissionsight.com", "scholarships360.org", "nshss.org",
    "borderless.so", "aralia.com", "indigoresearch.org", "ladderinternships.com",
    "opportunitiesforyouth.org", "youtube.com", "youtu.be", "reddit.com",
    "wikipedia.org", "lithub.com",
}
# Path-aware: a host that is a mill ONLY under a sub-path. immerse.education is a legitimate
# provider at /summer-schools/ but a content mill at /knowledge-base/, so a bare host match
# would wrongly reject its real program pages.
CONTENT_MILL_PATH_PREFIXES = {
    "immerse.education": ("/knowledge-base/",),
}


def is_content_mill(url):
    """True if the URL is a page ABOUT programs (listicle/blog/video/forum), never one's own.

    Registrable-domain match against CONTENT_MILL_HOSTS, plus the path-aware entries for hosts
    that are a mill only under a sub-path. A mill URL must never be stored as a row's URL.
    """
    if not url:
        return False
    try:
        parts = urllib.parse.urlsplit(url if "//" in url else "//" + url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower().strip(".")
    if not host:
        return False
    reg = url_dedupe.registrable_domain(host)
    for mill_host, prefixes in CONTENT_MILL_PATH_PREFIXES.items():
        if reg == mill_host or host == mill_host:
            return (parts.path or "/").startswith(prefixes)
    return reg in CONTENT_MILL_HOSTS or host in CONTENT_MILL_HOSTS


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Turn a 3xx into an HTTPError so the Location header can be read instead of followed."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, newurl, headers, fp)


_no_redirect_opener = urllib.request.build_opener(_NoRedirect)


def is_grounding_redirect(url):
    try:
        return urllib.parse.urlsplit(url or "").hostname == GROUNDING_REDIRECT_HOST
    except ValueError:
        return False


def _resolve_one(uri, timeout):
    """Follow a single redirect hop and return the destination, or None."""
    try:
        _no_redirect_opener.open(
            urllib.request.Request(uri, headers={"User-Agent": USER_AGENT}), timeout=timeout)
    except urllib.error.HTTPError as e:
        # _NoRedirect stuffs the Location value into `msg` for 3xx responses.
        if 300 <= e.code < 400 and isinstance(e.msg, str) and e.msg.startswith("http"):
            return e.msg
    except Exception:
        pass
    return None


def resolve_grounding_chunks(grounding, timeout=DEFAULT_TIMEOUT):
    """Resolve every groundingChunk redirect to its real URL.

    `grounding` is the `groundingMetadata` dict from call_gemini(..., return_grounding=True).

    Returns a list parallel to groundingChunks, each entry:
        {"index": int, "domain": str, "redirect_uri": str, "url": str|None}
    `url` is None when the hop failed; callers must treat that as "no source URL known"
    rather than falling back to the redirect (which is useless to a student and expires).
    """
    chunks = (grounding or {}).get("groundingChunks") or []
    jobs = []
    for i, chunk in enumerate(chunks):
        web = (chunk or {}).get("web") or {}
        jobs.append({
            "index": i,
            # `title` really is just the bare domain on this endpoint, despite the name.
            "domain": (web.get("domain") or web.get("title") or "").strip().lower(),
            "redirect_uri": web.get("uri") or "",
            "url": None,
        })
    if not jobs:
        return []

    def work(job):
        if job["redirect_uri"]:
            job["url"] = _resolve_one(job["redirect_uri"], timeout)
        return job

    with concurrent.futures.ThreadPoolExecutor(min(MAX_WORKERS, len(jobs))) as pool:
        return list(pool.map(work, jobs))


def support_urls_by_span(grounding, resolved):
    """Map each groundingSupport's answer span to the resolved URLs backing it.

    Returns [{"start": int, "end": int, "text": str, "urls": [str, ...]}, ...].

    This is what makes per-opportunity attribution possible: without it you can only say
    "these seven pages were consulted for this whole answer", which is not enough to decide
    which URL belongs to which opportunity in a list of eight.
    """
    by_index = {r["index"]: r for r in (resolved or [])}
    spans = []
    for support in (grounding or {}).get("groundingSupports") or []:
        segment = support.get("segment") or {}
        urls = []
        for idx in support.get("groundingChunkIndices") or []:
            entry = by_index.get(idx)
            if entry and entry.get("url") and entry["url"] not in urls:
                urls.append(entry["url"])
        if not urls:
            continue
        spans.append({
            "start": segment.get("startIndex") or 0,
            "end": segment.get("endIndex") or 0,
            "text": segment.get("text") or "",
            "urls": urls,
        })
    return spans


def _is_dns_failure(exc):
    """Did this exception mean 'no such host', as opposed to 'could not reach the host'?

    socket.gaierror is the only signal that separates the two, and it arrives wrapped in a
    URLError's `reason`. Everything else — TLS, timeouts, resets — is a transport failure
    and must not be read as absence. See DNS_FAILURE above for the measurement.
    """
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, socket.gaierror):
            return True
        exc = getattr(exc, "reason", None) if isinstance(exc, urllib.error.URLError) else None
    return False


def check_url(url, timeout=DEFAULT_TIMEOUT):
    """One URL -> {"url", "status", "code", "final_url"}.

    `status` is LIVE / DEAD / UNVERIFIED. A GET is used rather than a HEAD because a
    meaningful number of sites answer HEAD with 405 or a misleading code.

    `code` is an int for an HTTP answer and a short string otherwise ("malformed",
    DNS_FAILURE, or the exception class name). Callers store it verbatim, so keep it
    short and stable — check_links.py writes it to opportunities.link_status_code and the
    admin console renders it.
    """
    result = {"url": url, "status": UNVERIFIED, "code": None, "final_url": None}
    if not url or not str(url).lower().startswith(("http://", "https://")):
        result["status"] = DEAD
        result["code"] = "malformed"
        return result
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": USER_AGENT}),
                timeout=timeout) as resp:
            result["code"] = resp.status
            result["final_url"] = resp.geturl()
            result["status"] = LIVE if resp.status < 400 else UNVERIFIED
    except urllib.error.HTTPError as e:
        result["code"] = e.code
        result["status"] = DEAD if e.code in DEAD_CODES else UNVERIFIED
    except Exception as e:
        if _is_dns_failure(e):
            result["code"] = DNS_FAILURE
            result["status"] = DEAD
        else:
            result["code"] = type(e).__name__
            result["status"] = UNVERIFIED
    return result


def check_urls(urls, timeout=DEFAULT_TIMEOUT):
    """Check many URLs concurrently. Returns {url: result}. Duplicates checked once."""
    unique = [u for u in dict.fromkeys(urls) if u]
    if not unique:
        return {}
    with concurrent.futures.ThreadPoolExecutor(min(MAX_WORKERS, len(unique))) as pool:
        results = pool.map(lambda u: check_url(u, timeout), unique)
    return {r["url"]: r for r in results}


def same_host(a, b):
    """Do two URLs share a hostname, ignoring a leading www.?"""
    def host(u):
        try:
            name = (urllib.parse.urlsplit(u or "").hostname or "").lower()
            # Strip a literal leading "www." only. lstrip("www.") would eat any leading
            # run of 'w'/'.' characters (mangling e.g. a host that starts with "w").
            if name.startswith("www."):
                name = name[4:]
            return name
        except ValueError:
            return ""
    ha, hb = host(a), host(b)
    return bool(ha) and ha == hb


def is_bare_domain(url):
    """True if the URL has no meaningful path — a site homepage, not a program page.

    46 of 116 rows (40%) in the 08-20 scrape batch were bare domains, which usually means
    the model named an organisation rather than locating the opportunity's own page.
    """
    try:
        parts = urllib.parse.urlsplit(url or "")
    except ValueError:
        return False
    return bool(parts.hostname) and parts.path.strip("/") == "" and not parts.query


# Words that carry no identifying signal when matching a domain against an organisation
# name. "University", "Institute" and friends appear in hundreds of org names and in
# almost no registrable domain, so leaving them in makes every comparison look related.
_ORG_STOPWORDS = {
    "the", "a", "an", "of", "for", "and", "in", "at", "on", "to",
    "university", "college", "institute", "center", "centre", "school", "academy",
    "program", "programs", "department", "foundation", "society", "association",
    "national", "american", "america", "united", "states", "inc", "llc", "org",
    "summer", "high", "research", "internship", "students", "student", "education",
}


def _org_tokens(text):
    """Identifying words from an organisation/program name, long enough to match on."""
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if len(w) >= 4 and w not in _ORG_STOPWORDS}


def _acronyms(org, name=""):
    """Acronyms that could plausibly be the organisation’s own domain.

    Explicit ones ("NACLO", "FIT", "TCR") come from the org AND the name, since a program
    name routinely carries its own initialism. The initials of capitalised words come from
    the ORG ONLY: run over the name too, "NYU High School Summer Art Intensive" yields
    "nyuhssai", which is nobody’s domain and only widens the match.
    """
    text = f"{org or ''} {name or ''}"
    found = {a.lower() for a in re.findall(r"\b[A-Z]{3,8}\b", text)}
    # Initialisms come from _initial_forms(), which strips a trailing parenthetical first
    # ("Fermi National Accelerator Laboratory (Fermilab)" would otherwise yield "fnalf",
    # which matches nothing) and returns both the every-capitalised-word and the
    # generic-words-dropped shapes. Only the 3+ character ones belong here; the two-letter
    # ones need exact-label matching and are taken separately by domain_matches_org().
    found |= _initial_forms(org)[0]
    return found


def _initial_forms(org):
    """Every plausible initialism of an org name, as (loose, exact) sets.

    Two forms are built because the useful one differs by org. Taking every capitalised
    word gives "bu" for Boston University but only "cwm" for "College of William & Mary" —
    whose domain is `wm.edu`. Dropping the generic words first gives "wm" there, but leaves
    "University of Houston" with a single "h". Neither alone covers both, so both are
    produced and every result is bucketed by length.

    `loose` (3+ chars) is substring/prefix-matchable, like the explicit acronyms. `exact`
    (exactly 2) must equal a whole domain label: at two characters a substring rule matches
    nearly anything — "bu" would fire on `edinburgh`, `columbus`, `bulldogs`.

    Measured against the live catalog on 2026-08-23: without this, `uh.edu` (University of
    Houston), `bu.edu` (Boston University) and `wm.edu` (College of William & Mary) all
    read as third-party sites — the largest single group of false "unrelated" flags.
    """
    bare = re.sub(r"\([^)]*\)", " ", org or "")
    words = re.findall(r"\b[A-Z][a-z]+", bare)
    forms = {
        "".join(w[0] for w in words).lower(),
        "".join(w[0] for w in words if w.lower() not in _ORG_STOPWORDS).lower(),
    }
    return ({f for f in forms if len(f) >= 3}, {f for f in forms if len(f) == 2})


# Suffix labels that identify a registry, not an organisation.
_TLD_LABELS = {"com", "org", "net", "edu", "gov", "mil", "int", "app", "io", "co", "us",
               "uk", "ac", "info", "www"}


def domain_matches_org(url, org, name=""):
    """Does this URL live on the organisation's own site?

    Used to tell "the program's own page" from "a third-party article about the program".
    Measured on the 2026-08-23 batch: 10 of 166 rows (6%) stored a URL on an unrelated
    domain whose page was an SEO round-up ("19 Selective Internships for High School
    Students") that merely mentions the program — live links, so `check_urls` passes them,
    a bare-domain check passes them, and `is_low_value_path` passes them too. This is the
    only signal that catches them.

    Matching is by SUBSTRING against each domain label, not by whole tokens, because a
    domain label is words run together with no separator: an exact-token rule reads
    `idyllwildarts.org`, `tellurideassociation.org` and `artandwriting.org` as unrelated to
    the organisations that own them, and fired on 58% of the batch. Substring matching
    against long tokens only, with generic words removed, brings that to the real rate.

    Deliberately generous — a wrong "unrelated" flags a good row, which costs a reviewer
    more than a missed flag does. Acronyms and initialisms count (`mit.edu` for
    "Massachusetts Institute of Technology", `fitnyc.edu` for "FIT"). It answers "is this
    plausibly their site", not "is this the right page" — `is_bare_domain` and
    `url_dedupe.is_low_value_path` answer that.
    """
    try:
        host = (urllib.parse.urlsplit(url or "").hostname or "").lower()
    except ValueError:
        return True          # unparseable: not evidence of anything, do not flag
    if not host:
        return True
    labels = [l for l in re.split(r"[.\-]", host) if l and l not in _TLD_LABELS]
    if not labels:
        return True
    blob = f"{org or ''} {name or ''}"
    tokens = _org_tokens(blob)
    acronyms = {a for a in _acronyms(org, name) if len(a) >= 3}
    short = _initial_forms(org)[1]
    if not tokens and not acronyms and not short:
        return True          # nothing to compare against; silence beats a bogus flag
    for label in labels:
        if label in short:   # exact only — see _short_initials()
            return True
        if any(t in label for t in tokens):
            return True
        # The domain label may be an ABBREVIATION of an org word rather than contain it:
        # colum.edu (Columbia College Chicago), or the "u" + short-form shape that most
        # US universities use — umich.edu, upenn.edu. Both directions are needed; only
        # one of them was, and it read those three as third-party sites.
        if len(label) >= 4 and any(label in t for t in tokens):
            return True
        if len(label) >= 5 and label[0] == "u" and any(label[1:] in t for t in tokens):
            return True
        for acronym in acronyms:
            # `startswith` one way only: "fitnyc" is FIT’s own domain, but treating
            # a domain as a prefix OF an acronym matches very nearly anything.
            if label == acronym or label.startswith(acronym):
                return True
    return False

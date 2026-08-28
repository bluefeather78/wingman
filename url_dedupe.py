"""URL/name matching for user-submitted opportunities.

Why this exists separately from the `normalize_url()` that scrape_opportunities.py,
dryrun_common.py and migrate_to_supabase.py each carry: those three are deliberately
identical to one another (see the note in dryrun_common.py) and changing them would change
the scraper's dedupe behaviour, so they are left alone. This module is the stronger matcher
the *user-submission* path needs, and it is only used there.

The design rule here, which the catalog data forces:

    Exact normalized-URL equality is the ONLY signal strong enough to reject on.
    Everything else flags for a human and inserts anyway.

Measured against the 1330-row catalog snapshot:
  - 969 rows (73%) sit on a registrable domain shared with a DIFFERENT opportunity
    (nyu.edu alone hosts 36). So "same domain" cannot mean "duplicate".
  - fuzzy name matching at the scraper's 0.85 threshold produces 93 false-positive pairs
    ('1-Week Medical Academy' vs '3-Week Medical Academy' scores 0.95). So name similarity
    cannot mean "duplicate" either.
Both are still useful as *hints* pointed at a reviewer, which is what find_duplicates()
returns them as.
"""

import difflib
import re
import urllib.parse

# Query parameters that identify a referrer/campaign rather than the content. Everything
# NOT on this list is preserved, which matters: sites that key programs off a query string
# ("/programs?id=123" vs "?id=456") would otherwise collapse into a single row.
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
    "fbclid", "gclid", "msclkid", "dclid", "yclid", "twclid", "ttclid", "igshid",
    "wbraid", "gbraid", "mc_cid", "mc_eid", "_ga", "_gl", "ref", "ref_src", "referrer",
}

# Filenames that are the directory itself, so "/x/index.html" and "/x" are one page.
INDEX_FILES = {"index.html", "index.htm", "index.php", "default.aspx", "default.asp"}

# Path tails that are almost never the right canonical URL for an opportunity, even when
# the opportunity itself is genuinely new. 35 rows already in the catalog look like this.
LOW_VALUE_SEGMENTS = {
    "faq", "faqs", "about", "about-us", "contact", "contact-us", "apply", "application",
    "register", "registration", "signup", "sign-up", "login", "signin", "sign-in",
    "news", "blog", "privacy", "terms", "sitemap", "search", "donate", "support",
    "admissions", "admission", "cost", "costs", "tuition", "pricing", "rules", "eligibility",
}

# Document extensions that are never a program's canonical landing page — a rules PDF or an
# application form is a sub-resource, not the page a student should be sent to.
_LOW_VALUE_EXTENSIONS = (".pdf", ".doc", ".docx")

# Second-level labels that are part of a public suffix rather than the registrable name,
# so "cam.ac.uk" and "med.stanford.edu" resolve to the right owner.
_MULTIPART_TLD_HEADS = {"edu", "ac", "co", "com", "org", "net", "gov", "govt", "sch"}

# Name-similarity thresholds. Higher across hosts, since a shared host is itself evidence.
SAME_HOST_NAME_RATIO = 0.82
CROSS_HOST_NAME_RATIO = 0.88

# An identical URL is NOT sufficient to reject on by itself, because one URL can legitimately
# host several distinct opportunities. In the current catalog spicestanford.smapply.io backs
# six different programs (Stanford E-Japan, Sejong Korea Scholars, Stanford E-China, …) that
# all apply through one portal, and girlswhocode.com/programs/summer-immersion-program backs
# two. Rejecting on URL alone would make those permanently unsubmittable.
# So the URL must agree AND the name must agree before anything is auto-rejected. When the
# URL matches but the name does not, the submission is inserted with a strong flag instead —
# a wrongly-flagged row costs a reviewer ten seconds, a wrongly-rejected one is lost silently.
EXACT_DUP_NAME_RATIO = 0.82

# "Same site" on its own is only evidence when the site is quiet. naclo.org hosts one
# opportunity, so a second submission there is worth a look; nyu.edu hosts 36, where it
# means nothing and would bury the reviewer. Above this many existing rows on the domain,
# the bare same-site hint is dropped — the stronger signals (sub-page containment, name
# similarity) still fire regardless of how busy the domain is.
SAME_SITE_MAX_PEERS = 3

# Names too generic to carry evidence. The first is the fallback the client writes when AI
# extraction fails — without this, two failed extractions would match each other at 1.0.
GENERIC_NAMES = {"custom opportunity", "opportunity", "program", "summer program",
                 "internship", "competition", "conference", "journal", "untitled"}

# A containment match ("Secondary School Program" inside "Harvard Secondary School Program
# (SSP)") is a rename, not a coincidence, so it floors the similarity here — above every
# threshold in this module (0.82/0.88) and above the scraper's 0.9 in-run collapse rule.
CONTAINMENT_FLOOR = 0.9

# Connectors and the word "program" carry no identity; everything else in a name does. Kept
# deliberately SMALL — dropping "summer"/"school"/"academy" would let two distinct programs
# that merely share a category word satisfy the containment test below.
_NAME_FILLER = {"the", "a", "an", "of", "for", "and", "or", "in", "on", "at", "to", "with",
                "program", "programs"}

# If a name contains any of these it is a real program, not a bare institution label, no
# matter how much of it the host domain also matches. Used only to PROTECT a name from the
# bare-institution suppression — erring toward "this is a real name" keeps a good hint.
_PROGRAM_WORDS = {
    "academy", "academies", "camp", "camps", "scholar", "scholars", "internship",
    "internships", "research", "summer", "session", "seminar", "fellowship", "bootcamp",
    "olympiad", "challenge", "workshop", "immersion", "apprenticeship", "clinic",
    "symposium", "symposia", "competition", "conference", "journal", "mentorship",
    "pathways", "insights", "engineering", "precollege", "pre", "honors", "forum",
}

# Institution words stripped before asking "is anything program-specific left?".
_INSTITUTION_WORDS = {"university", "college", "institute", "school", "state",
                      "u", "the", "of", "at"}


def _distinctive_tokens(name):
    """Normalized name tokens with connectors and 'program' removed."""
    return [t for t in normalize_name(name).split() if t and t not in _NAME_FILLER]


def _is_containment_match(a, b):
    """The shorter name's distinctive tokens are ALL in the longer's, and there are >= 2.

    Two is load-bearing: a SINGLE shared distinctive word is how the programs sharing one
    application portal relate ('Scholars Program' inside 'China Scholars Program'), and
    matching those would merge distinct opportunities. Two shared distinctive words is the
    'X' vs 'Org X (ACRONYM)' rename pattern this exists to catch.
    """
    sa, sb = set(_distinctive_tokens(a)), set(_distinctive_tokens(b))
    if not sa or not sb:
        return False
    small, big = (sa, sb) if len(sa) <= len(sb) else (sb, sa)
    return len(small) >= 2 and small <= big


def _token_in_host(token, host):
    """Loose 'this name-word belongs to this host', mirroring url_validate.domain_matches_org's
    generosity WITHOUT importing it (url_validate imports this module — a cycle)."""
    if len(token) < 3:
        return False
    for label in (host or "").split("."):
        if label in _MULTIPART_TLD_HEADS or len(label) < 3:
            continue
        if token in label or (len(label) >= 4 and label in token):
            return True
        if len(label) >= 5 and label[0] == "u" and label[1:] in token:  # umich, upenn
            return True
    return False


def _is_bare_institution(name, host):
    """True when a name is just the institution — 'OSU', 'Tufts University',
    'University of Wisconsin-Madison' — carrying no program of its own.

    Two rows that are BOTH bare institution names score 1.0 on raw similarity (same string)
    and manufacture a false 'strong' duplicate hint between genuinely different programs
    (the Tulane/UTEP/UTSA rows in the 2026-08-28 audit). Suppressing the name signal for
    these fails OPEN — no name-based hint is emitted — which is the safe direction.
    """
    toks = _distinctive_tokens(name)
    if not toks:
        return False
    if any(t in _PROGRAM_WORDS for t in toks):
        return False
    leftover = [t for t in toks
                if t not in _INSTITUTION_WORDS and not _token_in_host(t, host)]
    return not leftover


def split_url(url):
    """(scheme, host, path, query) with host lowercased and 'www.'/default port removed.

    Path case is PRESERVED here — many hosts serve case-sensitive paths, and the catalog
    contains 100 URLs with uppercase in them. Lowercasing happens only in the match key.
    """
    raw = (url or "").strip()
    if not raw:
        return "", "", "", ""
    if "://" not in raw:
        raw = "https://" + raw.lstrip("/")
    try:
        p = urllib.parse.urlsplit(raw)
    except ValueError:
        return "", "", "", ""
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if p.port and ((p.scheme == "https" and p.port == 443) or (p.scheme == "http" and p.port == 80)):
        pass  # default port carries no meaning; hostname already excludes it
    path = p.path or "/"
    segments = [s for s in path.split("/") if s]
    if segments and segments[-1].lower() in INDEX_FILES:
        segments.pop()
    path = "/" + "/".join(segments)
    return (p.scheme or "https").lower(), host, path, p.query or ""


def _clean_query(query):
    """Drop tracking parameters, keep and sort the rest so order doesn't affect the key."""
    if not query:
        return ""
    kept = [(k, v) for k, v in urllib.parse.parse_qsl(query, keep_blank_values=True)
            if k.lower() not in TRACKING_PARAMS]
    return urllib.parse.urlencode(sorted(kept))


def match_key(url):
    """Aggressively normalized key for exact-duplicate comparison.

    Collapses scheme, 'www.', default ports, fragment, trailing slash, index files,
    tracking params and case. Two URLs sharing a key are treated as the same page.
    Never store this — it is lossy on purpose. Store the URL the user actually gave.
    """
    _, host, path, query = split_url(url)
    if not host:
        return ""
    q = _clean_query(query)
    return f"{host}{path.rstrip('/').lower()}" + (f"?{q.lower()}" if q else "")


def registrable_domain(host):
    """Best-effort owner domain: 'apply.naclo.org' and 'www.naclo.org' -> 'naclo.org'."""
    parts = [p for p in (host or "").split(".") if p]
    if len(parts) <= 2:
        return ".".join(parts)
    if len(parts[-1]) == 2 and parts[-2] in _MULTIPART_TLD_HEADS:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def normalize_name(name):
    """Lowercase, drop a 4-digit year and punctuation, collapse whitespace.

    Dropping the year is what lets 'ISEF 2025' and 'ISEF 2026' — the same recurring
    program on its next cycle — compare as equal.
    """
    n = (name or "").lower()
    n = re.sub(r"\b(19|20)\d{2}\b", " ", n)
    n = re.sub(r"[^a-z0-9]+", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def name_similarity(a, b):
    """0.0-1.0 similarity, or 0.0 when either name is too generic to be evidence."""
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb or na in GENERIC_NAMES or nb in GENERIC_NAMES:
        return 0.0
    if len(na) < 4 or len(nb) < 4:
        return 0.0
    ratio = difflib.SequenceMatcher(None, na, nb).ratio()
    if ratio < CONTAINMENT_FLOOR and _is_containment_match(a, b):
        return CONTAINMENT_FLOOR
    return ratio


def is_low_value_path(url):
    """True if the URL points at an FAQ/about/apply-style sub-page.

    Not a duplicate signal — a data-quality one. Even a genuinely new opportunity should
    not be catalogued under its FAQ page.
    """
    _, _, path, _ = split_url(url)
    segments = [s.lower() for s in path.split("/") if s]
    if not segments:
        return False
    last = segments[-1]
    return last.endswith(_LOW_VALUE_EXTENSIONS) or last in LOW_VALUE_SEGMENTS


def _prefix_relation(path_a, path_b):
    """'shorter is a real-path prefix of longer' — the high-precision containment signal.

    Returns False when the shorter side is the bare root: 77 of the catalog's 102
    same-host prefix pairs are root-vs-deep, which is just the shared-domain problem
    again and would flag a third of the catalog.
    """
    a, b = sorted([path_a.rstrip("/").lower(), path_b.rstrip("/").lower()], key=len)
    return bool(a) and a != "/" and b.startswith(a + "/")


def find_duplicates(url, name, existing_rows, apply_url=None):
    """Compare one submission against the catalog.

    existing_rows: dicts with at least id/name/url, optionally apply_url.

    Returns (exact_row, candidates). `exact_row` is a hard duplicate — same page, do not
    insert. `candidates` are ranked hints for a human, each {id, name, url, reason,
    confidence}; they NEVER justify rejecting on their own.
    """
    key = match_key(url)
    _, host, path, _ = split_url(url)
    domain = registrable_domain(host)
    apply_key = match_key(apply_url) if apply_url else ""

    # How crowded is this domain already? Decides whether a bare same-site match is a
    # useful hint or just noise. See SAME_SITE_MAX_PEERS.
    peers = 0
    if domain:
        for row in existing_rows or []:
            _, rh, _, _ = split_url(row.get("url") or "")
            if rh and registrable_domain(rh) == domain:
                peers += 1
    same_site_is_evidence = peers <= SAME_SITE_MAX_PEERS

    candidates = []
    for row in existing_rows or []:
        row_url = row.get("url") or ""
        row_key = match_key(row_url)
        if not row_key:
            continue

        if key and row_key == key:
            # Same page. Only a duplicate if the name agrees too — otherwise this is one
            # of the shared-application-portal cases (see EXACT_DUP_NAME_RATIO).
            if (normalize_name(name) == normalize_name(row.get("name"))
                    or name_similarity(name, row.get("name")) >= EXACT_DUP_NAME_RATIO):
                return row, []
            candidates.append(dict(
                row, confidence="strong", ratio=1.0,
                reason="identical URL but a different name — either the same opportunity "
                       "renamed, or a second program sharing one application portal"))
            continue

        # The submitted page is where an existing row already sends people to apply (or
        # the reverse). Same destination, different landing page.
        row_apply_key = match_key(row.get("apply_url") or "")
        if key and (key == row_apply_key or (apply_key and apply_key == row_key)):
            candidates.append(dict(row, reason="apply-url points at the same page",
                                   confidence="strong"))
            continue

        _, row_host, row_path, _ = split_url(row_url)
        same_domain = bool(domain) and registrable_domain(row_host) == domain
        ratio = name_similarity(name, row.get("name"))
        # A name that is just the institution ('OSU', 'Tufts University') is not evidence of
        # anything — two different programs both carrying it score 1.0. Drop the name-based
        # hint for those; the URL and prefix signals are unaffected.
        name_is_evidence = not (_is_bare_institution(name, host)
                                or _is_bare_institution(row.get("name"), row_host))

        if same_domain and _prefix_relation(path, row_path):
            reason, confidence = "one URL is a sub-page of the other on the same site", "strong"
        elif same_domain and name_is_evidence and ratio >= SAME_HOST_NAME_RATIO:
            reason, confidence = f"same site, name {int(ratio * 100)}% similar", "strong"
        elif name_is_evidence and ratio >= CROSS_HOST_NAME_RATIO:
            reason, confidence = f"name {int(ratio * 100)}% similar", "weak"
        elif same_domain and same_site_is_evidence:
            plural = "entry" if peers == 1 else "entries"
            reason, confidence = f"same site ({peers} existing {plural})", "weak"
        else:
            continue
        candidates.append(dict(row, reason=reason, confidence=confidence, ratio=ratio))

    order = {"strong": 0, "weak": 1}
    candidates.sort(key=lambda c: (order.get(c["confidence"], 9), -c.get("ratio", 0)))
    trimmed = [{"id": c.get("id"), "name": c.get("name"), "url": c.get("url"),
                "reason": c["reason"], "confidence": c["confidence"]} for c in candidates[:5]]
    return None, trimmed

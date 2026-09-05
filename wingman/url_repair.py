#!/usr/bin/env python3
"""Finding the correct URL for a catalog row whose stored one is dead. FREE — HTTP only.

Used by agents/check_links.py, which calls this BEFORE deactivating a row and again (via
--repair-flagged) over rows it deactivated on an earlier pass.

WHY A REPAIR STEP EXISTS
The 2026-08-23 scraper audit already showed the answer is usually still on the same site:
of 30 dead rows, 9 were re-found and 9 of 9 came back live —
`tisch.nyu.edu/.../summer-filmmakers-workshop` -> `.../filmmakers-workshop`,
`med.stanford.edu/psychiatry/education/CNIX.html` -> `.../highschool.html`. Programs get
reorganised; they rarely vanish. Deactivating without looking throws away a real
opportunity over a moved page.

PROPOSE IS CHEAP, ACCEPT IS EXPENSIVE — and the split IS the design
Finding plausible candidates is easy and worthless on its own. Measured over the 148 rows
check_links deactivated, taking the best-scoring link on the parent page "repaired" 72 of
them (49%) and a large share pointed at a DIFFERENT PROGRAM at the same institution:

    ll.mit.edu/outreach/summer-high-school-internships  ->  middle-school-stem-program
    training.nih.gov/research-training/hs/aip_hs/       ->  .../pb/sip/      (AIP != SIP)
    medschool.vanderbilt.edu/imsd/high-school-summer... ->  /md/

A wrong repair is worse than no repair: it is a live link, so every downstream check passes
it, and it silently sends a student to something that is not what the row promises. So
nothing is accepted on similarity. THREE independent tests must all pass:

1. TITLE PROOF. Fetch the candidate and require every distinctive word of the program's
   name in its <title>. This is the same check the 08-23 off-site audit used to find the
   10 SEO-listicle rows. A similarity ratio was tried and rejected: at >= 0.72 it accepted
   "Bay Area Entrepreneurship" -> "BootCamp Entrepreneurship" (0.76), "Summer Research
   Immersion" -> "First-year Research Immersion", "VEX Robotics Competition" -> "RECF
   Robotics Competition", and a UC Berkeley course -> the same provider's Yale one. In
   every case the shared word is the CATEGORY and the differing word is the IDENTITY —
   exactly backwards for a ratio over whole strings.

2. THE NAME MUST BE ITS OWN. Distinctive words are the name's minus the org's, so a match
   on the institution cannot stand in for a match on the program. Without this,
   "University of Notre Dame" verified against every page on nd.edu, "Jackson Laboratory
   Summer Student Program" against "Careers at The Jackson Laboratory", and "Doodle for
   Google" against "Google Doodles". Fewer than two such words left = unverifiable, and
   the row is left alone rather than guessed at.

3. NO LOST IDENTITY WORD. If the OLD url used a word to identify this program and the new
   url and its title both lack it, we have landed on a sibling. This is what catches a row
   whose name and org are swapped in the catalog — `name='University of Notre Dame'`,
   `org='Global Scholars Program'`, old slug `global-scholars`, candidate `summer-scholars`,
   which passes tests 1 and 2 and is still the wrong program.

Measured end to end over the same 148: 72 -> 34 -> 18 -> **13 accepted**, and the 13 are
right. That trade is deliberate. This runs on a catalog students use, and the rows it does
not repair are not lost — they stay in the review queue with their candidates attached as a
suggestion for a person, which is strictly more than they had before.
"""
import concurrent.futures
import html
import re
import urllib.error
import urllib.parse
import urllib.request

from wingman import url_dedupe
from wingman import url_validate as uv
from wingman.url_guard import safe_urlopen

DEFAULT_TIMEOUT = 20
MAX_CANDIDATES = 10
PAGE_BYTES = 400_000

# Words that identify nothing in a program name — every third row in this catalog is a
# "Summer High School Research Internship Program".
NAME_STOPWORDS = {
    "summer", "high", "school", "program", "programs", "internship", "internships",
    "research", "student", "students", "the", "and", "for", "with", "camp", "institute",
    "university", "college", "center", "centre", "academy", "course", "courses", "youth",
    "pre", "session", "sessions", "experience", "opportunity", "project",
    "www", "edu", "org", "com", "net", "html", "htm", "php", "aspx", "index",
}

# Slugs that are never a program's own page, so never a repair target.
GENERIC_SLUGS = {
    "about", "index", "home", "people", "news", "events", "news-events", "calendar",
    "contact", "search", "programs", "program", "courses", "education", "students",
    "apply", "admissions", "faq", "overview", "resources", "staff", "directory",
    "undergraduate", "graduate", "give", "donate", "login", "sitemap", "careers",
}

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_LINK_RE = re.compile(r'<a\s[^>]*href\s*=\s*["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_EXT_RE = re.compile(r"\.(html?|php|aspx?)$", re.I)


def _fetch(url, timeout=DEFAULT_TIMEOUT):
    """(page_text, final_url). page_text is None for a non-HTML or failed response."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": uv.USER_AGENT})
        # S1-4: url_repair walks candidate paths on a stored host, so a submitted private
        # address would be probed repeatedly — from the operator's laptop, on the LAN.
        with safe_urlopen(req, timeout=timeout) as r:
            if r.status >= 400:
                return None, None
            ctype = (r.headers.get("Content-Type") or "").lower()
            if "html" not in ctype and "xml" not in ctype:
                return None, r.geturl()
            return r.read(PAGE_BYTES).decode("utf-8", errors="replace"), r.geturl()
    except Exception:
        return None, None


def _text(raw):
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub(" ", raw or ""))).strip()


def page_title(page):
    m = _TITLE_RE.search(page or "")
    return _text(m.group(1)) if m else ""


def _words(text):
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if len(w) >= 3 and w not in NAME_STOPWORDS}


def identity_words(name, org=""):
    """The program's OWN identifying words — its name's, minus generic ones, minus the org's.

    See test 2 in the module docstring: without the org subtraction, a match on the
    institution passes for a match on the program.
    """
    return _words(name) - _words(org)


def _path_words(url):
    try:
        path = urllib.parse.urlsplit(url or "").path
    except ValueError:
        return set()
    return _words(path.replace("/", " ").replace("-", " ").replace("_", " "))


def _slug(url):
    try:
        segs = [s for s in urllib.parse.urlsplit(url or "").path.split("/") if s]
    except ValueError:
        return ""
    return _EXT_RE.sub("", segs[-1]).lower() if segs else ""


def title_proves(title, name, org):
    """Test 1 + 2. (ok, why) — does this page's title prove it is THIS program's page?"""
    if not title:
        return False, "page has no title"
    haystack = re.sub(r"[^a-z0-9]+", " ", title.lower())
    words = identity_words(name, org)
    if len(words) < 2:
        return False, "name has fewer than two words of its own to verify against"
    missing = sorted(w for w in words if w not in haystack)
    if missing:
        return False, f"title lacks {', '.join(missing[:3])}"
    return True, f"title carries all {len(words)} of the name's own words"


def keeps_identity(old_url, new_url, title, name, org):
    """Test 3. (ok, why) — did the candidate drop a word the old URL used to identify this?

    `name` and `org` are read TOGETHER here, unlike identity_words(): the distinguishing
    word can sit in either field, and rows exist with the two swapped.
    """
    ident = _words(name) | _words(org)
    old_ident = ident & _path_words(old_url)
    if not old_ident:
        return True, ""
    seen = _path_words(new_url) | _words(title)
    lost = sorted(w for w in old_ident if w not in seen)
    if lost:
        return False, f"drops '{lost[0]}', which the old URL used to identify it"
    return True, ""


def _variants(url):
    """Mechanical rewrites that address the same resource — case, slash, www, index file."""
    try:
        p = urllib.parse.urlsplit(url)
    except ValueError:
        return []
    hosts = {p.netloc}
    hosts.add(p.netloc[4:] if p.netloc.startswith("www.") else "www." + p.netloc)
    paths = {p.path, p.path.rstrip("/") or "/"}
    if not p.path.endswith("/"):
        paths.add(p.path + "/")
    for name in ("index.html", "index.htm", "index.php", "default.aspx"):
        if p.path.lower().endswith(name):
            paths.add(p.path[: -len(name)])
    if p.path != p.path.lower():
        paths.add(p.path.lower())
    out = []
    for host in hosts:
        for path in paths:
            cand = urllib.parse.urlunsplit((p.scheme, host, path, p.query, ""))
            if cand != url and cand not in out:
                out.append(cand)
    return out


def _deepest_live_ancestor(url, timeout):
    """The nearest parent directory that actually serves a page. ('', None, None) if none.

    Deepest-first and it stops at the first hit: a program's own section page lists its
    siblings, while the site root lists the whole institution and proposes noise.
    """
    try:
        p = urllib.parse.urlsplit(url)
    except ValueError:
        return "", None, None
    segs = [s for s in p.path.split("/") if s]
    for i in range(len(segs) - 1, -1, -1):
        path = "/" + "/".join(segs[:i]) + ("/" if i else "")
        anc = urllib.parse.urlunsplit((p.scheme, p.netloc, path, "", ""))
        page, final = _fetch(anc, timeout)
        if page:
            return anc, page, final
    return "", None, None


def propose(url, name, org, timeout=DEFAULT_TIMEOUT):
    """Candidate URLs worth verifying, best first. PROPOSAL ONLY — nothing is accepted here."""
    try:
        host = urllib.parse.urlsplit(url).netloc
    except ValueError:
        return [], ""
    ident = identity_words(name, org)
    old_slug = _slug(url)
    out, seen = [], {url}

    for cand in _variants(url):
        if cand not in seen:
            seen.add(cand)
            out.append((cand, "variant", 1.0))

    anc, page, final = _deepest_live_ancestor(url, timeout)
    scored = []
    for href, raw_label in _LINK_RE.findall(page or ""):
        try:
            cand = urllib.parse.urljoin(final or anc, html.unescape(href.strip())).split("#")[0]
        except ValueError:
            continue
        if not cand.lower().startswith(("http://", "https://")) or cand in seen:
            continue
        try:
            if urllib.parse.urlsplit(cand).netloc != host:
                continue          # same site only; another domain is a new source, not a repair
        except ValueError:
            continue
        slug = _slug(cand)
        if not slug or slug in GENERIC_SLUGS or uv.is_bare_domain(cand):
            continue
        label = _text(raw_label)[:200]
        readable = slug.replace("-", " ").replace("_", " ")
        score = max(url_dedupe.name_similarity(label, name),
                    url_dedupe.name_similarity(readable, name),
                    url_dedupe.name_similarity(slug, old_slug))
        # A word shared with the program's own identity outranks a ratio over two short
        # strings — the ratio is what let siblings through in the first place.
        if ident and any(w in (slug + " " + label.lower()) for w in ident):
            score = max(score, 0.6)
        if score > 0.2:
            seen.add(cand)
            scored.append((cand, "ancestor-link", score))
    scored.sort(key=lambda c: -c[2])
    out.extend(scored[:MAX_CANDIDATES])
    return out[:MAX_CANDIDATES + 4], anc


def repair_url(url, name, org, timeout=DEFAULT_TIMEOUT):
    """Try to find this row's real URL. Returns a dict; `url` is None when nothing verified.

        {"url", "how", "title", "why", "ancestor", "suggestion", "rejected"}

    `suggestion` is the best candidate that FAILED verification, kept so a reviewer gets a
    lead instead of a bare "dead link". It is never written to the row's url field.
    """
    out = {"url": None, "how": None, "title": None, "why": None, "ancestor": "",
           "suggestion": None, "rejected": []}
    if len(identity_words(name, org)) < 2:
        out["why"] = "program name has fewer than two words of its own — nothing to verify against"
        return out

    candidates, ancestor = propose(url, name, org, timeout)
    out["ancestor"] = ancestor
    for cand, how, _score in candidates:
        page, final = _fetch(cand, timeout)
        if not page:
            continue
        final = final or cand
        if uv.is_bare_domain(final):
            continue          # a redirect to the homepage is the soft-404 case, not a repair
        title = page_title(page)
        ok, why = title_proves(title, name, org)
        if ok:
            ok, lost_why = keeps_identity(url, final, title, name, org)
            why = why if ok else lost_why
        if ok:
            out.update(url=final, how=how, title=title, why=why)
            return out
        out["rejected"].append({"url": cand, "title": title[:80], "why": why})
        if out["suggestion"] is None and title:
            out["suggestion"] = {"url": cand, "title": title[:80], "why": why}

    out["why"] = (f"{len(out['rejected'])} candidate(s) checked, none proved to be this program"
                  if out["rejected"] else "no candidate found on the site")
    return out


# ---------------------------------------------------------------------------------------
# Phase-2 URL truth (scrape_opportunities.resolve_url_truth): prove a stored URL names the
# program, and recover a program's OWN page from a page written ABOUT it.

def title_proof_url(url, name, org, timeout=DEFAULT_TIMEOUT):
    """(verdict, title) for a stored URL:
        True  — the page title proves this is the program's page,
        False — the page loaded but its title does not prove it (a PDF/non-HTML counts here),
        None  — the page could not be fetched (blocked/timeout), or the name has fewer than
                two words of its own to verify against; either way the URL stays approvable.

    A blocked fetch is a fact about our HTTP client (~10-20% of sites refuse us), never about
    the program, so it must not read as a failed proof. A non-HTML 200 (a PDF) DID load and
    still cannot be the landing page, so that is a real failure worth flagging.
    """
    if len(identity_words(name, org)) < 2:
        return None, ""
    page, final = _fetch(url, timeout)
    if page is None:
        # _fetch gives (None, final_url) for a non-HTML 200 (PDF), (None, None) when nothing
        # loaded at all (blocked / 4xx / 5xx / timeout).
        return (False if final is not None else None), ""
    title = page_title(page)
    ok, _why = title_proves(title, name, org)
    return bool(ok), title


def extract_primary_link(page_html, name, org, base_url="", timeout=DEFAULT_TIMEOUT, max_fetch=5):
    """The program's OWN page, linked from a page ABOUT it (a listicle/blog/off-site article).

    Harvest <a> links, rank by how many of the program's identity words land in the anchor
    text and slug, then fetch the top few and accept the FIRST whose page is on the org's own
    domain AND whose title proves the program. BOTH gates are required: an SEO mill's most
    prominent outbound links are its own signup funnels — on the mill's domain, or not naming
    the program. Returns (url, title) or (None, None). Free HTTP; at most `max_fetch` fetches.
    """
    ident = identity_words(name, org)
    if len(ident) < 2:
        return None, None
    scored, seen = [], set()
    for href, raw_label in _LINK_RE.findall(page_html or ""):
        try:
            cand = urllib.parse.urljoin(base_url, html.unescape(href.strip())).split("#")[0]
        except ValueError:
            continue
        if not cand.lower().startswith(("http://", "https://")) or cand in seen:
            continue
        seen.add(cand)
        if uv.is_bare_domain(cand) or uv.is_content_mill(cand):
            continue                       # a homepage or another mill is never the page
        slug = _slug(cand)
        readable = slug.replace("-", " ").replace("_", " ")
        hay = f"{slug} {readable} {_text(raw_label)[:200].lower()}"
        overlap = sum(1 for w in ident if w in hay)
        if overlap:
            scored.append((cand, overlap, url_dedupe.name_similarity(readable, name)))
    scored.sort(key=lambda c: (-c[1], -c[2]))
    fetched = 0
    for cand, _ov, _sim in scored:
        if fetched >= max_fetch:
            break
        if not uv.domain_matches_org(cand, org, name):
            continue                       # cheap gate before paying for a fetch
        page, final = _fetch(cand, timeout)
        fetched += 1
        if not page:
            continue
        final = final or cand
        if uv.is_bare_domain(final) or uv.is_content_mill(final):
            continue
        title = page_title(page)
        if title_proves(title, name, org)[0]:
            return final, title
    return None, None


def repair_many(rows, timeout=DEFAULT_TIMEOUT, workers=8):
    """{row_id: repair_url(...)} for rows shaped {"id", "url", "name", "org"}.

    Narrower than url_validate's sweep on purpose: each row here costs an ancestor fetch
    plus up to a dozen candidate fetches, all against one host, so the width that is
    reasonable for one request per row is not reasonable here.
    """
    rows = [r for r in rows if r.get("url")]
    if not rows:
        return {}

    def work(row):
        return row["id"], repair_url(row["url"], row.get("name") or "",
                                     row.get("org") or "", timeout)

    with concurrent.futures.ThreadPoolExecutor(min(workers, len(rows))) as pool:
        return dict(pool.map(work, rows))

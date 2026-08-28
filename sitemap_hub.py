#!/usr/bin/env python3
"""Sitemap+LLM hub discovery — predict a hub's opportunity HOME pages from its own URL list.

THE PRINCIPLE (same as sitemap_common, one level up): enumerate a site's OWN pages, then let a
model CHOOSE — never hand-maintain a growing pile of slug drop-lists. Proven in a 10-hub spike
(2026-08-28): fed the scoped URL list, the classifier returned exactly the real program home
pages and excluded the tuition / campus-life / staff-bio / adult-program pages the slug lists
kept fighting, at a fraction of a cent per hub.

    program_candidates(hub_url, classify=None) -> (urls, trace)

`classify=None` runs the FREE half only (enumerate + scope) and returns the URLs that WOULD be
classified — the preview. Pass an injected classifier (the paid LLM call, or a fake in tests) to
get the predicted program pages. Everything except the classify call is free, stdlib + our own
url helpers, and injectable-fetch so the whole brain is unit-testable offline.

Pipeline:  ENUMERATE (sitemap ∪ anchor, host-rewritten, normalized)  ->  SCOPE (hub path prefix)
           ->  CLASSIFY (LLM: paths in, program-page paths out)  ->  CONTAINMENT (drop children).

WHY each step, measured in the spike:
- UNION, not sitemap-or-anchor: rockefeller.edu's sitemap omits its whole /outreach/ section, so
  a sitemap-only feed found ZERO programs; the anchor harvest supplies them. Conversely an anchor
  harvest omits pages the index does not link. Each covers the other's gap.
- SCOPE to the hub's path: unlv.edu's "sitemap" is the entire 20k-URL university; unscoped it
  cost $0.29 and buried the 3 real /caeo/ programs among hundreds of certificates. Scoped to
  /caeo/ it was 18 URLs, $0.0004, and precise. (This is sitemap_common.scope, applied to a hub.)
- CONTAINMENT stays as a cheap safety net (a program page beats its /residential-life child).
- The extractor downstream still fetches + verifies each page, and rows still land
  is_active=false / pending_review — a misclassification costs one review, never a live bad row.
"""
import re
import urllib.parse

import sitemap_common
import url_dedupe
import url_repair


# ---------- enumerate ----------

def _normalize(url, origin):
    """A harvested href, cleaned: absolute on `origin`, fragment and trailing-space/%20 removed.
    The spike saw '/outreach/snp/%20' and '/outreach/snp/' as two candidates — one page, an
    encoded trailing space in the hub's anchor. Collapsing them stops a paid double-extraction."""
    try:
        u = urllib.parse.urljoin(origin + "/", (url or "").strip()).split("#")[0]
    except ValueError:
        return ""
    p = urllib.parse.urlsplit(u)
    path = re.sub(r"(?:%20|\s)+$", "", p.path).rstrip("/")   # drop trailing space / %20 / slash
    if not p.netloc:
        return ""
    return urllib.parse.urlunsplit((p.scheme or "https", p.netloc, path, p.query, ""))


def enumerate_urls(hub_url, fetch=None, timeout=15):
    """(sources, [urls]) — the UNION of the host's sitemap (host-rewritten to the hub's public
    host) and the hub page's same-registrable-domain anchor links, normalized and deduped by
    url_dedupe.match_key. `sources` names what actually contributed (e.g. 'sitemap+anchor')."""
    fetch = fetch or sitemap_common.default_fetch
    host = urllib.parse.urlsplit(hub_url)
    origin = f"{host.scheme or 'https'}://{host.netloc}"
    hub_dom = url_dedupe.registrable_domain(host.netloc)
    out, seen, sources = [], set(), []

    def _add(url):
        if not url:
            return
        try:
            k = url_dedupe.match_key(url)
        except ValueError:
            return
        if k and k not in seen:
            seen.add(k)
            out.append(url)

    sm_urls = sitemap_common.locate_sitemaps(hub_url, fetch)
    entries = sitemap_common.collect_urls(sm_urls, fetch) if sm_urls else []
    if entries:
        sources.append(f"sitemap({len(entries)})")
        for loc, _lm in entries:
            _add(_normalize(urllib.parse.urlsplit(loc).path, origin))   # rewrite CDN/staging host

    try:
        links = url_repair._LINK_RE.findall(fetch_text(hub_url, fetch, timeout))
    except Exception:
        links = []
    anchor = 0
    for href, _label in links:
        u = _normalize(href, origin)
        if u and url_dedupe.registrable_domain(urllib.parse.urlsplit(u).netloc) == hub_dom:
            anchor += 1
            _add(u)
    if anchor:
        sources.append(f"anchor({anchor})")
    return "+".join(sources) or "none", out


def fetch_text(url, fetch, timeout):
    """The hub page's HTML as text (for the anchor harvest). Bytes -> str, never raises upward."""
    body = fetch(url)
    if isinstance(body, bytes):
        return body.decode("utf-8", "replace")
    return body or ""


# ---------- scope ----------

def scope_to_hub(urls, hub_url):
    """Keep only URLs under the hub's own path prefix, at a segment boundary — the section the
    hub indexes. '' (a root hub) keeps everything; the ceiling upstream still bounds collection."""
    prefix = urllib.parse.urlsplit(hub_url).path.rstrip("/")
    if not prefix:
        return list(urls)
    return [u for u in urls
            if (lambda p: p == prefix or p.startswith(prefix + "/"))(
                urllib.parse.urlsplit(u).path.rstrip("/"))]


# ---------- containment (reuse the miner's rule) ----------

def _drop_contained(urls):
    """Drop a URL that is the direct sub-page of another URL in the set (same host). A program's
    own page beats its /residential-life child. Pure; mirrors mine_hub_pages.contained_children
    without importing it (keeps this module free of the anchor-rules stack)."""
    def hp(u):
        s = urllib.parse.urlsplit(u)
        return (s.hostname or "").lower(), (s.path or "").rstrip("/").lower()
    own = {}
    for u in urls:
        h, p = hp(u)
        own.setdefault(h, set()).add(p)
    keep = []
    for u in urls:
        h, p = hp(u)
        segs = [s for s in p.split("/") if s]
        parent = "/" + "/".join(segs[:-1]) if len(segs) >= 2 else ""
        if parent and parent != p and parent in own.get(h, set()):
            continue
        keep.append(u)
    return keep


# ---------- classify (the ONE paid step; injected so this module stays testable) ----------

CLASSIFY_SYSTEM = (
    "You are given a list of URL paths from ONE institution's website. Return ONLY the paths "
    "that are the HOME PAGE of a single high-school extracurricular opportunity — a specific "
    "named program, competition, internship, camp, or academy a student would apply to.\n"
    "DO return (a specific named program's own landing page):\n"
    "  /programs/summer-programs/college-edge-summer\n"
    "  /outreach/ssrp\n"
    "  /oce/our-programs/talent-search\n"
    "DO NOT return:\n"
    "  /programs                     (an INDEX that lists many programs)\n"
    "  /admissions/program-costs     (a tuition or fees page)\n"
    "  /admissions/dates-and-deadlines (an ancillary tab)\n"
    "  /programs/nyc-residential-summer/residential-life  (a SUB-PAGE of a program)\n"
    "  /news/how-columbia-shaped-me  (a news article)\n"
    "  /person/jim-applegate         (a staff or person bio)\n"
    "  /columbia-experience/commuting-campus  (a campus-life page)\n"
    "  /caeo/adult  /caeo/eoc        (a non-high-school audience: adult / continuing ed)\n"
    "Return a JSON array of the exact input path strings you kept, and nothing else. If none "
    "qualify, return []."
)

CLASSIFY_CHUNK = 150            # paths per call — bounded so a big list cannot truncate the JSON


def make_gemini_classifier(key, timeout=40, min_delay=5, chunk=CLASSIFY_CHUNK):
    """Return classify(urls) -> (kept_urls, cost). PAID (M8 prompt + M9 call): one no-search
    Gemini call per chunk of paths. Kept out of program_candidates so the pure pipeline can be
    unit-tested with a fake classifier and no key."""
    from gemini_common import call_gemini, extract_json, estimate_cost, set_min_delay
    set_min_delay(min_delay)

    def classify(urls):
        by_path = {}
        for u in urls:
            by_path.setdefault(urllib.parse.urlsplit(u).path.rstrip("/") or "/", u)
        paths = list(by_path)
        kept, cost = [], 0.0
        for i in range(0, len(paths), chunk):
            block = paths[i:i + chunk]
            user = "PATHS:\n" + "\n".join(block) + "\n\nReturn the JSON array now."
            out, usage = call_gemini(CLASSIFY_SYSTEM, user, key, use_web_search=False,
                                     max_tokens=2000, timeout=timeout)
            cost += estimate_cost(usage)
            arr = extract_json(out)
            if isinstance(arr, list):
                for p in arr:
                    p = (p or "").strip().rstrip("/") or "/"
                    if p in by_path:
                        kept.append(by_path[p])
        return kept, cost
    return classify


# ---------- public entry point ----------

def program_candidates(hub_url, classify=None, fetch=None, timeout=15):
    """(urls, trace). classify=None -> FREE preview (enumerate+scope only, the URLs that WOULD be
    classified). Pass a classifier for the predicted program pages. Never raises: any failure
    degrades to ([], trace) so the caller can fall back to the anchor-rules miner."""
    trace = {"hub": hub_url, "enumerated": 0, "in_scope": 0, "classified": 0, "kept": 0,
             "cost": 0.0}
    try:
        src, urls = enumerate_urls(hub_url, fetch=fetch, timeout=timeout)
    except Exception as e:
        trace["error"] = f"enumerate: {str(e)[:80]}"
        return [], trace
    trace["sources"] = src
    trace["enumerated"] = len(urls)
    scoped = scope_to_hub(urls, hub_url)
    trace["in_scope"] = len(scoped)
    if classify is None:
        trace["kept"] = len(scoped)                 # preview: nothing paid, nothing chosen yet
        return scoped, trace
    try:
        picked, cost = classify(scoped)
        trace["cost"] = round(cost, 4)
    except Exception as e:
        trace["error"] = f"classify: {str(e)[:80]}"
        return [], trace
    trace["classified"] = len(picked)
    final = _drop_contained(picked)
    trace["kept"] = len(final)
    return final, trace

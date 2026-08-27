#!/usr/bin/env python3
"""Discover opportunities by MINING hub pages — program indexes that never rank in search.

Phase 4 of the scraper v2 plan. A hub is a page that LISTS many programs (a university
pre-college index, a city Parks & Rec teen page, even an SEO listicle). Search-first discovery
misses these; hub-first discovery is the opposite — one page yields many real programs, and a
followed link is real BY CONSTRUCTION, so no per-search fee and no grounding needed.

TWO-STAGE AUDIENCE FILTER (both FREE) is the accuracy design, proven by the 2026-08-26 pilot
where a bare Wisconsin homepage gave 40 links with 2 gems:
  1. anchor-level: drop links whose anchor text is plainly the wrong audience (elementary/
     middle/graduate/PhD/MBA/faculty/alumni/parents/admitted-students) — cuts most chaff free.
  2. page-level: fetch each surviving target and require high-school-audience words on the page.
Plus one-level sub-hub recursion (an anchor like "Pre-College Programs" is itself a hub) and
hard caps, so this can never wander into crawling the open web.

COST: the harvest, both filters, recursion and catalog dedup are ALL FREE (plain HTTP + regex).
The ONLY paid step is extraction — one no-search model call per surviving page (~$0.003), page
in / JSON out, because the URL is already real. `--preview` stops before ANY model call and
prints what WOULD be extracted, at zero cost. A live run spends real money and — like every paid
agent here — needs fresh explicit approval per run. Rows land is_active=false,
source='hub-<domain>-<date>', found_via=<hub url>; nothing reaches students without a human yes.

    python mine_hub_pages.py --hubs https://ceismc.gatech.edu/programs --preview   # FREE
    python mine_hub_pages.py --hubs-file seattle_hubs.json --preview               # FREE
    python mine_hub_pages.py --hubs https://ceismc.gatech.edu/programs             # PAID (gated)
"""
import argparse
import datetime
import html
import json
import os
import re
import urllib.parse

import page_text
import url_dedupe
import url_repair
import url_validate
from scrape_opportunities import build_row

# --- pure audience/relevance filters (free, unit-tested) --------------------------------
_WRONG_AUDIENCE = re.compile(
    r"\b(elementary|middle[\s-]?school|kindergarten|grades?\s*[k1-8](?!\d)|"
    r"graduate|grad\s+student|ph\.?d|doctoral|post[\s-]?doc|mba|master'?s|"
    r"faculty|staff|alumni|parents?|guardians?|educators?|teachers?|"
    r"admitted\s+students?|current\s+students?|undergraduate|college\s+students?|"
    r"professional\s+development|continuing\s+education)\b", re.I)
_HS_AUDIENCE = re.compile(
    r"\b(high[\s-]?school|grades?\s*9|grades?\s*1[012]|9th|10th|11th|12th|"
    r"rising\s+(junior|senior|sophomore)s?|teens?|teenagers?|secondary\s+school|"
    r"pre[\s-]?college|precollege|young\s+scholars?)\b", re.I)
# An index anchor is generic and usually PLURAL ("Summer Programs", "Pre-College Programs") or
# carries an explicit index word; a singular "Summer Program" is one program, not a list, so it
# must NOT be routed to recursion.
_SUB_HUB = re.compile(
    r"\b(pre[\s-]?college(\s+programs?)?|precollege|"
    r"summer\s+programs\b|youth\s+programs\b|our\s+programs\b|all\s+programs\b|"
    r"explore\s+programs?|programs?\s+(index|list|directory|catalog|catalogue|finder)|"
    r"k[\s-]?12(\s+programs?)?)\b", re.I)


# Slugs that are page furniture, never a program's own page. Reuses url_repair's vetted
# GENERIC_SLUGS (apply/faq/about/contact/donate/news/admissions/resources/index/...) plus the
# extra nav labels the Aug-27 real-index preview surfaced as chaff (USNA Admissions index,
# UW-Madison pre-college footer). Kept conservative: a real program slug (stem, nass,
# business-emerging-leaders, badger-summer-scholars, summer-music-clinic) is in none of these.
_NAV_SLUGS = url_repair.GENERIC_SLUGS | {
    "events", "event", "blog", "blogs", "partners", "partner", "employment", "jobs",
    "faqs", "in-the-news", "news-events", "financial-information", "financial", "tuition",
    "safety", "safety-commitment", "culture-belonging", "prospective-students",
    "current-students", "student-life", "visit", "quicklinks", "contact-us", "about-us",
    "our-team", "team", "history", "mission", "accessibility", "privacy", "terms",
    "gallery", "photos", "media", "international-student-resources", "employment-opportunities",
}
# A non-HTML target (a viewbook PDF, a flyer image) can never be a program's landing page.
_NONHTML_EXT = (".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".zip",
                ".jpg", ".jpeg", ".png", ".gif", ".mp4", ".mov", ".webp", ".svg", ".bmp",
                ".tiff", ".avif", ".mp3", ".wav")
# Path segments that mark a page never worth an extraction call, even when the SLUG is a
# real-looking title. Two classes surfaced by the Aug-27 preview:
#  - adult/degree: online.wisc.edu/degrees/marketing leaked past the anchor filter because its
#    anchor text was just "Marketing".
#  - editorial: /blog/<post-title> and /news/<headline> — the slug is the article title, so the
#    nav-slug check (which only sees the last segment) never catches them.
_ADULT_PATH_SEGMENTS = {"degrees", "degree", "graduate", "phd", "mba", "faculty", "alumni"}
_EDITORIAL_PATH_SEGMENTS = {"blog", "blogs", "news", "in-the-news", "press", "press-releases",
                            "stories", "story", "article", "articles", "media"}
_DROP_PATH_SEGMENTS = _ADULT_PATH_SEGMENTS | _EDITORIAL_PATH_SEGMENTS


def is_nonprogram_link(url, hub_url):
    """True if a harvested link is the hub's own page, a non-HTML file, page nav, an
    adult/degree page, or an editorial post — i.e. never worth a paid extraction call.
    Pure, free, pre-fetch."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return True
    if url_dedupe.match_key(url) == url_dedupe.match_key(hub_url):
        return True                       # the hub links to itself
    path = (parts.path or "").lower()
    if path.endswith(_NONHTML_EXT):
        return True
    segs = {s for s in path.split("/") if s}
    if segs & _DROP_PATH_SEGMENTS:
        return True
    slug = url_repair._slug(url)
    return (not slug) or slug in _NAV_SLUGS


def is_wrong_audience(text):
    """True if the anchor/text is plainly for a non-high-school audience."""
    return bool(_WRONG_AUDIENCE.search(text or ""))


def has_hs_audience(text):
    """True if the page text names a high-school audience (grades 9-12, teens, pre-college)."""
    return bool(_HS_AUDIENCE.search(text or ""))


def looks_like_sub_hub(anchor):
    """True if this anchor points at ANOTHER index worth recursing into once."""
    return bool(_SUB_HUB.search(anchor or ""))


def harvest_links(page_html, base_url):
    """[(absolute_url, anchor_text)] from a page's <a> tags, deduped by URL, order preserved."""
    out, seen = [], set()
    for href, raw_label in url_repair._LINK_RE.findall(page_html or ""):
        try:
            url = urllib.parse.urljoin(base_url, html.unescape(href.strip())).split("#")[0]
        except ValueError:
            continue
        if not url.lower().startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        out.append((url, url_repair._text(raw_label)[:200]))
    return out


def filter_hub_links(links, hub_url, off_domain=False, cap=25):
    """Anchor-level filter (stage 1). Returns program-candidate links, best-effort ordered.

    off_domain=False (institutional hub): keep only same-registrable-domain links — an
    institution's index links out to sponsors and news, which are not its programs.
    off_domain=True (listicle hub): keep only OFF-domain links — a listicle's on-domain links
    are its own nav/signup; the programs are the outbound ones. Following the wrong kind is how
    a miner turns into a web crawler, so the caller declares which per hub.
    """
    hub_dom = url_dedupe.registrable_domain(urllib.parse.urlsplit(hub_url).netloc)
    kept, subs = [], []
    for url, anchor in links:
        if is_wrong_audience(anchor):
            continue
        if url_validate.is_bare_domain(url) or url_validate.is_content_mill(url):
            continue
        if is_nonprogram_link(url, hub_url) and not looks_like_sub_hub(anchor):
            continue          # page nav / PDF / degree page — but keep a labelled sub-hub index
        dom = url_dedupe.registrable_domain(urllib.parse.urlsplit(url).netloc)
        same = dom == hub_dom
        if off_domain and same:
            continue
        if not off_domain and not same:
            continue
        if looks_like_sub_hub(anchor):
            subs.append((url, anchor))
        else:
            kept.append((url, anchor))
        if len(kept) >= cap:
            break
    return kept, subs


def fetch_html(url, timeout=url_repair.DEFAULT_TIMEOUT):
    page, _final = url_repair._fetch(url, timeout)
    return page or ""


_EXTRACT_SYSTEM = (
    "You extract ONE high-school extracurricular opportunity from the text of its own web "
    "page. Return a single JSON object with keys: name, org, summary, type, price, state, "
    "location, intl, season, eligibility, grade_min, grade_max, subject_tags (array), "
    "contact_email. Use ONLY what the page says; use null for anything the page does not "
    "state. Do NOT invent a URL. If the page is not a single high-school program (a list, a "
    "news article, a graduate/adult program), return {\"name\": null}.")


def extract_opportunity(url, key, timeout=40, min_delay=5):
    """PAID: one no-search model call, page text in -> JSON out. Returns (candidate|None, cost).

    No grounding is needed — the URL is real by construction, so unlike the search scraper this
    is a single call. Kept parallel to scrape_opportunities.extract_candidates."""
    from gemini_common import call_gemini, extract_json, estimate_cost, set_min_delay
    set_min_delay(min_delay)
    text, _reason = page_text.fetch_page_text(url, timeout)
    if not text:
        return None, 0.0
    user = f"PAGE URL: {url}\n\nPAGE TEXT:\n{text[:14000]}\n\nReturn the JSON object now."
    out, usage = call_gemini(_EXTRACT_SYSTEM, user, key, use_web_search=False,
                             max_tokens=1500, timeout=timeout)
    cost = estimate_cost(usage)
    cand = extract_json(out)
    if not isinstance(cand, dict) or not (cand.get("name") or "").strip():
        return None, cost
    return cand, cost


def discover(hub_url, off_domain=False, timeout=url_repair.DEFAULT_TIMEOUT, recurse=True):
    """FREE: the program-page URLs a hub yields, after both filter stages and one-level recursion.

    Returns (candidate_urls, trace) where trace explains what was dropped and why. No model calls.
    """
    trace = {"hub": hub_url, "harvested": 0, "after_anchor_filter": 0, "sub_hubs": 0,
             "dropped_no_hs_audience": 0, "kept": 0}
    html = fetch_html(hub_url, timeout)
    if not html:
        trace["error"] = "hub page could not be fetched"
        return [], trace
    links = harvest_links(html, hub_url)
    trace["harvested"] = len(links)
    kept, subs = filter_hub_links(links, hub_url, off_domain=off_domain)
    trace["sub_hubs"] = len(subs)
    if recurse:
        for sub_url, _anchor in subs[:3]:            # one level, at most 3 sub-hubs
            sub_html = fetch_html(sub_url, timeout)
            sub_kept, _ = filter_hub_links(harvest_links(sub_html, sub_url), sub_url,
                                           off_domain=off_domain)
            kept.extend(sub_kept)
    # de-dupe the merged candidate set by normalized URL
    seen, cand = set(), []
    for url, anchor in kept:
        k = url_dedupe.match_key(url)
        if k in seen:
            continue
        seen.add(k)
        cand.append((url, anchor))
    trace["after_anchor_filter"] = len(cand)
    # stage 2: page-level high-school-audience check (free fetch of each target)
    final = []
    for url, anchor in cand:
        text, _reason = page_text.fetch_page_text(url, timeout)
        if text and not has_hs_audience(text) and not has_hs_audience(anchor):
            trace["dropped_no_hs_audience"] += 1
            continue
        final.append(url)
    trace["kept"] = len(final)
    return final, trace


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hubs", nargs="+", help="Hub page URL(s).")
    ap.add_argument("--hubs-file", help="JSON file: [{\"url\":..., \"off_domain\": bool}, ...].")
    ap.add_argument("--off-domain", action="store_true",
                    help="Treat hubs as listicles: follow OFF-domain links, not same-domain.")
    ap.add_argument("--preview", action="store_true",
                    help="FREE: discover + dedup against the catalog, print candidates, make NO "
                         "model call and write nothing.")
    ap.add_argument("--mode", default="national")
    ap.add_argument("--min-delay", type=int, default=5)
    ap.add_argument("--timeout", type=int, default=40)
    args = ap.parse_args()

    hubs = []
    if args.hubs_file:
        with open(args.hubs_file, encoding="utf-8") as f:
            hubs = [(h["url"], bool(h.get("off_domain"))) for h in json.load(f)]
    hubs += [(u, args.off_domain) for u in (args.hubs or [])]
    if not hubs:
        print("[ERROR] Give --hubs or --hubs-file.")
        raise SystemExit(1)

    from supabase_common import load_dotenv, supabase_get
    load_dotenv()
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    service_key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_ANON_KEY")
    gemini_key = os.environ.get("GEMINI_API_KEY")
    existing = supabase_get(supabase_url, "opportunities", {"select": "id,name,url"},
                            service_key) if supabase_url else []

    all_new = []
    for hub_url, off_domain in hubs:
        urls, trace = discover(hub_url, off_domain=off_domain, timeout=args.timeout)
        # dedup against the catalog BEFORE any model call — a followed link we already have is free
        # to skip and must never be re-extracted.
        fresh = []
        for u in urls:
            exact, _ = url_dedupe.find_duplicates(u, "", existing)
            if not exact:
                fresh.append(u)
        print(f"[HUB] {hub_url}: harvested {trace['harvested']}, after audience filter "
              f"{trace['after_anchor_filter']}, kept {trace['kept']}, new (not in catalog) "
              f"{len(fresh)}." + (f"  ERROR: {trace['error']}" if trace.get("error") else ""))
        for u in fresh:
            print(f"    candidate: {u}")
        all_new.append((hub_url, fresh))

    total = sum(len(f) for _, f in all_new)
    if args.preview:
        print(f"\n[PREVIEW] {total} new candidate page(s) across {len(hubs)} hub(s). No model "
              f"call, no writes. A live run extracts each (~$0.003/page) and needs approval.")
        return

    if not gemini_key:
        print("[ERROR] GEMINI_API_KEY not set — cannot extract. (Preview is free without it.)")
        raise SystemExit(1)
    # PAID PATH — extraction. Reached only on an explicit (approved) live run.
    today = datetime.date.today().strftime("%Y%m%d")
    rows, cost = [], 0.0
    for hub_url, fresh in all_new:
        dom = url_dedupe.registrable_domain(urllib.parse.urlsplit(hub_url).netloc) or "hub"
        source = f"hub-{dom}-{today}"
        for u in fresh:
            cand, c = extract_opportunity(u, gemini_key, timeout=args.timeout,
                                          min_delay=args.min_delay)
            cost += c
            if not cand:
                continue
            row = build_row(cand, cand.get("id") or f"hub-{len(rows)}", source, u, [])
            if row:
                row["found_via"] = hub_url
                rows.append(row)
    print(f"[SUMMARY] extracted {len(rows)} row(s) from {total} page(s), cost ${cost:.4f}. "
          f"Rows are is_active=false and need console activation.")
    # Insert is intentionally left to the operator's console flow / a follow-up, mirroring the
    # scraper: this script proves the discovery and prices the extraction; wiring the insert is a
    # separate approved step so a hub run cannot silently write the catalog.


if __name__ == "__main__":
    main()

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
Paid work for a page that clears the refusal gate is the SAME per-candidate gate the search
scraper runs (MARQUEE M9), so a hub-mined row reaches the review queue CLASSIFIED and LABELLED
exactly like a scraper row, whatever its source (2026-09-02). Four no-search calls on the ONE
fetched page (~$0.004 total, page in / JSON out, URL real by construction — see
`extract_opportunity`):
  1. refusal + `state`  — decides it IS a single high-school program and gets the one field
     refresh's schema does not cover; a page it refuses never pays for the rest.
  2. classify_page      — the page CLASSIFICATION (program / hub / none, confidence, staleness),
     attached as the same `classify:` pill the scraper's discovery gate writes. Advisory here
     (call 1 is the extraction gate): it LABELS, it never drops.
  3. metadata           — refresh_opportunities' own `build_system`/`clean_update_dict`, unchanged,
     so the row is filled as richly as a `agents/refresh_opportunities.py` pass would fill it.
  4. embedding dedupe   — cosine nearest in the catalog `dedupe_vector` index (dedupe_embed_store),
     the SAME embedding dedupe the scraper attaches, merged into `dup_candidates` as a HINT.
`--preview` stops before ANY model call and prints what WOULD be extracted, at zero cost. A live
run spends real money and — like every paid agent here — needs fresh explicit approval per run.
Rows land is_active=false, source='hub-<domain>-<date>', found_via=<hub url>; nothing reaches
students without a human yes. (When embeddings are (re)computed, see MARQUEE_DECISIONS.md M9:
dedupe + semantic vectors are written at ACTIVATION and on a metadata-changing refresh — the
hub-miner run only READS the dedupe index for a hint, it does not write either vector.)

    python -m agents.mine_hub_pages --hubs https://ceismc.gatech.edu/programs --preview   # FREE
    python -m agents.mine_hub_pages --hubs-file seattle_hubs.json --preview               # FREE
    python -m agents.mine_hub_pages --hubs https://ceismc.gatech.edu/programs             # PAID (gated)
"""
import argparse
import datetime
import html
import json
import os
import re
import urllib.parse

from wingman import combined_reader
from wingman import classify_page
from wingman import dedupe_embed_store
from wingman import embed_common
from wingman import page_text
from wingman import queue_flags
from wingman import url_dedupe
from wingman import url_repair
from wingman import url_validate
from wingman.agent_common import safe_console, snapshot_stamp
from agents.scrape_opportunities import (build_row, next_id_generator, insert_rows, VALID_TYPES,
                                  collapse_intra_run_twins, gate_dup_candidates,
                                  FLAG_BARE_DOMAIN, FLAG_LOW_VALUE, FLAG_OFFSITE, FLAG_NO_TYPE)
from wingman import REPO_ROOT   # the repo root, defined once (see wingman/__init__.py)

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
    "wp-admin", "wp-login", "wp-json",   # WordPress internals — never a program page
    # A signup form is not a program. CMU's pre-college index links
    # apply-precollege.studentaffairs.cmu.edu/register/join-our-mailing-list, which passes every
    # other filter (same registrable domain, deep path, high-school wording) and would have been
    # extracted and inserted as a row.
    "join-our-mailing-list", "mailing-list", "newsletter", "subscribe",
    # Measured on the first three walk-up previews (Columbia, Vanderbilt, Seattle Parks): pages
    # ABOUT a program set rather than a program. Each is a leaf slug, so a real program sitting
    # under /programs/ or /admissions/ is untouched -- only the page itself is dropped.
    "frequently-asked-questions", "program-costs", "costs", "cost", "fees", "tuition-and-fees",
    "program-policies", "policies", "policy", "compare-programs", "explore-courses",
    "publications", "publications-and-funding", "funding", "grants", "grants-collaborations",
    "work-with-us", "partnerships", "successful-partnerships", "partnership-opportunities",
    "map", "maps", "projects", "viewform",
    # Inquiry/application-material forms, surfaced by the 2026-08-28 Columbia re-preview:
    # /register/pre-college-rfi (a request-for-information form) and
    # /applying-pre-college-programs/application-materials both passed every other filter and
    # would have cost a refusal each. Exact leaves here; the program-name-prepended shapes
    # (pre-college-rfi) are caught by _NAV_LEAF_SUFFIXES below.
    "application-materials", "request-info", "request-information", "rfi", "inquiry",
    "info-session", "information-session", "how-to-apply", "checklist",
    # NOT here, deliberately: anything naming a scholarship or financial aid. The operator wants
    # SCHOLARSHIPS in the catalog (2026-08-28), so a page at /scholarships-and-financial-aid is a
    # lead, not chaff -- and this list existing at all is why that had to be caught by hand
    # rather than by a rule. Anything added here must be a page nobody would ever want as a row.
}
# Furniture tails that mark an inquiry/application form even when the program name is prepended
# to the leaf ('pre-college-rfi', 'summer-info-session'). Matched as a leaf suffix, the same way
# _NAV_SLUGS matches an exact leaf — a program is never named after one of these.
_NAV_LEAF_SUFFIXES = ("-rfi", "-request-info", "-request-information", "-inquiry",
                      "-info-session", "-information-session", "-application-materials",
                      "-how-to-apply")
# A non-HTML target (a viewbook PDF, a flyer image) can never be a program's landing page.
_NONHTML_EXT = (".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".zip",
                ".jpg", ".jpeg", ".png", ".gif", ".mp4", ".mov", ".webp", ".svg", ".bmp",
                ".tiff", ".avif", ".mp3", ".wav", ".xml")   # .xml: seattle.gov links x58902.xml
# Path segments that mark a page never worth an extraction call, even when the SLUG is a
# real-looking title. Two classes surfaced by the Aug-27 preview:
#  - adult/degree: online.wisc.edu/degrees/marketing leaked past the anchor filter because its
#    anchor text was just "Marketing".
#  - editorial: /blog/<post-title> and /news/<headline> — the slug is the article title, so the
#    nav-slug check (which only sees the last segment) never catches them.
_ADULT_PATH_SEGMENTS = {"degrees", "degree", "graduate", "phd", "mba", "faculty", "alumni"}
_EDITORIAL_PATH_SEGMENTS = {"blog", "blogs", "news", "in-the-news", "press", "press-releases",
                            "stories", "story", "article", "articles", "media"}
# Civic/nonprofit local sites (libraries, museums, city depts) link mostly to branch LOCATIONS
# and service categories, not programs. Measured 2026-08-27 on a Seattle registry: SPL's teen
# page yielded 23 /hours-and-locations/<branch> pages and KCLS yielded /ebooks//volunteer/.
# These are the local analogue of a university's nav chaff.
_CIVIC_PATH_SEGMENTS = {"hours-and-locations", "hours-locations", "locations", "location",
                        "hours", "branches", "directions", "parking", "rentals", "donate",
                        "ebooks", "audiobooks", "databases", "catalog"}
_DROP_PATH_SEGMENTS = _ADULT_PATH_SEGMENTS | _EDITORIAL_PATH_SEGMENTS | _CIVIC_PATH_SEGMENTS
# Hosts that are never a program's OWN page — social networks, share widgets, commerce, and
# email-list signup providers. In off-domain (listicle) mode these leak heavily: measured
# 2026-08-27 a CS-programs blog linked out to its own Facebook/Twitter/TikTok/LinkedIn, an NYU
# Mailchimp signup (list-manage.com), and an Amazon book. Matched on the registrable domain.
# (is_content_mill already covers youtube/reddit/wikipedia; this is the social/commerce/list
# complement, kept here so the graded scraper's mill list is untouched.)
_NONPROGRAM_HOSTS = {
    "twitter.com", "x.com", "facebook.com", "fb.com", "instagram.com", "tiktok.com",
    "linkedin.com", "pinterest.com", "threads.net", "snapchat.com", "whatsapp.com",
    "t.me", "telegram.me", "amazon.com", "list-manage.com", "mailchi.mp", "eventbrite.com",
    # Link shorteners, booking pages and form SaaS. A round-up's own funnel runs through these,
    # and none of them can ever BE a program's page: measured on a ladderinternships round-up,
    # which offered bit.ly, calendly and airtable links among its candidates. A shortener is also
    # opaque -- we would pay to extract whatever it happens to point at today.
    "bit.ly", "tinyurl.com", "goo.gl", "ow.ly", "buff.ly", "lnkd.in", "rebrand.ly",
    "calendly.com", "airtable.com", "typeform.com", "jotform.com", "surveymonkey.com",
    "forms.gle", "wufoo.com", "formstack.com", "smartsheet.com",
    # NOT docs.google.com: hosts match on the REGISTRABLE domain, so it would collapse to
    # google.com and take "Doodle for Google" -- a real catalog row -- with it. A Google Form is
    # caught by its /viewform leaf instead.
}


def is_nonprogram_link(url, hub_url):
    """True if a harvested link is the hub's own page, a social/commerce/list-signup host, a
    non-HTML file, page nav, an adult/degree page, an editorial post, or a page whose URL names
    a non-high-school audience — i.e. never worth a paid extraction call. Pure, free, pre-fetch."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return True
    try:
        if url_dedupe.match_key(url) == url_dedupe.match_key(hub_url):
            return True                   # the hub links to itself
    except ValueError:
        # A broken page yields hrefs that are not URLs at all — measured live, one carried
        # `... A Home That Builds Multitudes<` where a port should be, and urlsplit raises
        # while parsing it. A junk href is not a program link, and a single bad anchor must
        # never take down the harvest of the whole page.
        return True
    if "{{" in url or "}}" in url or "%7b%7b" in url.lower():
        return True                       # an unrendered template placeholder (e.g. kcls .../{{url}})
    host = (parts.hostname or "").lower()
    if url_dedupe.registrable_domain(host) in _NONPROGRAM_HOSTS:
        return True                       # social / share / commerce / list-signup host
    path = (parts.path or "").lower()
    if path.endswith(_NONHTML_EXT):
        return True
    segs = {s for s in path.split("/") if s}
    if segs & _DROP_PATH_SEGMENTS:
        return True
    # Wrong audience named only in the URL — the anchor text may omit it. Measured: Stanford's
    # "…/stanford-middle-school-scholars-program" whose anchor read "Scholars Program" passed the
    # anchor-level is_wrong_audience but its slug names the middle-school audience.
    if _WRONG_AUDIENCE.search(re.sub(r"[/_-]+", " ", path)):
        return True
    slug = url_repair._slug(url)
    return (not slug) or slug in _NAV_SLUGS or slug.endswith(_NAV_LEAF_SUFFIXES)


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


def filter_hub_links(links, hub_url, off_domain=False, cap=25, with_dropped=False):
    """Anchor-level filter (stage 1). Returns program-candidate links, best-effort ordered.

    off_domain=False (institutional hub): keep only same-registrable-domain links — an
    institution's index links out to sponsors and news, which are not its programs.
    off_domain=True (listicle hub): keep only OFF-domain links — a listicle's on-domain links
    are its own nav/signup; the programs are the outbound ones. Following the wrong kind is how
    a miner turns into a web crawler, so the caller declares which per hub.
    """
    hub_dom = url_dedupe.registrable_domain(urllib.parse.urlsplit(hub_url).netloc)
    hub_path = urllib.parse.urlsplit(hub_url).path.rstrip("/")
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
    # EVERY link is judged before the cap applies. It used to `break` at `cap` survivors, which
    # truncated by POSITION IN THE PAGE -- and a page's first links are its chrome. Measured on
    # seattle.gov/parks/childcare/teen-programs/ (974 links): the cap filled with navigation and
    # the real programs, further down, were never looked at. `career-explorations` passes every
    # filter here and was never reached, so the hub reported "0 real programs" and that was an
    # artifact of the cap, not a fact about the site. Judging a link is pure and free -- only
    # stage 2 fetches and only extraction pays -- so there was never a reason to stop early.
    kept.sort(key=lambda ua: 0 if urllib.parse.urlsplit(ua[0]).path.startswith(hub_path)
              else 1)
    dropped = max(0, len(kept) - cap)
    # `(kept, subs)` stays the default shape so no existing caller had to change; a caller that
    # reports coverage asks for the third value. Same opt-in the repo uses for
    # call_gemini(return_grounding=True) and page_text.fetch_page_text_resolved.
    return (kept[:cap], subs, dropped) if with_dropped else (kept[:cap], subs)


def fetch_html(url, timeout=url_repair.DEFAULT_TIMEOUT):
    page, _final = url_repair._fetch(url, timeout)
    return page or ""


_EXTRACT_SYSTEM = (
    "You extract ONE high-school extracurricular opportunity from the text of its own web "
    "page. Return a single JSON object with keys: name, org, summary, type, price, state, "
    "location, intl, season, eligibility, grade_min, grade_max, subject_tags (array), "
    "contact_email, running, running_reason. Use ONLY what the page says; use null for "
    "anything the page does not state. Do NOT invent a URL. If the page is not a single "
    "high-school program (a list, a news article, a graduate/adult program), return "
    "{\"name\": null}.\n"
    # `type` is a closed set on the catalog row. Naming the key without naming its values
    # made the model answer in its own vocabulary ("Summer Program"), which build_row parks
    # on a placeholder while the caller attaches FLAG_NO_TYPE — measured at 19 of 19 rows on
    # the first live run, i.e. every row needed a human to set a field the page states plainly.
    "`type` MUST be exactly one of: " + ", ".join(sorted(VALID_TYPES)) + ". Choose the "
    "closest one; never invent another word and never leave it null.\n"
    # running / running_reason: the discontinuation signal. A false NEGATIVE (a dead program
    # called running) just wastes a reviewer's glance; a false POSITIVE (a live program called
    # dead) is DELETED from discovery with no reviewer to catch it — see queue_flags. So the
    # examples below all bias the same way: default true, fire false ONLY on explicit page
    # evidence. House style: define the term with concrete good/bad examples, never adjectives.
    "`running` is a boolean: is this program STILL OFFERED? Default it to TRUE. Set it FALSE "
    "ONLY when the page ITSELF shows the program is over, and put the deciding sentence in "
    "`running_reason`. Exactly three things make it false: (a) the page says the program is "
    "discontinued, cancelled, retired, or no longer offered — e.g. 'This program has been "
    "discontinued' -> running=false, running_reason=\"page: 'This program has been "
    "discontinued'\"; (b) applications are PERMANENTLY closed with no future cycle — e.g. "
    "'Applications are closed and will not reopen' -> running=false; (c) the most recent cycle "
    "the page NAMES has already passed AND no later cycle is announced — e.g. 'Program dates: "
    "June 2022' with nothing later and no open application -> running=false, running_reason="
    "\"newest cycle June 2022, no future cycle or open application\". Set it TRUE when the page "
    "shows or implies a current or upcoming cycle — e.g. 'Summer 2026 applications open now' -> "
    "running=true — and TRUE whenever the page gives no timing at all: a missing date is NOT "
    "evidence a program ended. 'Deadline has passed' for a cycle that recurs is NOT discontinued "
    "— that is a closed application for a live program, running=true. Never infer from outside "
    "the page. When running=true, set running_reason to null.")


def extract_opportunity(url, key, index=None, timeout=40, min_delay=5):
    """PAID: the hub-miner's full page gate on ONE fetched page — the SAME classify + metadata +
    embedding-dedupe the search scraper runs, so a hub-mined row reaches the review queue labelled
    identically. Returns (candidate|None, cost, classification|None, dup_hints).

    Four no-search model/embedding calls on the ONE fetched text (a page call 1 refuses pays for
    nothing after it):
      1. `_EXTRACT_SYSTEM`  — the REFUSAL decision ("not a single high-school program" ->
         {"name": null}) and `state`, the one field refresh's schema does not ask for.
      2. `classify_page`    — the page CLASSIFICATION (program/hub/none, confidence, staleness),
         which the caller attaches as the same `classify:` pill the scraper's discovery gate writes.
         ADVISORY here — call 1 is the extraction gate — so it LABELS, it never drops (the hub
         miner reviews everything; the pill just tells the reviewer what the classifier thinks,
         staleness included).
      3. metadata           — `refresh_opportunities.build_system`/`clean_update_dict` UNCHANGED
         (via `combined_reader.extract_metadata`, no second fetch), so the row is filled as richly
         as a `agents/refresh_opportunities.py` pass would. `cost` (refresh's key) is merged in as
         `cost_detail` since refresh's schema has no separate free/paid enum to collide with.
      4. embedding dedupe   — when `index` is given, cosine nearest in the catalog dedupe_vector
         index (the SAME embedding dedupe the scraper attaches), returned as `dup_hints` for the
         caller to merge into `dup_candidates`. A HINT only; it never rejects.

    The URL is real by construction (a followed link), so no call needs grounding.
    """
    from wingman.gemini_common import call_gemini, extract_json, estimate_cost, set_min_delay
    set_min_delay(min_delay)
    text, _reason = page_text.fetch_page_text(url, timeout)
    if not text:
        return None, 0.0, None, []
    user = f"PAGE URL: {url}\n\nPAGE TEXT:\n{text[:14000]}\n\nReturn the JSON object now."
    out, usage = call_gemini(_EXTRACT_SYSTEM, user, key, use_web_search=False,
                             max_tokens=1500, timeout=timeout)
    cost = estimate_cost(usage)
    cand = extract_json(out)
    if not isinstance(cand, dict) or not (cand.get("name") or "").strip():
        return None, cost, None, []

    # 2. page classification -> the classify: pill (label + staleness signal), the same one the
    # scraper's discovery gate attaches. Runs only for a page call 1 accepted, so we never pay to
    # classify a refused page. classify_from_text banks its own cost before parsing.
    classify_call = lambda system, u: call_gemini(system, u, key, use_web_search=False,
                                                  max_tokens=800, timeout=timeout)
    classification = classify_page.classify_from_text(
        url, text, classify_call, name_hint=cand.get("name", ""), org_hint=cand.get("org", ""))
    cost += classification.cost or 0.0

    # 3. metadata overlay (refresh's own extraction, on the text we already hold).
    metadata_call = lambda system, u: call_gemini(
        system, u, key, use_web_search=False, timeout=timeout,
        max_tokens=combined_reader._METADATA_MAX_TOKENS, model=combined_reader._METADATA_MODEL)
    metadata, mcost = combined_reader.extract_metadata(
        url, text, metadata_call, name_hint=cand.get("name", ""), org_hint=cand.get("org", ""))
    cost += mcost
    for k, v in metadata.items():
        if v is None:
            continue
        cand["cost_detail" if k == "cost" else k] = v

    # 4. embedding dedupe hint — cosine nearest in the catalog dedupe_vector index. Uses the row's
    # own fields representation (default_representation), the SAME text the index was built from, so
    # a hit means the same program at a URL url_dedupe's name/URL match could not see.
    dup_hints = []
    if index:
        rep = combined_reader.default_representation(cand, text)
        embed_fn = lambda t: embed_common.embed_text(t, key, timeout=timeout or 60)
        dup_hints, dcost = combined_reader.dedup_hint(rep, embed_fn, index)
        cost += dcost
    return cand, cost, classification, dup_hints


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
    kept, subs, over_cap = filter_hub_links(links, hub_url, off_domain=off_domain,
                                           with_dropped=True)
    if over_cap:
        # Never a silent truncation: a bounded sweep reported as a total reads as complete
        # coverage when it is not. Raise --cap to reach them.
        trace["over_cap"] = over_cap
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
    # stage 2: page-level high-school-audience check (free fetch of each target).
    #
    # This is also where a candidate is resolved to the page it actually LANDS on, and both
    # checks below exist because of one measured run. CMU's pre-college index links nine of its
    # programs twice -- once at /pre-college/... and once at /student-affairs/pre-college/... --
    # and every one of the second set 302s back to the index itself. Deduping on the requested
    # URL, this hub offered 24 candidates; nine were one page wearing nine addresses and would
    # have been extracted, paid for, and inserted as nine rows saying the same thing.
    #
    # The fetch already happens here, so the final URL is free -- it was simply being discarded.
    final, landed = [], set()
    hub_key = url_dedupe.match_key(hub_url)
    hub_parts = urllib.parse.urlsplit(hub_url)
    hub_segments = [seg for seg in hub_parts.path.split("/") if seg]

    def _lands_above_the_hub(landing):
        """True when the redirect ended on the hub itself or on a section ABOVE it.

        A program page is never an ancestor of the index that lists it, so this cannot drop a
        real find -- and it catches the soft 404 that a bare `== hub` check misses. Measured on
        CMU: /student-affairs/pre-college/academic-programs/<program>.html redirects to
        /pre-college/, which is the parent of the hub being mined rather than the hub itself.
        """
        try:
            parts = urllib.parse.urlsplit(landing or "")
        except ValueError:
            return False
        if parts.netloc != hub_parts.netloc:
            return False
        segments = [seg for seg in parts.path.split("/") if seg]
        return len(segments) <= len(hub_segments) and hub_segments[:len(segments)] == segments
    for url, anchor in cand:
        text, _reason, landing = page_text.fetch_page_text_resolved(url, timeout)
        if text and not has_hs_audience(text) and not has_hs_audience(anchor):
            trace["dropped_no_hs_audience"] += 1
            continue
        land_key = url_dedupe.match_key(landing or url)
        if land_key == hub_key or _lands_above_the_hub(landing):
            # It redirected onto the hub, or onto a section above it. There is no program page
            # at the other end, and extracting it would describe an index rather than a program.
            trace["redirects_to_hub"] = trace.get("redirects_to_hub", 0) + 1
            continue
        if land_key in landed:
            trace["same_page_twice"] = trace.get("same_page_twice", 0) + 1
            continue
        landed.add(land_key)
        final.append(url)
    trace["kept"] = len(final)
    return final, trace


def allocate_budget(all_new, max_pages):
    """Spend a run's extraction budget ACROSS the hubs, not down the list. Pure.

    Returns (all_new_trimmed, [(hub_url, n_skipped), ...]).

    First-come allocation looked fine and was not. Measured on the 43-hub run: the ceiling of 300
    fell entirely on the last hubs in the file, which happened to be all ten walk-up leads -- so
    the round-ups took 248 extractions and the walk-ups 52, with 46 candidates dropped. Brown, BU,
    Vanderbilt and UCSF reported zero rows because they NEVER RAN, which reads exactly like a hub
    that yielded nothing. That is the same failure as a link cap that truncates by position in the
    page, one level up: a cap must bound the cost, never silently choose the winners.

    Round-robin means a ceiling degrades every hub by a similar fraction, and a hub with fewer
    candidates than its share is never trimmed at all.
    """
    if max_pages is None or sum(len(f) for _, f in all_new) <= max_pages:
        return all_new, []
    take = {u: 0 for u, _ in all_new}
    remaining, budget = {u: len(f) for u, f in all_new}, max_pages
    while budget > 0 and any(remaining[u] > take[u] for u in take):
        for u in take:
            if budget <= 0:
                break
            if take[u] < remaining[u]:
                take[u] += 1
                budget -= 1
    trimmed, capped = [], []
    for u, fresh in all_new:
        if take[u] < len(fresh):
            capped.append((u, len(fresh) - take[u]))
        trimmed.append((u, fresh[:take[u]]))
    return trimmed, capped


def fresh_candidates(urls, catalog_keys, seen_this_run):
    """(fresh, already_in_catalog, seen_earlier_this_run) -- what is worth PAYING to extract.

    URL-only, and that is the point: nothing has been extracted yet, so there is no name to match
    on. `url_dedupe.find_duplicates(u, "")` cannot help here -- its exact rule is "same normalized
    URL AND similar name" by design, so an empty name always falls through to a hint, and the
    caller ignored the hint. The result was a check that suppressed NOTHING while its comment
    said it deduped against the catalog: mining CMU's index inserted 14 rows of which 12 were
    pages the catalog already held, and the walk-up lead had predicted exactly that ("links 12
    program(s) we already have").

    URL-only is safe HERE in a way it would not be at the scraper's insert layer, where one
    application portal legitimately backs six different programs. We are following a link: if a
    row already sits at this exact URL, re-reading the page cannot produce anything the catalog
    does not have, and improving that row is tenet 9's job rather than a second extraction's.

    `seen_this_run` is shared across hubs and MUTATED, because two hubs in one run legitimately
    link the same program -- CMU AI Scholars appeared on two different Immerse round-ups in a
    single 3-hub run -- and extracting it twice pays twice for one page and hands the reviewer a
    twin to resolve.
    """
    fresh, already, twice = [], 0, 0
    for u in urls or []:
        try:
            k = url_dedupe.match_key(u)
        except ValueError:
            continue
        if not k:
            continue
        if k in catalog_keys:
            already += 1
            continue
        if k in seen_this_run:
            twice += 1
            continue
        seen_this_run.add(k)
        fresh.append(u)
    return fresh, already, twice


def _host_path(url):
    """(lowercased hostname, path with no trailing slash) — the pair a containment check needs."""
    try:
        p = urllib.parse.urlsplit(url)
    except ValueError:
        return "", ""
    return (p.hostname or "").lower(), (p.path or "").rstrip("/").lower()


def contained_children(urls, catalog_paths=None):
    """(keep, dropped): drop a candidate that is the DIRECT sub-page of another page — one also
    in this candidate set, or already in the catalog — on the same host. Pure, free, pre-spend.

    A program index links the program's own page AND its ancillary tabs: the walk-up review
    surfaced Columbia's `/programs/summer-programs/nyc-residential-summer/residential-life`
    alongside its parent `/nyc-residential-summer`, which is the page a student should land on.
    When the parent is already catalogued, `fresh_candidates`' exact-URL check does NOT see the
    child, so the run would PAY to extract a worse-URL duplicate of a row we already have; when
    both are candidates this run, `collapse_intra_run_twins` only merges them AFTER paying for
    both. Suppressing the child here is free and stops both.

    DIRECT parent only (not any ancestor): a distinct program legitimately nested one level under
    a section is rare, but dropping on any ancestor would reach far enough up to catch it. This
    mirrors the same-host, real-prefix shape url_dedupe._prefix_relation already treats as a
    strong duplicate signal, kept one level tight because this DROPS rather than merely flags.
    """
    parsed = [(u, *_host_path(u)) for u in (urls or [])]
    own = {}
    for _u, h, pth in parsed:
        if h and pth:
            own.setdefault(h, set()).add(pth)
    cat = catalog_paths or {}
    keep, dropped = [], []
    for u, h, pth in parsed:
        segs = [s for s in pth.split("/") if s]
        parent = "/" + "/".join(segs[:-1]) if len(segs) >= 2 else ""
        parents = own.get(h, set()) | cat.get(h, set())
        if parent and parent != pth and parent in parents:
            dropped.append(u)
        else:
            keep.append(u)
    return keep, dropped


def catalog_paths_by_host(rows):
    """{host: {normalized_path, ...}} for every catalog url — the index contained_children reads."""
    out = {}
    for r in rows or []:
        h, pth = _host_path(r.get("url") or "")
        if h and pth:
            out.setdefault(h, set()).add(pth)
    return out


def hubs_from_leads(leads):
    """[(url, off_domain)] for queued hub leads -- WHICH WAY each one must be mined.

    The direction travels ON the lead rather than being decided here, because only whatever
    qualified the page knows it. The router qualifies a round-up by the distinct OTHER sites it
    links (>= 6), so its programs are on those sites and it is mined OFF-domain; `walk_up_hubs`
    qualifies an institution's own index by proving it links a program on ITS OWN site, so that
    one is mined same-domain. Mining either the wrong way round follows exactly the links that
    did not qualify it -- for a round-up, the page's own navigation.

    This was a flat `True` for every lead, and before that a flat `False`. Both were right for
    one kind of lead and wrong for the other, which is why the direction is now data.
    """
    from wingman import discovered_leads
    return [(l["url"], discovered_leads.lead_scope(l) == discovered_leads.SCOPE_OFF_DOMAIN)
            for l in (leads or []) if l.get("url")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hubs", nargs="+", help="Hub page URL(s).")
    ap.add_argument("--hubs-file", help="JSON file: [{\"url\":..., \"off_domain\": bool}, ...].")
    ap.add_argument("--from-leads", type=int, nargs="?", const=5, metavar="N",
                    help="Take up to N hub leads (default 5) that a search run captured for "
                         "free — see wingman/discovered_leads.py. The search already paid to consult "
                         "those pages; this is what stops them being discarded.")
    ap.add_argument("--off-domain", action="store_true",
                    help="Treat hubs as listicles: follow OFF-domain links, not same-domain.")
    ap.add_argument("--preview", action="store_true",
                    help="FREE: discover + dedup against the catalog, print candidates, make NO "
                         "model call and write nothing.")
    ap.add_argument("--give-up-after", type=int, default=6, metavar="N",
                    help="Stop reading a hub after N pages in a row are refused as not-a-program "
                         "(default 6). What it leaves unread is reported.")
    ap.add_argument("--max-pages", type=int, default=None, metavar="N",
                    help="Hard ceiling on how many pages this run may EXTRACT (i.e. pay for), "
                         "across every hub. What it skips is reported, never dropped silently.")
    ap.add_argument("--mode", default="national")
    ap.add_argument("--min-delay", type=int, default=5)
    ap.add_argument("--timeout", type=int, default=40)
    ap.add_argument("--dry-run", action="store_true",
                    help="PAID (extracts at full cost) but writes NO rows — logs the run + a snapshot.")
    args = ap.parse_args()
    safe_console()   # model output can carry characters a cp1252 console cannot encode

    hubs = []
    if args.hubs_file:
        with open(args.hubs_file, encoding="utf-8") as f:
            hubs = [(h["url"], bool(h.get("off_domain"))) for h in json.load(f)]
    hubs += [(u, args.off_domain) for u in (args.hubs or [])]
    lead_urls = []
    if args.from_leads:
        from wingman import discovered_leads
        queued = discovered_leads.pending(discovered_leads.KIND_HUB, limit=args.from_leads)
        lead_urls = [l["url"] for l in queued]
        # Each lead says which way it must be mined, and this is load-bearing. The router
        # qualifies a lead by counting the distinct OTHER sites it links (>= 6), so its programs
        # are on those sites and it is mined OFF-domain; mining it same-domain would follow
        # precisely the links the router did not count, i.e. the page's own navigation. A
        # walk-up lead is the opposite case -- proven by linking a program on its OWN site -- so
        # it is mined same-domain. The direction travels ON the lead rather than being decided
        # here, because only whatever qualified the page knows it. (This was a flat `True` for
        # every lead, and before that a flat `False`; both were wrong for half the queue.)
        hubs += hubs_from_leads(queued)
        by_scope = {}
        for l in queued:
            by_scope[discovered_leads.lead_scope(l)] = by_scope.get(
                discovered_leads.lead_scope(l), 0) + 1
        print(f"[OK] {len(lead_urls)} hub lead(s) taken from the queue"
              + (" (" + ", ".join(f"{k}={v}" for k, v in sorted(by_scope.items())) + ")"
                 if by_scope else "") + ".")
    if not hubs:
        print("[ERROR] Give --hubs or --hubs-file.")
        raise SystemExit(1)

    from wingman.supabase_common import load_dotenv, supabase_get
    load_dotenv()
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    service_key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_ANON_KEY")
    gemini_key = os.environ.get("GEMINI_API_KEY")
    existing = supabase_get(supabase_url, "opportunities", {"select": "id,name,url"},
                            service_key) if supabase_url else []

    # Every URL the catalog already holds, active or not. THIS is the pre-spend check, and it
    # has to be URL-only: at this point we have not extracted anything, so there is no name to
    # match on, and `find_duplicates(u, "")` can never return an exact hit -- its exact rule is
    # "same normalized URL AND similar name" by design, so an empty name always falls through to
    # a hint the caller then ignored. The comment below claimed the catalog was checked; it was
    # not, and NOTHING was ever suppressed.
    #
    # Measured: mining CMU's pre-college index extracted and inserted 14 rows of which 12 were
    # pages already in the catalog -- and the walk-up lead had said so in advance ("links 12
    # program(s) we already have"). The URL-only rule is right HERE, unlike at the scraper's
    # insert layer where a shared application portal legitimately backs several programs: we are
    # following a link, so if a row already sits at this exact URL, re-reading the page cannot
    # produce anything the catalog does not have. Improving that row is tenet 9's job, not a
    # second extraction's.
    catalog_keys = set()
    for r in existing or []:
        try:
            k = url_dedupe.match_key(r.get("url") or "")
        except ValueError:
            continue
        if k:
            catalog_keys.add(k)

    catalog_paths = catalog_paths_by_host(existing)

    from wingman import sitemap_hub
    seen_this_run = set()

    # ---- PREVIEW (free): no model call, no writes. An institutional hub reports the scoped page
    # list the LLM WOULD classify; an off-domain listicle uses the anchor miner as before.
    if args.preview:
        would_classify = 0
        for hub_url, off_domain in hubs:
            if not off_domain:
                scoped, tr = sitemap_hub.program_candidates(hub_url, classify=None,
                                                            timeout=args.timeout)
                would_classify += len(scoped)
                print(f"[HUB] {hub_url}: {tr.get('sources', 'none')}, enumerated "
                      f"{tr['enumerated']}, in scope {tr['in_scope']} -> classify (LLM) then "
                      f"extract survivors."
                      + (f"  ERROR: {tr['error']}" if tr.get("error") else ""))
                for u in scoped[:25]:
                    print(f"    to-classify: {u}")
                if tr["in_scope"] > 25:
                    print(f"    ... +{tr['in_scope'] - 25} more in scope")
            else:
                urls, _trace = discover(hub_url, off_domain=True, timeout=args.timeout)
                urls, _c = contained_children(urls, catalog_paths)
                fresh, _a, _t = fresh_candidates(urls, catalog_keys, seen_this_run)
                print(f"[HUB] {hub_url} (off-domain listicle): {len(fresh)} candidate(s).")
                for u in fresh:
                    print(f"    candidate: {u}")
        print(f"\n[PREVIEW] institutional hubs would classify ~{would_classify} in-scope page(s) "
              f"(~$0.001/hub) then extract only the pages the LLM calls a program. No model call, "
              f"no writes. A live run needs approval.")
        return

    if not gemini_key:
        print("[ERROR] GEMINI_API_KEY not set — cannot classify/extract. (Preview is free.)")
        raise SystemExit(1)

    # ---- SELECT (paid): the LLM URL classifier is the PRIMARY selector for an institution's own
    # index (sitemap ∪ anchor, scoped to the hub path, program pages out). A hub it returns
    # nothing for — a category-only sitemap, a non-opportunity section — falls back to the
    # anchor-rules recursive miner, so recall is never worse than before. An off-domain listicle's
    # programs live on OTHER sites, which the same-domain enumeration cannot see, so those stay on
    # the anchor miner. Validated across 22 real hubs 2026-08-28 (~$0.0009/hub). See sitemap_hub.
    classify = sitemap_hub.make_gemini_classifier(gemini_key, timeout=args.timeout,
                                                  min_delay=args.min_delay)
    all_new, select_cost = [], 0.0
    for hub_url, off_domain in hubs:
        if not off_domain:
            urls, tr = sitemap_hub.program_candidates(hub_url, classify=classify,
                                                      timeout=args.timeout)
            select_cost += tr.get("cost", 0.0)
            selector = f"sitemap-llm ${tr.get('cost', 0):.4f}"
            if not urls:                                 # empty -> anchor-rules recursive fallback
                d_urls, _dt = discover(hub_url, off_domain=False, timeout=args.timeout)
                urls, _c = contained_children(d_urls, catalog_paths)
                selector = "anchor-rules fallback"
        else:
            d_urls, _dt = discover(hub_url, off_domain=True, timeout=args.timeout)
            urls, _c = contained_children(d_urls, catalog_paths)
            selector = "anchor-rules (off-domain)"
        # Drop any candidate that is the sub-page of a CATALOGUED page, for every selector.
        urls, _c = contained_children(urls, catalog_paths)
        fresh, already, twice = fresh_candidates(urls, catalog_keys, seen_this_run)
        print(f"[HUB] {hub_url}: {selector}, {len(urls)} program page(s), already in catalog "
              f"{already}, seen this run {twice}, new {len(fresh)}.")
        for u in fresh:
            print(f"    candidate: {u}")
        all_new.append((hub_url, fresh))

    # A per-hub cap bounds one page; nothing bounded the RUN — cap the paid extractions and spread
    # the ceiling evenly across hubs rather than off the end of the list.
    capped_hubs = set()
    if args.max_pages is not None:
        all_new, capped = allocate_budget(all_new, args.max_pages)
        capped_hubs = {u for u, _n in capped}
        if capped:
            skipped = sum(n for _, n in capped)
            print(f"[LIMIT] --max-pages {args.max_pages} reached: {skipped} candidate(s) across "
                  f"{len(capped)} hub(s) NOT extracted, spread evenly rather than taken off the "
                  f"end of the list. They stay queued; raise the ceiling to reach them.")
            for hub_url, n in capped:
                print(f"    skipped {n}: {hub_url[:88]}")

    total = sum(len(f) for _, f in all_new)

    # PAID PATH — extract each followed link, then insert as is_active=false / pending_review,
    # exactly like the search scraper (shared insert_rows handles the migration-degrade ladder).
    # Reached only on an explicit (approved) live run.
    from wingman.supabase_common import supabase_insert_one, supabase_patch
    today = datetime.date.today().strftime("%Y%m%d")
    mint = next_id_generator({r["id"] for r in (existing or [])})
    run_row = supabase_insert_one(supabase_url, "agent_runs", {
        "agent": "hub_miner",
        "mode": "hub" + ("-dryrun" if args.dry_run else ""),
        "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }, service_key)
    run_id = run_row["id"] if run_row else None

    # The catalog dedupe_vector index (active rows only — only active rows carry a vector, written
    # at activation) powers the SAME embedding dedupe hint the scraper attaches. Read once, in
    # process; a missing column / no embeddings degrades to an empty index and the hint is simply
    # off (no exception), exactly like the scraper's gate. gate_by_id enriches a hint with the
    # survivor's name/url for the queue back-link.
    gate_index = dedupe_embed_store.fetch_dedupe_index(supabase_url, service_key)
    gate_by_id = {r["id"]: r for r in (existing or []) if r.get("id")}
    print(f"[OK] Discovery gate ON (M9): classify pill + embedding dedupe per extracted row; "
          f"dedupe index holds {len(gate_index)} embedded row(s).")

    # Bank the classifier spend up front so an exception in extraction cannot discard what SELECT
    # already paid — the same per-attempt banking the two-phase agents use.
    rows, review_by_id, cost, errors = [], {}, select_cost, 0
    rejected = []          # not-running programs DROPPED before insert (kept for the snapshot)
    yield_by_hub = {}
    for hub_url, fresh in all_new:
        dom = url_dedupe.registrable_domain(urllib.parse.urlsplit(hub_url).netloc) or "hub"
        source = f"hub-{dom}-{today}"
        made, tried, refused_in_a_row = 0, 0, 0
        for u in fresh:
            # A hub whose first pages are ALL refused is not going to start producing. Measured:
            # non-trivial.org spent 17 extractions for zero rows, and one Ladder post 4 for zero.
            # Refusal is the extractor working -- it returns {"name": null} for a page that is not
            # a single program -- so this does not skip anything a row would have come from; it
            # just stops paying to re-learn the same answer.
            if refused_in_a_row >= args.give_up_after:
                print(f"  [SKIP] {hub_url[:70]}: {refused_in_a_row} refusals in a row, "
                      f"{len(fresh) - tried} page(s) left unread.")
                break
            tried += 1
            try:
                cand, c, classification, dup_hints = extract_opportunity(
                    u, gemini_key, index=gate_index, timeout=args.timeout,
                    min_delay=args.min_delay)
                cost += c
            except Exception as e:
                errors += 1
                print(f"  [WARN] extract failed {u}: {str(e)[:100]}")
                continue
            if not cand:
                refused_in_a_row += 1
                continue
            # Discontinuation gate (shared, free): the extractor READ the page and may report the
            # program is no longer offered (`running`: false). A dead program must never reach the
            # reviewer, so DROP it here rather than insert it — but record the full candidate in the
            # rejected snapshot so nothing vanishes silently. A dropped page produced no row, so it
            # counts toward the give-up streak exactly like a refusal: a hub that is all dead
            # programs should stop costing money. queue_flags is the single interpreter of the
            # signal, so every review-queue writer drops identically; only `running is False` drops.
            if queue_flags.is_not_running(cand.get("running")):
                reason = queue_flags.not_running_reason(cand.get("running_reason"))
                print(f"  [DROP not-running] {(cand.get('name') or u)[:60]}: {reason[:90]}")
                rejected.append({**cand, "url": u, "found_via": hub_url, "source": source,
                                 "reject_reason": reason})
                refused_in_a_row += 1
                continue
            row = build_row(cand, next(mint), source, u, [])
            if not row:
                refused_in_a_row += 1
                continue
            refused_in_a_row = 0
            made += 1
            row["found_via"] = hub_url
            # Honest, free flags — but NOT the same set as the scraper, and that is the point.
            # FLAG_OFFSITE asks "did a model type a URL that belongs to somebody else?" Here the
            # URL was FOLLOWED FROM A LINK, so it is real by construction and the question does
            # not apply. Measured on the 43-hub run: 16 of the 17 rows it flagged were false
            # positives — thesca.org for the Student Conservation Association, precollege.syr.edu
            # for Syracuse four times, medschool.uci.edu for UC Irvine, caes.uga.edu for the
            # University of Georgia, internships.fnal.gov for Fermilab, and three CMU departments
            # whose org name carries a school suffix the acronym rule cannot see. A flag that is
            # wrong 94% of the time teaches the reviewer to ignore flags.
            #
            # A CONTENT MILL still flags: that is a fact about the destination, not about who
            # typed it, and a mill can be linked from anywhere.
            name, org = row.get("name"), row.get("org")
            flags = []
            if url_validate.is_bare_domain(u):
                flags.append(FLAG_BARE_DOMAIN)
            if url_validate.is_content_mill(u):
                flags.append(FLAG_OFFSITE)
            if url_dedupe.is_low_value_path(u):
                flags.append(FLAG_LOW_VALUE)
            if cand.get("type") not in VALID_TYPES:
                flags.append(FLAG_NO_TYPE)
            # The classify pill — the ONE thing that now makes a hub-mined row indistinguishable
            # from a scraper row in the review queue. Same `classify:` prefix, same string
            # (classify_page.Classification.flag()), so the console's flag_class() reads it the
            # same way and renders the same pill, staleness included. upsert (not append) so a
            # later change here can never leave two classify pills on one row.
            if classification and classification.flag():
                flags = queue_flags.upsert_flag(flags, queue_flags.CLASSIFY_PREFIX,
                                                classification.flag())
            # fresh_candidates already dropped every EXACT-URL catalog match before we paid to
            # extract, so the risk that remains is the SAME program at a DIFFERENT URL (the
            # 2026-08-28 audit's Cut 2 — a rename, a second departmental path, a slug change).
            # find_duplicates is free (pure, no network) and surfaces those as name/domain
            # hints; without this the row reached the queue with no link to its twin. Hints
            # only — the hub miner never auto-rejects, so an exact hit (should not occur here)
            # is downgraded to a strong candidate rather than dropping the row.
            _exact, dup_cands = url_dedupe.find_duplicates(u, name, existing, include_weak=False)
            if _exact and not any(c.get("id") == _exact.get("id") for c in dup_cands):
                dup_cands = [{"id": _exact.get("id"), "name": _exact.get("name"),
                              "url": _exact.get("url"),
                              "reason": "identical URL and matching name",
                              "confidence": "strong"}] + dup_cands
            # Merge the embedding dedupe hints (dedupe_vector cosine) on top of url_dedupe's
            # name/URL hints, via the SAME helper the scraper uses — so both sources produce the
            # same `dup_candidates` shape and the same queue back-links. Catches the twin at a
            # DIFFERENT URL that a name/URL match cannot see. A no-op when the index is empty.
            dup_cands = gate_dup_candidates(dup_hints, gate_by_id, dup_cands)
            review_by_id[row["id"]] = {"moderation_status": "pending_review",
                                       "dup_candidates": (dup_cands or None),
                                       "quality_flags": flags or None}
            rows.append(row)
            existing.append({"id": row["id"], "name": row["name"], "url": row["url"]})
        yield_by_hub[hub_url] = (made, tried)

    # Collapse in-run twins to their best-URL copy, exactly as the search scraper does. A
    # program index links the program AND its sub-pages, so one hub legitimately yields the
    # same opportunity more than once — that is a property of hubs, not a model error.
    flags_by_id = {rid: (rv.get("quality_flags") or []) for rid, rv in review_by_id.items()}
    rows, collapsed = collapse_intra_run_twins(rows, flags_by_id)
    if collapsed:
        print(f"[OK] Collapsed {len(collapsed)} in-run twin(s) to their best-URL copy.")

    # Review snapshot (audit trail; `inserted`/`rejected` shape mirrors the scraper's).
    stamp = snapshot_stamp()
    review_path = os.path.join(REPO_ROOT,
                               f"hub_review_{args.mode}_{stamp}.json")
    with open(review_path, "w", encoding="utf-8") as f:
        json.dump({"inserted": [{**r, "review": review_by_id.get(r["id"], {})} for r in rows],
                   "rejected": rejected, "merged": []}, f, indent=2, ensure_ascii=False)
    if rejected:
        print(f"[OK] Dropped {len(rejected)} not-running program(s) before insert — recorded in "
              f"{os.path.basename(review_path)} under 'rejected', never queued for review.")

    tier = None
    if args.dry_run:
        print(f"[DRY RUN] Extracted {len(rows)} row(s); NOTHING written. The run is still logged "
              f"to agent_runs (it cost real money).")
    elif rows:
        tier = insert_rows(supabase_url, service_key, rows, review_by_id)
        print(f"[OK] Inserted {len(rows)} row(s) into opportunities "
              f"(is_active=false, pending_review, tier={tier}).")
    else:
        print("[OK] No rows extracted — nothing to insert.")

    if run_id is not None:
        supabase_patch(supabase_url, "agent_runs", {"id": f"eq.{run_id}"}, {
            "finished_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "items_processed": total,
            "items_added": 0 if args.dry_run else len(rows),
            "errors": errors,
            "cost_usd": round(cost, 4),
            "notes": (f"hubs={len(hubs)}, candidates={total}, extracted={len(rows)}, "
                      f"dropped_not_running={len(rejected)}, source-date={today}"
                      + (f", would_have_added={len(rows)}" if args.dry_run else "")),
        }, service_key)

    # Mark every hub we actually mined, not only the ones taken off the queue. The CMU pilot was
    # run with --hubs while its lead sat in the queue as "new", so the next --from-leads run would
    # have re-mined and re-PAID for the same 14 pages. marking a URL that is not in the file is a
    # no-op, so this is safe for a hub that was never a lead.
    # A hub the ceiling truncated is NOT finished -- marking it processed would drop the rest of
    # its candidates out of the queue silently, which is the failure the ceiling exists to avoid.
    lead_urls = [u for u in dict.fromkeys(lead_urls + [h for h, _off in hubs])
                 if u not in capped_hubs]
    if lead_urls and not args.dry_run and not args.preview:
        # Stamped only on a real run: a dry run proved the extraction works but wrote nothing,
        # so the lead still has work left in it and must stay in the queue.
        from wingman import discovered_leads
        n = discovered_leads.mark_processed(lead_urls)
        print(f"[OK] Marked {n} hub lead(s) processed.")

    if yield_by_hub:
        print("[YIELD] rows made / pages read, per hub — worst first:")
        for hub_url, (made, tried) in sorted(yield_by_hub.items(), key=lambda kv: kv[1][0]):
            if not tried:
                continue
            rate = 100.0 * made / tried
            print(f"    {made:3} / {tried:3}  ({rate:3.0f}%)  {hub_url[:76]}")
        dud = [u for u, (made, tried) in yield_by_hub.items() if tried and not made]
        if dud:
            print(f"    {len(dud)} hub(s) produced NOTHING and are worth retiring by hand.")
    print(f"[SUMMARY] {total} candidate page(s) across {len(hubs)} hub(s) -> extracted "
          f"{len(rows)} row(s), errors {errors}, cost ${cost:.4f}. Wrote {review_path}.")
    print(f"[DONE] Review before activating anything from a source='hub-*-{today}' row.")


if __name__ == "__main__":
    main()

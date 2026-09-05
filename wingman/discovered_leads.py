#!/usr/bin/env python3
"""Phase 4F — capture the hub pages and listicles a search already paid to consult. FREE.

The three discovery channels are strangers to each other. The search scraper spends real money
consulting pages, keeps the handful that are program pages, and **throws the rest away** — and a
large share of what it throws away is exactly the feedstock the other two channels need.

**Measured on the 2026-08-23 run (28 seeds):** 660 grounding pages consulted -> 126 became
program rows -> **71 discarded content-mill/listicle URLs** (aralia "research journals for high
school", immerse "15 summer writing camps", lumiere "10 conferences"), each naming 7-15
programs. That is hundreds of leads discarded per run, on pages we had already paid to retrieve.

So: classify what the run did NOT use, and write it down.

    content mill / listicle   ->  a NAME-HARVEST lead   (agents/harvest_names.py --from-leads)
    links >= N HS programs    ->  a HUB-MINING lead     (agents/mine_hub_pages.py --from-leads)
    anything else             ->  ignored

**Capture, never inline-process.** Classifying is free; acting on a lead is paid. Keeping those
apart is what lets a search run stay one approved expense instead of quietly becoming three, and
it is the same split `--preview` makes everywhere else in this repo.

**This also changes where hub registries come from.** `hub_pilot_national.json` and
`hubs_seattle.json` are hand-curated, and curation was the bottleneck — the Seattle work measured
~40% of hand-picked civic hubs refusing our client at all. A search that spins off its own hubs
turns discovery into something that compounds: the more angles run, the more hubs exist to mine.

STORAGE is a JSONL file at the repo root (`discovered_leads.jsonl`), deliberately not a table.
A migration needs the operator to run DDL by hand, and this has to earn that first; the file is
append-only, greppable, and readable by both consumers today. The plan's `discovered_leads`
table stays the mature form.

    python -m wingman.discovered_leads --list              # FREE: what is queued, by kind
    python -m wingman.discovered_leads --list --kind hub   # just the hub-mining leads
"""
import argparse
import concurrent.futures
import datetime
import json
import os
import re
import urllib.parse

from wingman import page_text
from wingman import url_dedupe
from wingman import url_repair
from wingman import url_validate
from wingman import REPO_ROOT   # the repo root, defined once (see wingman/__init__.py)

# `mine_hub_pages` is imported LAZILY inside the two functions that need it. It imports
# scrape_opportunities (for build_row/insert_rows), and scrape_opportunities imports this
# module to capture leads — a module-level import here closes that cycle and breaks both.

LEADS_PATH = os.path.join(REPO_ROOT, "discovered_leads.jsonl")

KIND_NAMES = "names"          # a page that NAMES programs -> agents/harvest_names.py
KIND_HUB = "hub"              # a page that LINKS programs -> agents/mine_hub_pages.py

# WHICH WAY a hub lead must be mined, carried on the lead because it is a property of how the
# lead QUALIFIED, not of the miner. A round-up earns its place by linking >= 6 distinct OTHER
# sites, so its programs are on those sites; an institution's own index (wingman/walk_up_hubs.py) is
# proven by linking a program on ITS OWN site. Mining either the wrong way round follows exactly
# the links that did not qualify it -- for a round-up, its own navigation. Every lead written
# before this field existed came from the router, which is why the default is off-domain.
SCOPE_OFF_DOMAIN = "off-domain"
SCOPE_SAME_DOMAIN = "same-domain"
DEFAULT_SCOPE = SCOPE_OFF_DOMAIN

# The ONE reject reason that feeds this system. Rejecting is not the trigger — rejecting FOR
# THIS REASON is. Every other verdict ("wrong page", "dead link", "not a fit") says nothing
# about the page being a round-up, and routing all of them would spend a fetch per rejection to
# mostly learn no. Kept here rather than in the console so the hook and the backfill sweep can
# never disagree about which reason means what.
ROUNDUP_REJECT_REASON = "third-party-roundup"

STATUS_NEW = "new"
STATUS_DONE = "processed"
# A page we looked at and judged NOT a round-up is recorded too, so neither the reject hook nor
# a backfill sweep ever re-opens it. Without this every sweep re-fetches the whole rejected pile
# to re-learn answers it already had — 40 pages to recover 24 known "no"s, growing with the pile.
STATUS_NOT_A_LEAD = "not-a-lead"

# --- the classifier -------------------------------------------------------------------
# A third-party page earns a lead by its STRUCTURE, not by being on a known-bad host list.
# The question is only ever: does this page LINK the programs it mentions, or merely NAME them?
#
# The discriminator is DISTINCT OFF-DOMAIN DOMAINS. Measured 2026-08-27 on 6 real listicles and
# 5 real non-listicles:
#     listicles        collegevine 20, aralia 16, veritasai 13, ladder 13, immerse 9
#     not listicles    Cornell program page 3, job posting 2, university FAQ 1, cost page 1
# Off-domain is the half that matters for a third-party page — its SAME-domain links are its own
# navigation. (An earlier attempt counted same-domain links and could not separate the two
# populations at all; that measurement was asking the wrong question of the wrong half.)
# Distinct DOMAINS, not link count: the university FAQ carried 11 off-domain links pointing at a
# single repeated footer destination, and a raw count calls that a hub.
MIN_HUB_DOMAINS = 6
# A page that names programs without linking them still has to be a page ABOUT programs. These
# two are what separate "an article listing 15 summer programs" from a job posting that happens
# to mention a high school: it must read as prose aimed at high schoolers, and there must be
# enough of it to be a list rather than a mention.
MIN_NAMES_CHARS = 2000
# ...and its TITLE must promise MANY of them. A plural opportunity noun is what separates "15
# Summer Art Programs for High School Students" from "FAQ | Wake Forest Summer Immersion
# Program" — measured on 9 real pages, it kept 4 of 4 round-ups and rejected 4 of 4 non-lists.
# The fifth, "YoungArts - Wikipedia", is rejected too and that is CORRECT: it is an article
# about one program, so there is nothing to harvest. A Wikipedia LIST article ("List of physics
# competitions") carries the plural and is kept.
_MANY_RE = re.compile(r"\b(programs|competitions|internships|camps|scholarships|courses|"
                      r"workshops|fellowships|contests|conferences|journals|opportunities|"
                      r"schools|academies|institutes|summits|olympiads)\b", re.I)
# Free HTTP fetches per seed. Classification is a side-effect of a scrape, not the job, and a
# broad seed resolves dozens of pages.
# A runaway guard, NOT a cost control — looking at a page is free and fast. Measured on a real
# seed: 17 candidates classify in 1.4s across 12 workers (15s one at a time). A seed resolves
# ~20 pages, so this only ever fires on something pathological.
MAX_PAGES_PER_SEED = 60

# Hosts that are content mills but are NOT round-ups, so they are never leads of either kind.
# Measured 2026-08-27 by fetching one of each: youtube returns 24,000 chars of `ytcfg` JS config
# (which bills as input tokens like any other text) and reddit returns 0 (it refuses our client,
# the same blanket block check_reviews measured on 654 source URLs). YouTube alone was 20 of the
# 109 mill hits in the archived grounding. Wikipedia is deliberately NOT here: its LIST articles
# genuinely name programs, and the title test below rejects its single-program articles anyway.
_NOT_LISTICLE_HOSTS = {"youtube.com", "youtu.be", "reddit.com"}
def _key(url):
    """The dedupe key for a URL, or "" if it is not one.

    Defensive because this runs on whatever a search or a rejected row happens to contain:
    `match_key` parses, and a value like "javascript:void(0)" makes urlsplit raise while trying
    to read a port. A malformed URL is not a lead, and it must not be able to stop a scrape.
    """
    try:
        return url_dedupe.match_key(url or "")
    except ValueError:
        return ""


def is_ignorable(url):
    """True for a URL that can never be a lead of either kind. FREE, pure, no fetch.

    Social/share/commerce hosts and editorial posts are dropped up front: a Facebook page names
    no programs and a /blog/ post is one article, not an index. Both lists are already vetted
    where they live — reusing them here keeps one definition of "never a program source".
    """
    if not url or not url.lower().startswith(("http://", "https://")):
        return True
    from agents import mine_hub_pages
    host = url_dedupe.registrable_domain(urllib.parse.urlsplit(url).hostname or "")
    if host in mine_hub_pages._NONPROGRAM_HOSTS or host in _NOT_LISTICLE_HOSTS:
        return True
    return url_validate.is_editorial_url(url)


# Most page titles end with the site's own name after a separator, and that suffix lies about
# what the page is. Measured live: `opportunitiesforyouth.org` publishes single-program articles
# whose titles end "... - Opportunities for Youth", and the plural in the SITE NAME made the
# title test call a one-program article a round-up. Only a short tail is stripped, so a real
# title that merely contains a dash keeps all of itself.
_TITLE_SEPARATORS = (" | ", " - ", " – ", " — ", " :: ")
_MAX_SITE_NAME_WORDS = 5


def strip_site_name(title):
    """A page title with its trailing site-name removed, if that is what the tail looks like."""
    best = title or ""
    for sep in _TITLE_SEPARATORS:
        head, found, tail = best.rpartition(sep)
        if found and head.strip() and len(tail.split()) <= _MAX_SITE_NAME_WORDS:
            best = head.strip()
    return best


def _looks_like_stylesheet(text):
    """True when the extracted 'text' is really CSS. FREE, pure.

    Site builders can return a successful 200 whose readable text is entirely custom
    properties. Measured: a Wix round-up yielded 24,000 chars containing **412 `--` markers and
    0% prose lines**, while the round-ups that really are readable (lumiere aside) scored 0.
    It reads as a healthy fetch, which is what makes it dangerous — the page reports "named
    nothing" rather than "could not be read".
    """
    return (text or "").count("--") > 100


def classify_confirmed_roundup(url, timeout=url_repair.DEFAULT_TIMEOUT):
    """(kind, signal) for a page a PERSON has already confirmed is a third-party round-up.

    Deliberately skips the title test. That test answers "is this a page about many
    programs?" — and here a human has answered it by picking the round-up reject reason, having
    actually looked at the page. Re-asking it with a heuristic could only overrule them, and it
    would: the test reads a `<title>`, and a title is a poor description of a page that a person
    has read. So the only question left is the one the title never answered anyway — does it
    LINK the programs or merely NAME them.

    Defaults to KIND_NAMES rather than to nothing. An unreadable page still costs zero to queue
    (`harvest_names` makes no model call when a page yields no text), and dropping a lead a
    person explicitly flagged is the one outcome this path must not produce.
    """
    from agents import mine_hub_pages
    html = ""
    try:
        html = mine_hub_pages.fetch_html(url, timeout)
        kept, subs = mine_hub_pages.filter_hub_links(
            mine_hub_pages.harvest_links(html or "", url), url, off_domain=True, cap=400)
        domains = {url_dedupe.registrable_domain(urllib.parse.urlsplit(u).netloc)
                   for u, _ in kept + subs}
        domains.discard("")
    except Exception:
        domains = set()
    if len(domains) >= MIN_HUB_DOMAINS:
        return KIND_HUB, f"you marked it a round-up; it links programs on {len(domains)} sites"
    if not html:
        return KIND_NAMES, "you marked it a round-up, but we cannot read this page at all"
    return KIND_NAMES, ("you marked it a round-up; it links "
                        f"{len(domains)} site(s), so its programs are named, not linked")


def classify_page(url, timeout=url_repair.DEFAULT_TIMEOUT):
    """(kind, signal) for a third-party page, by looking at it. FREE — plain HTTP, no model.

    This is the whole routing decision:
        links programs (many distinct off-domain destinations)  -> KIND_HUB
        names programs in prose, without linking them           -> KIND_NAMES
        neither                                                 -> (None, why)
    """
    from agents import mine_hub_pages
    try:
        html = mine_hub_pages.fetch_html(url, timeout)
    except Exception:
        return None, "fetch failed"
    if not html:
        return None, "page could not be fetched"
    # ONE gate before either branch: is this page about many opportunities at all? Structure
    # alone is not enough — measured on the real rejected pile, a school district's "Classified
    # Employees" jobs page linked 10 distinct sites and a CLA press release linked 6, and the
    # link test happily called both hubs. Asking the title first makes the two branches
    # symmetrical: they then only decide HOW the page presents its programs, not whether it has
    # any. The wrong-audience check rides along for free (seagrant's "Undergraduate
    # Opportunities" links 20 sites and is not for high schoolers).
    title = strip_site_name(url_repair.page_title(html) or "")
    if not _MANY_RE.search(title):
        return None, f"title does not promise many programs: {title[:60]!r}"
    if mine_hub_pages.is_wrong_audience(title):
        return None, f"title names a non-high-school audience: {title[:60]!r}"
    # A page with NO anchors at all was not rendered for us. Every real article has some —
    # nav, footer, related posts — so zero is not "it doesn't link its programs", it is "we
    # received a shell". Measured on a Wix round-up: 400KB of HTML, **0 `<a>` tags**, and none
    # of the programs it lists (Parsons, Otis, Pratt, RISD) present anywhere in the bytes.
    # Without this guard that page scored "0 outside sites" and was routed to name harvest as
    # though it named its programs in prose — where the identical fetch would have found the
    # identical nothing. A confident wrong answer is worse than an honest no verdict.
    anchors = mine_hub_pages.harvest_links(html, url)
    if not anchors:
        return None, f"page did not render for us — 0 links in {len(html)} bytes (JS-built)"
    if _looks_like_stylesheet(page_text.fetch_page_text(url, timeout)[0] or ""):
        return None, "page returned stylesheet, not article text (site builder)"
    try:
        kept, subs = mine_hub_pages.filter_hub_links(anchors, url, off_domain=True, cap=400)
        domains = {url_dedupe.registrable_domain(urllib.parse.urlsplit(u).netloc)
                   for u, _ in kept + subs}
        domains.discard("")
    except Exception:
        domains = set()
    if len(domains) >= MIN_HUB_DOMAINS:
        return KIND_HUB, f"links programs on {len(domains)} distinct sites"
    text, _reason = page_text.fetch_page_text(url, timeout)
    text = text or ""
    if len(text) < MIN_NAMES_CHARS:
        return None, f"only {len(text)} chars of text — not a page about many programs"
    if not mine_hub_pages.has_hs_audience(text):
        return None, "page text does not name a high-school audience"
    return KIND_NAMES, f"names many programs: {title[:60]!r}"


def _shortlist(urls, used, known, trace, seen=None):
    """The candidates worth looking at, after the free pure filters. No page is fetched here."""
    seen = seen if seen is not None else set()
    out = []
    for url in urls or []:
        k = _key(url)
        if not k or k in seen:
            continue
        seen.add(k)
        if k in used:
            trace["already_used"] += 1
        elif k in known:
            trace["already_known"] += 1
        elif is_ignorable(url):
            trace["ignored"] += 1
        else:
            out.append(url)
    return out


def _classify_all(urls, classify, timeout=None):
    """[(url, kind, signal)] — every candidate looked at, CONCURRENTLY. FREE.

    Measured on a real seed: 17 candidates take **15s one at a time and 1.4s across 12
    workers**. That is why there is no per-seed budget any more. An earlier version capped this
    at 12 pages and would have needed a prioritiser to choose which 12 — a whole extra mechanism
    (and, in the version before that, a paid model call) to ration something that costs a second
    and a half. Same thread-pool width the liveness checker already uses.

    Classification is pure per URL — nothing is shared between them — so this parallelises with
    no coordination. The verdicts are assembled back in input order by the caller, so a run is
    reproducible regardless of which fetch finishes first.
    """
    if not urls:
        return []
    call = classify if timeout is None else (lambda u: classify(u, timeout))
    width = min(url_validate.MAX_WORKERS, len(urls))
    with concurrent.futures.ThreadPoolExecutor(width) as pool:
        verdicts = list(pool.map(call, urls))
    return [(u, v[0], v[1]) for u, v in zip(urls, verdicts)]


def capture(resolved_urls, used_urls, existing_rows=None, seed_id=None, angle="",
            known_keys=None, max_pages=MAX_PAGES_PER_SEED, timeout=None, classify=None):
    """The leads one seed's grounding yields. FREE. Returns (leads, trace).

    `used_urls` are the pages that BECAME rows — a page the run already turned into an
    opportunity is a result, not a lead. `known_keys` carries the catalog and the existing lead
    file, so the same listicle is not re-queued by every run that finds it.

    `classify` is injected so the routing is testable without a network; production gets
    `classify_page`.
    """
    classify = classify or classify_page
    used = {_key(u) for u in (used_urls or []) if u}
    known = set(known_keys or set())
    known |= {_key(r.get("url")) for r in (existing_rows or []) if r.get("url")}
    trace = {"resolved": len(resolved_urls or []), "already_used": 0, "already_known": 0,
             "ignored": 0, "looked_at": 0, "over_cap": 0, "no_verdict": 0,
             KIND_NAMES: 0, KIND_HUB: 0}

    shortlist = _shortlist(resolved_urls, used, known, trace)
    if len(shortlist) > max_pages:
        trace["over_cap"] = len(shortlist) - max_pages
        shortlist = shortlist[:max_pages]
    trace["looked_at"] = len(shortlist)

    leads = []
    stamp = datetime.date.today().isoformat()
    for url, kind, signal in _classify_all(shortlist, classify, timeout):
        if kind is None:
            trace["no_verdict"] += 1
            continue
        trace[kind] += 1
        leads.append({"url": url, "kind": kind, "seed_id": seed_id, "angle": angle,
                      "signal": signal, "first_seen": stamp, "status": STATUS_NEW})
    return leads, trace


def from_rejected_rows(rows, known_keys=None, classify=None, limit=None, timeout=None,
                       confirmed=False):
    """Leads from rows a person rejected. FREE. Returns (leads, trace).

    `confirmed=True` means the operator picked the round-up reject reason — they looked at the
    page and said what it is. That skips the title test (see `classify_confirmed_roundup`) and
    always yields a lead. `confirmed=False` is the general sweep, which still has to judge
    whether the page is a round-up at all, and records its NOs so they are never re-fetched.
    """
    classify = classify or (classify_confirmed_roundup if confirmed else classify_page)
    known = set(known_keys or set())
    trace = {"rejected": len(rows or []), "already_known": 0, "ignored": 0, "already_used": 0,
             "no_verdict": 0, KIND_NAMES: 0, KIND_HUB: 0}
    by_url = {}
    for row in rows or []:
        if row.get("url") and _key(row["url"]) not in by_url:
            by_url[_key(row["url"])] = row
    shortlist = _shortlist([r["url"] for r in by_url.values()], set(), known, trace)
    if limit:
        shortlist = shortlist[:limit]

    leads = []
    stamp = datetime.date.today().isoformat()
    for url, kind, signal in _classify_all(shortlist, classify, timeout):
        row = by_url.get(_key(url)) or {}
        entry = {"url": url, "kind": kind, "seed_id": row.get("seed_id"),
                 "angle": f"rejected row {row.get('id')}: {(row.get('name') or '')[:60]}",
                 "signal": signal, "first_seen": stamp, "status": STATUS_NEW}
        if kind is None:
            # Remember the NO. Written to the same file so the next sweep skips it outright.
            trace["no_verdict"] += 1
            entry["status"] = STATUS_NOT_A_LEAD
        else:
            trace[kind] += 1
        leads.append(entry)
    return leads, trace


def fetch_rejected_rows(supabase_url, service_key, limit=None, any_reason=False):
    """The catalog's rejected rows, newest first. FREE (a Supabase read costs nothing).

    Defaults to rows rejected FOR THE ROUND-UP REASON only, matching the live hook — the reason
    is the trigger, not the rejection. `any_reason=True` is the backfill escape hatch for rows
    rejected before that reason existed, where the general classifier has to judge for itself.
    """
    from wingman.supabase_common import supabase_get
    params = {"select": "id,name,url,seed_id,moderation_status,moderation_reason,quality_flags",
              "moderation_status": "eq.rejected", "order": "id.desc"}
    if not any_reason:
        params["moderation_reason"] = f"eq.{ROUNDUP_REJECT_REASON}"
    rows = supabase_get(supabase_url, "opportunities", params, service_key) or []
    return rows[:limit] if limit else rows


def load_leads(path=LEADS_PATH):
    """Every lead on disk, oldest first. A malformed line is skipped, never fatal."""
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                lead = json.loads(line)
            except ValueError:
                continue
            if isinstance(lead, dict) and lead.get("url"):
                out.append(lead)
    return out


def lead_keys(leads):
    return {_key(l.get("url")) for l in leads if l.get("url")}


def append_leads(leads, path=LEADS_PATH):
    """Append, skipping anything already on file. Returns how many were actually written."""
    if not leads:
        return 0
    known = lead_keys(load_leads(path))
    fresh = []
    for lead in leads:
        k = _key(lead.get("url"))
        if not k or k in known:
            continue
        known.add(k)
        fresh.append(lead)
    if not fresh:
        return 0
    with open(path, "a", encoding="utf-8") as f:
        for lead in fresh:
            f.write(json.dumps(lead, ensure_ascii=False) + "\n")
    return len(fresh)


def pending(kind, path=LEADS_PATH, limit=None):
    """The unprocessed leads of one kind, oldest first — what a consumer should work on."""
    out = [l for l in load_leads(path)
           if l.get("kind") == kind and l.get("status", STATUS_NEW) == STATUS_NEW]
    return out[:limit] if limit else out


def mark_processed(urls, path=LEADS_PATH):
    """Stamp these leads processed so the next gated run does not re-pay for them.

    Rewrites the file rather than appending a tombstone: the file is the work-list, and a
    work-list you have to replay to interpret is how a queue quietly grows forever.
    """
    keys = {_key(u) for u in (urls or []) if u}
    if not keys:
        return 0
    leads, n = load_leads(path), 0
    for lead in leads:
        if _key(lead.get("url")) in keys and lead.get("status") != STATUS_DONE:
            lead["status"] = STATUS_DONE
            lead["processed_at"] = datetime.date.today().isoformat()
            n += 1
    if n:
        with open(path, "w", encoding="utf-8") as f:
            for lead in leads:
                f.write(json.dumps(lead, ensure_ascii=False) + "\n")
    return n


def lead_scope(lead):
    """Which way this hub lead must be mined. Never guess it from the URL -- see SCOPE_*."""
    scope = (lead or {}).get("scope")
    return scope if scope in (SCOPE_OFF_DOMAIN, SCOPE_SAME_DOMAIN) else DEFAULT_SCOPE


def leads_to_show(leads, show_all=False, kind=None):
    """The rows `--list` prints. Pure, so the listing can be regression-tested.

    The default is the WORK-LIST: actionable leads only. A remembered NO (`not-a-lead`) and an
    already-processed lead both stay ON FILE so neither is ever re-fetched or re-paid for, but a
    work-list you have to mentally filter is not a work-list. `--all` shows every row.
    """
    rows = leads if show_all else [l for l in leads
                                   if l.get("status", STATUS_NEW) == STATUS_NEW]
    if kind:
        rows = [l for l in rows if l.get("kind") == kind]
    return rows


def summarize(leads):
    """{kind: count} over unprocessed leads — what the run summary and console report."""
    out = {}
    for lead in leads:
        if lead.get("status", STATUS_NEW) != STATUS_NEW:
            continue
        out[lead.get("kind") or "?"] = out.get(lead.get("kind") or "?", 0) + 1
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="FREE: show the queued leads.")
    ap.add_argument("--kind", choices=[KIND_NAMES, KIND_HUB], help="Only this kind.")
    ap.add_argument("--all", action="store_true", help="Include already-processed leads.")
    ap.add_argument("--path", default=LEADS_PATH)
    ap.add_argument("--from-rejects", action="store_true",
                    help="FREE: queue the rows you rejected AS ROUND-UPS. This is the catch-up "
                         "for rows rejected before the live hook existed — new ones are queued "
                         "the moment you pick that reason in the console.")
    ap.add_argument("--any-reason", action="store_true",
                    help="Sweep EVERY rejected row, not just the round-ups, and let the "
                         "classifier judge each one. For the backlog rejected before the "
                         "round-up reason existed. Its NOs are remembered, so it is cheap twice.")
    ap.add_argument("--limit", type=int, help="Max rejected rows to classify.")
    ap.add_argument("--commit", action="store_true", help="Write the leads (default: preview).")
    args = ap.parse_args()

    if args.from_rejects:
        import os as _os
        from wingman.supabase_common import load_dotenv
        load_dotenv()
        su = _os.environ.get("SUPABASE_URL", "").rstrip("/")
        key = _os.environ.get("SUPABASE_SERVICE_KEY") or _os.environ.get("SUPABASE_ANON_KEY")
        if not su or not key:
            print("[ERROR] SUPABASE_URL and a key must be set in .env.")
            raise SystemExit(1)
        rows = fetch_rejected_rows(su, key, limit=args.limit, any_reason=args.any_reason)
        scope = "every rejected row" if args.any_reason else f"rejected as {ROUNDUP_REJECT_REASON}"
        print(f"[OK] {len(rows)} row(s) to classify ({scope}; free HTTP, no model calls)...")
        leads, trace = from_rejected_rows(rows, known_keys=lead_keys(load_leads(args.path)),
                                          confirmed=not args.any_reason)
        print("[OK] " + ", ".join(f"{k}={v}" for k, v in trace.items()))
        for l in leads:
            if l["status"] == STATUS_NOT_A_LEAD:
                continue
            print(f"    {l['kind']:5}  {l['url'][:88]}")
            print(f"           {l['signal']}")
        nos = sum(1 for l in leads if l["status"] == STATUS_NOT_A_LEAD)
        if nos:
            print(f"    ({nos} page(s) judged not a round-up — remembered, never re-fetched)")
        real = [l for l in leads if l["status"] == STATUS_NEW]
        if args.commit:
            n = append_leads(leads, args.path)
            print(f"[OK] Wrote {n} row(s) ({len(real)} lead(s), {len(leads) - len(real)} "
                  f"remembered as not-a-round-up). Queue: {summarize(load_leads(args.path))}")
        else:
            print("")
            print(f"[PREVIEW] {len(real)} lead(s) would be queued, and "
                  f"{len(leads) - len(real)} NO(s) remembered so they are never re-fetched. "
                  f"Re-run with --commit.")
        return

    leads = load_leads(args.path)
    if not leads:
        print(f"[OK] No leads yet ({args.path} does not exist or is empty). Leads are captured "
              f"by agents/scrape_opportunities.py as a free side-effect of a search run.")
        return
    shown = leads_to_show(leads, show_all=args.all, kind=args.kind)
    counts = summarize(leads)
    nos = sum(1 for l in leads if l.get("status") == STATUS_NOT_A_LEAD)
    print(f"[OK] {len(leads)} lead(s) on file; unprocessed by kind: "
          + (", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "none")
          + (f"; {nos} remembered NO(s)" if nos else ""))
    for lead in shown:
        note = {STATUS_DONE: "  [processed]",
                STATUS_NOT_A_LEAD: "  [not a lead]"}.get(lead.get("status"), "")
        # A remembered NO has kind=None by construction, so the width format must not assume a
        # verdict exists -- `f"{None:5}"` raises, and it raised on the real file.
        scope = f" [{lead_scope(lead)}]" if lead.get("kind") == KIND_HUB else ""
        print(f"  {(lead.get('kind') or '--'):5}{scope}  {lead.get('url')}{note}")
        print(f"         seed={lead.get('seed_id')}  {lead.get('signal')}")
    print(f"\n  {KIND_HUB:5} leads -> python -m agents.mine_hub_pages --from-leads   (PAID extraction)")
    print(f"  {KIND_NAMES:5} leads -> python -m agents.harvest_names --from-leads    (PAID search)")


if __name__ == "__main__":
    main()

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

    content mill / listicle   ->  a NAME-HARVEST lead   (harvest_names.py --from-leads)
    links >= N HS programs    ->  a HUB-MINING lead     (mine_hub_pages.py --from-leads)
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

    python discovered_leads.py --list              # FREE: what is queued, by kind
    python discovered_leads.py --list --kind hub   # just the hub-mining leads
"""
import argparse
import datetime
import json
import os
import re
import urllib.parse

import page_text
import url_dedupe
import url_repair
import url_validate

# `mine_hub_pages` is imported LAZILY inside the two functions that need it. It imports
# scrape_opportunities (for build_row/insert_rows), and scrape_opportunities imports this
# module to capture leads — a module-level import here closes that cycle and breaks both.

LEADS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "discovered_leads.jsonl")

KIND_NAMES = "names"          # a page that NAMES programs -> harvest_names.py
KIND_HUB = "hub"              # a page that LINKS programs -> mine_hub_pages.py

STATUS_NEW = "new"
STATUS_DONE = "processed"

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
PROBE_PER_SEED = 12

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
    import mine_hub_pages
    host = url_dedupe.registrable_domain(urllib.parse.urlsplit(url).hostname or "")
    if host in mine_hub_pages._NONPROGRAM_HOSTS or host in _NOT_LISTICLE_HOSTS:
        return True
    return url_validate.is_editorial_url(url)


def classify_page(url, timeout=url_repair.DEFAULT_TIMEOUT):
    """(kind, signal) for a third-party page, by looking at it. FREE — plain HTTP, no model.

    This is the whole routing decision:
        links programs (many distinct off-domain destinations)  -> KIND_HUB
        names programs in prose, without linking them           -> KIND_NAMES
        neither                                                 -> (None, why)
    """
    import mine_hub_pages
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
    title = url_repair.page_title(html) or ""
    if not _MANY_RE.search(title):
        return None, f"title does not promise many programs: {title[:60]!r}"
    if mine_hub_pages.is_wrong_audience(title):
        return None, f"title names a non-high-school audience: {title[:60]!r}"
    try:
        kept, subs = mine_hub_pages.filter_hub_links(
            mine_hub_pages.harvest_links(html, url), url, off_domain=True, cap=400)
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


def capture(resolved_urls, used_urls, existing_rows=None, seed_id=None, angle="",
            known_keys=None, probe_budget=PROBE_PER_SEED, timeout=None, classify=None):
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
             "ignored": 0, "probed": 0, "no_verdict": 0, KIND_NAMES: 0, KIND_HUB: 0}
    leads, seen = [], set()
    stamp = datetime.date.today().isoformat()
    probed = 0
    for url in resolved_urls or []:
        k = _key(url)
        if not k or k in seen:
            continue
        seen.add(k)
        if k in used:
            trace["already_used"] += 1
            continue
        if k in known:
            trace["already_known"] += 1
            continue
        if is_ignorable(url):
            trace["ignored"] += 1
            continue
        # Every candidate earns a look. The verdict is structural — does this page LINK the
        # programs it mentions, or merely NAME them — and nothing about its domain shortcuts it.
        if probed >= probe_budget:
            continue
        probed += 1
        trace["probed"] += 1
        kind, signal = classify(url) if timeout is None else classify(url, timeout)
        if kind is None:
            trace["no_verdict"] += 1
            continue
        trace[kind] += 1
        leads.append({"url": url, "kind": kind, "seed_id": seed_id, "angle": angle,
                      "signal": signal, "first_seen": stamp, "status": STATUS_NEW})
    return leads, trace


def from_rejected_rows(rows, known_keys=None, classify=None, limit=None):
    """Leads from the review queue's REJECTED pile. FREE. Returns (leads, trace).

    The operator's second source, and it is free evidence of exactly the right kind: a row
    rejected as a third-party round-up is a page a HUMAN has already confirmed is not a
    program's own page but does talk about programs. That is the premise this whole classifier
    has to guess at when it works from raw grounding — here it is given.

    The URL is still classified structurally, because "not a program page" does not say whether
    it LINKS the programs or merely NAMES them, and that is what decides which extractor gets it.
    """
    classify = classify or classify_page
    known = set(known_keys or set())
    trace = {"rejected": len(rows or []), "already_known": 0, "ignored": 0, "no_verdict": 0,
             KIND_NAMES: 0, KIND_HUB: 0}
    leads, seen = [], set()
    stamp = datetime.date.today().isoformat()
    for row in rows or []:
        if limit and len(leads) >= limit:
            break
        url = row.get("url")
        k = _key(url)
        if not k or k in seen:
            continue
        seen.add(k)
        if k in known:
            trace["already_known"] += 1
            continue
        if is_ignorable(url):
            trace["ignored"] += 1
            continue
        kind, signal = classify(url)
        if kind is None:
            trace["no_verdict"] += 1
            continue
        trace[kind] += 1
        leads.append({"url": url, "kind": kind, "seed_id": row.get("seed_id"),
                      "angle": f"rejected row {row.get('id')}: {(row.get('name') or '')[:60]}",
                      "signal": signal, "first_seen": stamp, "status": STATUS_NEW})
    return leads, trace


def fetch_rejected_rows(supabase_url, service_key, limit=None):
    """The catalog's rejected rows, newest first. FREE (a Supabase read costs nothing)."""
    from supabase_common import supabase_get
    params = {"select": "id,name,url,seed_id,moderation_status,moderation_reason,quality_flags",
              "moderation_status": "eq.rejected", "order": "id.desc"}
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
           if l.get("kind") == kind and l.get("status", STATUS_NEW) != STATUS_DONE]
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


def summarize(leads):
    """{kind: count} over unprocessed leads — what the run summary and console report."""
    out = {}
    for lead in leads:
        if lead.get("status", STATUS_NEW) == STATUS_DONE:
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
                    help="FREE: classify the review queue's REJECTED rows into leads. A row "
                         "rejected as a third-party round-up is a page a human already "
                         "confirmed talks about programs without being one.")
    ap.add_argument("--limit", type=int, help="Max rejected rows to classify.")
    ap.add_argument("--commit", action="store_true", help="Write the leads (default: preview).")
    args = ap.parse_args()

    if args.from_rejects:
        import os as _os
        from supabase_common import load_dotenv
        load_dotenv()
        su = _os.environ.get("SUPABASE_URL", "").rstrip("/")
        key = _os.environ.get("SUPABASE_SERVICE_KEY") or _os.environ.get("SUPABASE_ANON_KEY")
        if not su or not key:
            print("[ERROR] SUPABASE_URL and a key must be set in .env.")
            raise SystemExit(1)
        rows = fetch_rejected_rows(su, key, limit=args.limit)
        print(f"[OK] {len(rows)} rejected row(s) to classify (free HTTP, no model calls)...")
        leads, trace = from_rejected_rows(rows, known_keys=lead_keys(load_leads(args.path)))
        print("[OK] " + ", ".join(f"{k}={v}" for k, v in trace.items()))
        for l in leads:
            print(f"    {l['kind']:5}  {l['url'][:88]}")
            print(f"           {l['signal']}")
        if args.commit:
            n = append_leads(leads, args.path)
            print(f"[OK] Wrote {n} new lead(s). Queue: {summarize(load_leads(args.path))}")
        else:
            print("")
            print(f"[PREVIEW] {len(leads)} lead(s) would be queued. Re-run with --commit.")
        return

    leads = load_leads(args.path)
    if not leads:
        print(f"[OK] No leads yet ({args.path} does not exist or is empty). Leads are captured "
              f"by scrape_opportunities.py as a free side-effect of a search run.")
        return
    shown = leads if args.all else [l for l in leads if l.get("status") != STATUS_DONE]
    if args.kind:
        shown = [l for l in shown if l.get("kind") == args.kind]
    counts = summarize(leads)
    print(f"[OK] {len(leads)} lead(s) on file; unprocessed by kind: "
          + (", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "none"))
    for lead in shown:
        flag = "" if lead.get("status") != STATUS_DONE else "  [processed]"
        print(f"  {lead.get('kind'):5}  {lead.get('url')}{flag}")
        print(f"         seed={lead.get('seed_id')}  {lead.get('signal')}")
    print(f"\n  {KIND_HUB:5} leads -> python mine_hub_pages.py --from-leads   (PAID extraction)")
    print(f"  {KIND_NAMES:5} leads -> python harvest_names.py --from-leads    (PAID search)")


if __name__ == "__main__":
    main()

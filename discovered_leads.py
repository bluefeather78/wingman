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
import urllib.parse

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

# A page must link at least this many surviving program candidates to be worth calling a hub.
# Below it we are looking at an ordinary program page that happens to link two siblings, and
# queueing those would bury the real indexes — the same reason `filter_hub_links` caps its own
# output rather than returning everything it sees.
MIN_HUB_LINKS = 5
# Hub detection costs one free HTTP fetch per URL, and a broad seed resolves dozens of pages.
# The budget caps those fetches: lead capture is a side-effect of a scrape, not the job. Mill
# classification is pure and is NOT capped, which is why the names half still works at 0.
#
# **DEFAULT 0 — automatic hub capture is OFF, because free link-counting DOES NOT identify a hub
# page, and two measurements say so rather than one opinion.** Replaying the 40 archived seed
# logs (900 grounding URLs) at a budget of 8 classified **204 of 273 probed pages (75%) as
# hubs** — among them /faq/, /apply/, /contact/, a PR-newswire release and a job posting. Two
# candidate discriminators were then measured head-to-head on 6 known indexes vs 7 known
# non-indexes:
#
#   raw candidate-link count   good 11-94   bad  7-53   -> fully overlapping
#   count minus the site nav   good  0-57   bad  0-35   -> fully overlapping
#
# The cause is structural: same-domain links on ANY page are dominated by the site's shared
# navigation, so the count measures how big the nav is, not whether the page is an index. Nav
# subtraction cannot fix it either — `precollege.wisc.edu`, one of the best hubs we have, scores
# 0 because it IS the site root, so its links ARE the nav; meanwhile a CMU cost page scores 35.
#
# A hub lead feeds a PAID extraction, so a 25%-precision queue spends money and reviewer
# attention on junk. The machinery below is kept, tested and ready — `capture(probe_budget=N)`
# turns it on for an experiment — but nothing queues hub leads until a discriminator exists that
# actually separates the two populations. The names half needs none of this and ships on.
HUB_PROBE_PER_SEED = 0

# NOT every content mill is a listicle, and this distinction is worth money.
# `url_validate.is_content_mill` answers "may this URL be stored as a row's URL", and for THAT
# question a video, a forum thread and an SEO round-up are all equally disqualified. For "does
# this page name programs worth resolving" they are not remotely alike. Measured on the 40
# archived seed logs (2026-08-23, 900 grounding URLs, 109 mill hits) by fetching one of each:
#
#   lumiere / immerse / aralia   19,500-24,000 chars of real listicle prose   <- the feedstock
#   en.wikipedia.org              7,713 chars of real article prose          <- keep, it names
#   www.youtube.com              24,000 chars of `ytcfg.set({...})` JS config <- junk, and it
#                                bills as input tokens like any other text
#   www.reddit.com                    0 chars (empty-or-js; Reddit refuses our client, the same
#                                blanket block check_reviews measured on 654 source URLs)
#
# YouTube was 20 of the 109 mill hits and Reddit several more, so leaving them in would have
# spent naming calls on the two hosts guaranteed to yield nothing. Wikipedia stays: a list
# article genuinely names programs, and the three free gates in harvest_names judge the names.
_NOT_LISTICLE_HOSTS = {"youtube.com", "youtu.be", "reddit.com"}


def _key(url):
    return url_dedupe.match_key(url or "")


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


def classify_pure(url):
    """KIND_NAMES for a content mill/listicle, else None. FREE and pure — no fetch.

    A mill is classified WITHOUT a fetch because `is_content_mill` is a host/path test, so the
    largest bucket of leads costs nothing at all to find. That asymmetry is deliberate: it means
    a scrape can capture its listicles even when every hub probe is over budget.
    """
    if is_ignorable(url):
        return None
    return KIND_NAMES if url_validate.is_content_mill(url) else None


def hub_link_count(url, timeout=url_repair.DEFAULT_TIMEOUT):
    """How many program candidates this page links, via the hub miner's own free filters.

    Uses `filter_hub_links` rather than a raw `<a>` count so the number means the same thing it
    means to the consumer that will mine it — nav, PDFs, social, wrong-audience and branch pages
    are already gone. A page we cannot fetch counts 0: unreachable is not evidence of a hub.
    """
    import mine_hub_pages
    try:
        html = mine_hub_pages.fetch_html(url, timeout)
        if not html:
            return 0
        kept, subs = mine_hub_pages.filter_hub_links(
            mine_hub_pages.harvest_links(html, url), url, off_domain=False)
        return len(kept) + len(subs)
    except Exception:
        return 0


def capture(resolved_urls, used_urls, existing_rows=None, seed_id=None, angle="",
            known_keys=None, probe_budget=HUB_PROBE_PER_SEED, timeout=None, probe=None):
    """The leads one seed's grounding yields. FREE. Returns (leads, trace).

    `used_urls` are the pages that BECAME rows — a page the run already turned into an
    opportunity is not a lead, it is a result. `known_keys` carries the catalog and the existing
    lead file so the same listicle is not re-queued on every run that finds it.

    `probe` is injected so the hub half is testable without a network; production passes None
    and gets `hub_link_count`.
    """
    probe = probe or hub_link_count
    used = {_key(u) for u in (used_urls or []) if u}
    known = set(known_keys or set())
    known |= {_key(r.get("url")) for r in (existing_rows or []) if r.get("url")}
    trace = {"resolved": len(resolved_urls or []), "already_used": 0, "already_known": 0,
             "ignored": 0, "probed": 0, "names": 0, "hub": 0}
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
        kind, signal = classify_pure(url), None
        if kind is None:
            # Not a mill, so the only way to know is to look. Budgeted per seed.
            if probed >= probe_budget:
                continue
            probed += 1
            trace["probed"] += 1
            n = probe(url) if timeout is None else probe(url, timeout)
            if n < MIN_HUB_LINKS:
                continue
            kind, signal = KIND_HUB, f"links {n} program candidate(s)"
        else:
            signal = "content mill / listicle — names programs it does not link"
        trace[kind] += 1
        leads.append({"url": url, "kind": kind, "seed_id": seed_id, "angle": angle,
                      "signal": signal, "first_seen": stamp, "status": STATUS_NEW})
    return leads, trace


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
    args = ap.parse_args()

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

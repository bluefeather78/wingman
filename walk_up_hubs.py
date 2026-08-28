#!/usr/bin/env python3
"""Phase 4B - derive an institution's own program index by walking UP from a program. FREE.

The router (`discovered_leads.py`) finds round-ups because they link OUTWARD: >= 6 distinct
off-domain domains separates them cleanly (round-ups 9-20, ordinary pages 1-3). It is blind to
the other half of the world - a university's own pre-college index, which links its programs on
its OWN domain. That half was measured three separate ways and none of them worked, over 6 real
seeds / 109 candidate pages:

  * raw same-domain link counting calls **42% of everything** an index (FIT's *costs* page
    scored 401, a press release 318, a Berkeley FAQ 93);
  * subtracting the shared navigation still overlaps (real indexes 0-57, ordinary pages 0-35);
  * counting only the links under the page's own path gives **0 for 3 of 6 real indexes** -
    Georgetown, Ringling and LIM keep their programs at a different path than the index.

The cause is structural rather than a badly chosen threshold: on any page, same-domain links are
dominated by the navigation every page on the site shares. So **stop trying to recognise an index
from the outside.**

    a program we already trust        ced.berkeley.edu/academics/summer-programs/summer-institute
    its parent                        ced.berkeley.edu/academics/summer-programs/
    does the parent link the child?   yes -> it is that program's index, PROVEN, not guessed

Walking up costs one free fetch per distinct parent, needs no classifier, and aims hub mining at
exactly the institutions where a real program is already proven to exist. The catalog becomes the
source of hubs, which is the compounding version of a hand-curated registry.

**The proof is the back-link, never the shape of the URL.** A parent that does not link the child
is not that child's index - it is just a shorter URL. This is the same rule `url_repair` applies
to a re-found link and `page_text` applies to a task: proof over similarity, always. It is also
what makes the 42%-false-positive measurement above irrelevant here: a costs page does not link
the program pages, so it can never pass, however many same-domain links it carries.

**Never walk up to a bare domain.** Measured on the first hub pilot: `business.wisc.edu` (a root
homepage) yielded 40 links with exactly 2 gems, while its `/precollege/` sub-hub was almost all
programs. A root homepage is a site, not an index, so a row sitting one segment deep contributes
no lead rather than a bad one.

Output is a **hub lead with `scope = same-domain`**, appended to the same
`discovered_leads.jsonl` work-list the router writes to. That scope is load-bearing: a router
lead is mined OFF-domain because its programs are on other sites, and one of these must be mined
SAME-domain because its programs are on this one. Mining either the wrong way round follows
precisely the links that did not qualify it - for a round-up, its own menu.

    python walk_up_hubs.py                  # FREE: preview what the whole active catalog implies
    python walk_up_hubs.py --limit 40       # FREE: only the 40 best-ranked parents
    python walk_up_hubs.py --commit         # FREE: write them as leads for hub mining

Every tier here is free - it is plain HTTP and pure code, with no model call anywhere. The PAID
step is mining what this queues, which is gated as usual (`mine_hub_pages.py --from-leads`).
"""
import argparse
import concurrent.futures
import datetime
import urllib.parse

import discovered_leads
import mine_hub_pages
from agent_common import safe_console
import url_dedupe
import url_repair
import url_validate

# A parent has to offer more than the row we already have, or mining it is a paid no-op. Kept
# low deliberately: this is a yield hint, not the proof - the back-link is the proof - and hub
# mining re-filters and re-dedupes everything it is handed anyway.
MIN_PROGRAM_LINKS = 3

# How many links are scanned on one parent before the count stops being exact. A page that
# reaches this is a site section rather than a program list -- UCLA Anderson's student-experience
# page hit it on the first live run -- and the number is reported as "N+" rather than as a total,
# because a censored count printed as a total is how a bounded sweep comes to read as a complete
# one. Nothing is rejected for hitting it; the operator decides, and the miner has its own cap.
LINK_SCAN_CAP = 400


def parent_url(url):
    """The directory one level above `url`, or None when there is nothing safe to walk up to.

    Returns None for a bare domain and for anything one segment deep, because walking up from
    those lands on the site's ROOT homepage - measured chaff (business.wisc.edu: 40 links, 2
    gems), and a homepage is a site rather than an index. Query strings and fragments are
    dropped: they address a view of a page, not a place in a hierarchy.
    """
    try:
        parts = urllib.parse.urlsplit((url or "").strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    segments = [s for s in parts.path.split("/") if s]
    if len(segments) < 2:
        return None                      # bare domain, or one level deep -> parent is the root
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc,
                                    "/" + "/".join(segments[:-1]) + "/", "", ""))


def group_by_parent(rows):
    """{parent_url: [row, ...]} over rows that have a parent worth looking at.

    Several rows sharing one parent is the strongest signal in this whole module and it costs
    nothing to compute: if five programs we have already approved sit under one path, that path
    is an index and no fetch is needed to suspect it. The fetch is still made, because suspecting
    is not proving.
    """
    out = {}
    for row in rows or []:
        parent = parent_url(row.get("url"))
        if not parent or url_validate.is_content_mill(parent):
            continue
        if discovered_leads.is_ignorable(parent):
            # A row can sit at /blog/<post> or on a social host, and its parent is then a blog
            # index or a profile page -- never a program list. Reusing the router's own "can
            # never be a source" rule keeps one definition of that rather than a second one here.
            continue
        out.setdefault(parent, []).append(row)
    return out


def _key(url):
    return discovered_leads._key(url)


def verify_index(parent, child_urls, page_html, known_keys=None, final_url=None):
    """(ok, signal, stats) for one candidate index page. Pure - the fetch is the caller's.

    Two questions, in the order that matters:
      1. **Does it link one of the children we walked up from?** This is the proof, and nothing
         substitutes for it. Without it we have a shorter URL, not an index.
      2. Does it offer program pages beyond that child? A page that links only the one program is
         that program's own sub-page, not a list.
    """
    stats = {"links": 0, "candidates": 0, "new": 0, "children_linked": 0}
    if not page_html:
        return False, "could not read the page", stats

    child_keys = {_key(u) for u in child_urls if u}
    child_keys.discard("")
    if final_url and _key(final_url) in child_keys:
        # The parent redirected onto the child itself - a directory that is really just the
        # program page. There is no index here, and following it would re-mine what we have.
        return False, "redirects to the program itself", stats

    links = mine_hub_pages.harvest_links(page_html, parent)
    stats["links"] = len(links)
    linked = {_key(u) for u, _ in links}
    stats["children_linked"] = len(child_keys & linked)
    if not stats["children_linked"]:
        return False, "does not link the program we walked up from", stats

    kept, subs = mine_hub_pages.filter_hub_links(links, parent, off_domain=False,
                                                 cap=LINK_SCAN_CAP)
    candidates = [u for u, _ in kept + subs if _key(u) not in child_keys]
    stats["candidates"] = len(candidates)
    stats["capped"] = len(kept) >= LINK_SCAN_CAP
    known = set(known_keys or ())
    stats["new"] = sum(1 for u in candidates if _key(u) not in known)
    if stats["candidates"] < MIN_PROGRAM_LINKS:
        return False, (f"links the program but only {stats['candidates']} other program page(s) "
                       f"- not a list"), stats
    more = f"{stats['candidates']}+" if stats["capped"] else str(stats["candidates"])
    return True, (f"links {stats['children_linked']} program(s) we already have, plus "
                  f"{more} more ({stats['new']} not in the catalog)"
                  + (" - so many that this is likely a site section, not a program list"
                     if stats["capped"] else "")), stats


def _fetch_parent(parent, timeout):
    try:
        return url_repair._fetch(parent, timeout)
    except Exception:
        return None, None


def walk_up(rows, known_keys=None, limit=None, timeout=url_repair.DEFAULT_TIMEOUT, fetch=None):
    """(leads, trace) - the indexes the catalog itself implies. FREE.

    Parents are ranked BEFORE the fetch by how many of our own rows sit under them, so a `--limit`
    spends its fetches on the parents most likely to be real indexes rather than on whichever
    happened to be read first.
    """
    fetch = fetch or _fetch_parent
    known = set(known_keys or ())
    groups = group_by_parent(rows)
    trace = {"rows": len(rows or []), "parents": len(groups), "already_known": 0, "looked_at": 0,
             "unreadable": 0, "not_an_index": 0, "leads": 0}

    ranked = [(p, rs) for p, rs in groups.items() if _key(p) not in known]
    trace["already_known"] = len(groups) - len(ranked)
    ranked.sort(key=lambda pr: (-len(pr[1]), pr[0]))
    if limit:
        ranked = ranked[:limit]
    trace["looked_at"] = len(ranked)
    if not ranked:
        return [], trace

    width = min(url_validate.MAX_WORKERS, len(ranked))
    with concurrent.futures.ThreadPoolExecutor(width) as pool:
        fetched = list(pool.map(lambda pr: fetch(pr[0], timeout), ranked))

    leads, ranked_leads, stamp = [], [], datetime.date.today().isoformat()
    for (parent, rows_here), (page, final) in zip(ranked, fetched):
        ok, signal, stats = verify_index(parent, [r.get("url") for r in rows_here], page,
                                         known_keys=known, final_url=final)
        if not ok:
            trace["unreadable" if not page else "not_an_index"] += 1
            continue
        trace["leads"] += 1
        first = rows_here[0]
        ranked_leads.append((stats["children_linked"], stats["new"], len(leads)))
        leads.append({"url": parent, "kind": discovered_leads.KIND_HUB,
                      "scope": discovered_leads.SCOPE_SAME_DOMAIN, "seed_id": None,
                      "angle": (f"walk-up from row {first.get('id')}: "
                                f"{(first.get('name') or '')[:60]}"),
                      "signal": signal, "first_seen": stamp,
                      "status": discovered_leads.STATUS_NEW})
    # Densest first, measured AFTER the fetch: how many of our own rows the page actually links
    # is the proven version of how many sit under its path, and it is what decides which leads
    # are worth paying to mine first. Ties break on how much of the page is new to us.
    ranked_leads.sort(key=lambda t: (-t[0], -t[1]))
    return [leads[i] for _ours, _new, i in ranked_leads], trace


def fetch_trusted_rows(supabase_url, service_key, limit=None):
    """The rows worth walking up from: the ACTIVE catalog. FREE (a Supabase read costs nothing).

    Active is the strongest trust signal this repo has - `is_active=true` means a person looked
    at the row and said yes (nothing in this repo activates anything automatically). Walking up
    from a row nobody has vetted would aim the miner at whatever a scrape happened to guess.
    """
    from supabase_common import supabase_get
    rows = supabase_get(supabase_url, "opportunities",
                        {"select": "id,name,org,url", "is_active": "eq.true",
                         "order": "id.asc"}, service_key) or []
    return rows[:limit] if limit else rows


def catalog_keys(supabase_url, service_key):
    """Every URL the catalog holds, active or not - a parent we already have is not a lead."""
    from supabase_common import supabase_get
    rows = supabase_get(supabase_url, "opportunities", {"select": "url"}, service_key) or []
    return {_key(r.get("url")) for r in rows if r.get("url")} - {""}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--limit", type=int, help="Only the N best-ranked parents (most of our own "
                                             "rows underneath them first).")
    ap.add_argument("--rows", type=int, help="Only walk up from the first N catalog rows.")
    ap.add_argument("--timeout", type=int, default=url_repair.DEFAULT_TIMEOUT)
    ap.add_argument("--commit", action="store_true",
                    help="Write the leads (default: preview). Free either way.")
    ap.add_argument("--path", default=discovered_leads.LEADS_PATH)
    args = ap.parse_args()
    # A catalog row's NAME reaches this console -- and one of them carries a Hawaiian okina
    # (U+02BB), which a cp1252 console cannot encode. Without this the whole run died on a print
    # after every fetch had already completed. Same crash the hub miner hit on a model's U+2011.
    safe_console()

    import os
    from supabase_common import load_dotenv
    load_dotenv()
    su = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_ANON_KEY")
    if not su or not key:
        print("[ERROR] SUPABASE_URL and a key must be set in .env.")
        raise SystemExit(1)

    rows = fetch_trusted_rows(su, key, limit=args.rows)
    known = catalog_keys(su, key) | discovered_leads.lead_keys(
        discovered_leads.load_leads(args.path))
    print(f"[OK] {len(rows)} active row(s); {len(known)} URL(s) already known. "
          f"Free HTTP only - no model call anywhere in this script.")

    leads, trace = walk_up(rows, known_keys=known, limit=args.limit, timeout=args.timeout)
    print("[OK] " + ", ".join(f"{k}={v}" for k, v in trace.items()))

    # WRITE FIRST, then print. Printing 241 leads is where this run died the first time, and
    # everything it had done was lost with it -- the same shape as banking a paid call's cost
    # before any parse that can raise. Writing is cheap and idempotent; rendering is not safe.
    written = discovered_leads.append_leads(leads, args.path) if args.commit else 0

    for lead in leads:
        print(f"    {lead['url']}")
        print(f"           {lead['signal']}")
        print(f"           {lead['angle']}")

    if args.commit:
        print(f"[OK] Wrote {written} same-domain hub lead(s). "
              f"Queue: {discovered_leads.summarize(discovered_leads.load_leads(args.path))}")
        print("     Mining them is PAID and gated: python mine_hub_pages.py --from-leads")
    else:
        print("")
        print(f"[PREVIEW] {len(leads)} lead(s) would be queued. Re-run with --commit to write "
              f"them. Writing is free; mining them later is not.")


if __name__ == "__main__":
    main()

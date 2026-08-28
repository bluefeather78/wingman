#!/usr/bin/env python3
"""Resurrect real programs whose stored link died — one narrow search per row. PAID (gated).

Phase 4 of the scraper v2 plan. check_links.py deactivated ~200 rows for dead links, and Phase 2
showed those are real programs whose page MOVED, not programs that ended — the free URL-truth
rescue could not recover them because their real page was never in any grounding (a true search
miss). This does the one thing that can: a single, narrow web search per row — "the current
official page for <name> by <org>" — takes the grounding-resolved, title-proven URL, and inserts
a fresh is_active=false row for the operator to approve. A genuinely discontinued program returns
no qualifying page and writes nothing; either way the old row is stamped `refind_attempted <date>`
so it is never re-paid on the next pass.

SELECTION is FREE and previewable; the SEARCH is PAID (~$0.02-0.05/row) and — like every paid
agent here — needs fresh explicit approval per run.

    python refind_dead_links.py --preview            # FREE: which rows would be re-found
    python refind_dead_links.py --limit 20            # PAID (gated): re-find up to 20 rows
"""
import argparse
import datetime
import os
import urllib.parse

import url_dedupe
import url_repair
import url_validate

_REFIND_STAMP = "refind_attempted"
# At most this many grounding siblings are fetched per row to find a proven page.
_MAX_SIBLING_FETCH = 3
# The editorial-post test (the live UVA /blog/ mis-find that motivated it) now lives in
# url_validate, shared with harvest_names — the same question, asked of the same kind of
# search result. Kept as a module alias so this file's callers and tests read unchanged.
_EDITORIAL_SEGMENTS = url_validate.EDITORIAL_SEGMENTS
_is_editorial_url = url_validate.is_editorial_url


def is_dead_link_reject(row):
    """True if this row was rejected/deactivated for a DEAD link and has not been re-found yet.

    Pure — the selection predicate, unit-tested. A row already stamped refind_attempted is
    excluded so a discontinued program is not re-searched (and re-paid) every pass.
    """
    flags = [str(f) for f in (row.get("quality_flags") or [])]
    if any(_REFIND_STAMP in f for f in flags):
        return False
    reason = str(row.get("moderation_reason") or "").lower()
    haystack = (" ".join(flags) + " " + reason).lower()
    return ("dead link" in haystack or reason.startswith("dead-link")
            or "404" in haystack or "410" in haystack)


def refind_angle(name, org):
    """The narrow search handed to the model for one row."""
    who = f'"{name}"' + (f" run by {org}" if org else "")
    return f"Find the current official program page for {who}. Return only its own page."


def best_refound_url(resolved_urls, old_url, name, org, timeout):
    """The title-proven URL among a search's grounding results — or None. FREE.

    Held to a STRICTER bar than the general scraper's `_first_proven_sibling`, because a re-find
    is a paid, precision-first operation whose measured failures (Aug-27 pilot) were exactly the
    two things a looser gate lets through:
      1. `domain_matches_org` substring-matches too loosely (`northern.virginia.edu` ⊃
         "virginia"). So instead of the name-derived substring heuristic, require the re-found URL
         to sit on the **same registrable domain as the dead URL** — a program moves pages, not
         institutions; a jump to another domain is re-finding a different thing. This is "require
         the org's registrable domain, not a substring": the old URL IS the org's domain of
         record.
      2. Title-proof is weak on generic names ("Creative Writing") and can land on a sibling. So
         require BOTH title_proves (tests 1+2) AND keeps_identity (test 3, the built sibling
         guard: the new page must not drop an identity word the old URL used).
      3. An editorial post (/blog//news/) is never the program's own page — the live UVA
         mis-find that motivated this was a same-domain blog article that passed 1 and 2.
    A malformed/empty old URL yields no registrable domain, so nothing passes — fail closed,
    which is the correct precision-first default (the row simply stays in the dead pile)."""
    old_reg = url_dedupe.registrable_domain(
        (urllib.parse.urlsplit(old_url or "").hostname or "").lower())
    if not old_reg:
        return None
    fetched = 0
    for sib in resolved_urls:
        if fetched >= _MAX_SIBLING_FETCH:
            break
        if not sib or url_validate.is_content_mill(sib) or _is_editorial_url(sib):
            continue
        sib_reg = url_dedupe.registrable_domain(
            (urllib.parse.urlsplit(sib).hostname or "").lower())
        if sib_reg != old_reg:
            continue
        fetched += 1
        page, final = url_repair._fetch(sib, timeout)
        if not page:
            continue
        final = final or sib
        if url_validate.is_bare_domain(final):
            continue                      # a redirect to the homepage is a soft-404, not a page
        title = url_repair.page_title(page)
        if not url_repair.title_proves(title, name, org)[0]:
            continue
        if not url_repair.keeps_identity(old_url, final, title, name, org)[0]:
            continue
        return final
    return None


def select(rows):
    return [r for r in rows if is_dead_link_reject(r)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true", help="FREE: list targets, no search, no write.")
    ap.add_argument("--limit", type=int, default=20, help="Max rows to re-find in a paid run.")
    ap.add_argument("--min-delay", type=int, default=5)
    ap.add_argument("--timeout", type=int, default=280)
    args = ap.parse_args()

    from supabase_common import load_dotenv, supabase_get, supabase_patch, supabase_insert_one
    load_dotenv()
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    service_key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_ANON_KEY")
    if not supabase_url or not service_key:
        print("[ERROR] SUPABASE_URL and a key must be set in .env.")
        raise SystemExit(1)

    rows = supabase_get(supabase_url, "opportunities",
                        {"select": "id,name,org,url,quality_flags,moderation_reason,is_active",
                         "is_active": "eq.false"}, service_key) or []
    targets = select(rows)
    print(f"[OK] {len(targets)} dead-link row(s) eligible for re-find "
          f"(of {len(rows)} inactive rows scanned).")
    for r in targets[:args.limit]:
        print(f"    {r['id']}  {(r.get('name') or '')[:50]}  ({r.get('url')})")

    if args.preview:
        print(f"\n[PREVIEW] No search, no writes. A live run searches up to {args.limit} row(s) "
              f"(~$0.02-0.05 each) and needs approval.")
        return

    gemini_key = os.environ.get("GEMINI_API_KEY")
    if not gemini_key:
        print("[ERROR] GEMINI_API_KEY not set — cannot search. (Preview is free without it.)")
        raise SystemExit(1)

    # PAID PATH — reached only on an explicit (approved) live run.
    import scrape_opportunities as so
    from gemini_common import set_min_delay
    set_min_delay(args.min_delay)
    today = datetime.date.today().strftime("%Y%m%d")
    all_ids = {r["id"] for r in (supabase_get(supabase_url, "opportunities",
                                              {"select": "id"}, service_key) or [])}
    mint_id = so.next_id_generator(all_ids)

    class _A:  # minimal args shim for research_seed
        timeout = args.timeout
        max_searches = 1
    found = paid = 0
    cost = 0.0
    for r in targets[:args.limit]:
        name, org = r.get("name") or "", r.get("org") or ""
        try:
            notes, usage, grounding, c, _att = so.research_seed(
                refind_angle(name, org), "", today, gemini_key, _A, system=so.RESOLVE_SYSTEM)
            cost += c
            paid += 1
            resolved = [x["url"] for x in url_validate.resolve_grounding_chunks(grounding)
                        if x.get("url")]
            new_url = best_refound_url(resolved, r.get("url") or "", name, org,
                                       url_validate.DEFAULT_TIMEOUT)
        except Exception as e:
            print(f"  [WARN] {r['id']}: {str(e)[:120]}")
            new_url = None
        stamp = list(r.get("quality_flags") or []) + [f"{_REFIND_STAMP} {today}"]
        patch = {"quality_flags": stamp}
        if new_url and not url_dedupe.find_duplicates(new_url, name, rows)[0]:
            new_row = so.build_row({**r, "url": new_url}, next(mint_id),
                                   f"refind-{today}", new_url, [])
            if new_row:
                new_row["found_via"] = r.get("url")
                supabase_insert_one(supabase_url, "opportunities",
                                    {**new_row, "moderation_status": "pending_review"}, service_key)
                found += 1
                print(f"  [REFOUND] {r['id']} -> {new_url}")
        supabase_patch(supabase_url, "opportunities", {"id": f"eq.{r['id']}"}, patch, service_key)
    print(f"[SUMMARY] searched {paid} row(s), re-found {found}, cost ${cost:.4f}. "
          f"Re-found rows are is_active=false pending review.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Phase 4N — turn the program NAMES a page mentions into title-proven catalog rows. PAID (gated).

Hub mining (`mine_hub_pages.py`) can only follow an `<a>` tag. Two large classes of page NAME
many real programs without linking them, and both are invisible to link-harvest:

  * **JS-rendered national directories.** Measured 2026-08-27 on College Transitions' Dataverse:
    0 harvestable program links, while `page_text` returns 14,355 chars naming ~70 academic
    competitions. The names are in the server-rendered TEXT; the URLs are built by script.
  * **Local civic calendars.** Measured 2026-08-27 across 8 Seattle hubs: libraries and museums
    link to branch LOCATIONS and service categories, and put their actual teen programs in event
    widgets and prose. Link-harvest returned ~0 programs from them; the names are all there in
    the text.

So this reads the names off the page and resolves each one to its OWN page the same way
`refind_dead_links.py` resurrects a moved program: one narrow search, take the
grounding-resolved URL, and demand the page's own `<title>` prove it. The name is never the
evidence — the page is. A name that resolves to nothing provable writes nothing.

**Why this is higher-precision than a broad search angle:** the hub already vouched that the
program exists and is aimed at high schoolers, so the search only has to answer name -> URL. It
is not discovering from scratch, which is where the search scraper's 26%-dead-link and
6%-listicle failures came from.

THREE FREE GATES run before a single search is paid for, and each one exists to stop money being
spent on a name that could never produce a provable row:
  1. **The name must be ON the page** (`name_is_on_page`) — every one of its identity words,
     literally, in the fetched text. A model listing programs it merely knows of is the
     invented-Algebra-2 family, and this is the same code-level answer `page_text` gives there.
  2. **The name must be PROVABLE** (`name_is_resolvable`) — `url_repair.title_proves` needs two
     identity words of its own, so "Debate" or "Summer Internship" can never clear the evidence
     bar no matter what the search returns. Paying to find out is pure waste.
  3. **The name must be NEW** (`is_known_name`) — strict identity-set equality against the
     catalog, never a similarity ratio (see that function).

COST: fetching the page and all three gates are FREE. One no-search model call per hub reads the
names (~$0.001). Then per surviving name: one search (~$0.02-0.05, the per-search fee dominates)
plus one no-search extraction (~$0.003). `--preview` stops before ANY model call and prints what
would be harvested. Rows land is_active=false / pending_review, `found_via` = the page that named
the program; nothing reaches students without a human yes. Like every paid agent here, a live run
needs fresh explicit approval.

    python harvest_names.py --hubs https://www.collegetransitions.com/dataverse/... --preview
    python harvest_names.py --hubs-file hub_pilot_national.json --preview   # FREE
    python harvest_names.py --hubs URL --max-names 10                       # PAID (gated)
"""
import argparse
import datetime
import json
import os
import re
import urllib.parse

import mine_hub_pages
import page_text
import url_dedupe
import url_repair
import url_validate
from agent_common import safe_console, snapshot_stamp
from scrape_opportunities import (build_row, next_id_generator, insert_rows, VALID_TYPES,
                                  FLAG_BARE_DOMAIN, FLAG_LOW_VALUE, FLAG_OFFSITE, FLAG_NO_TYPE)

# At most this many grounding siblings are fetched while proving one name. The same cap
# refind_dead_links uses: the answer is in the first few results or it is not there.
MAX_SIBLING_FETCH = 3
# Default ceiling on names resolved per hub page. A directory naming 70 competitions would
# otherwise turn one approved run into ~$2-3 of searches without the operator seeing a figure.
DEFAULT_MAX_NAMES = 10
# Whole-word tokenisation for the on-page check. Substring matching would let "art" pass on a
# page that only says "start" — the identity words are short by construction (the generic ones
# have already been stripped), so whole words are the only honest test.
_WORD_RE = re.compile(r"[a-z0-9]+")

_NAME_SYSTEM = (
    "You read one web page and list the extracurricular opportunities it NAMES for high "
    "school students — programs, competitions, internships, summer courses, conferences or "
    "journals.\n"
    "Return ONLY a JSON array of strings, each the opportunity's name exactly as the page "
    "writes it. No URLs, no descriptions, no numbering.\n"
    "RULES:\n"
    "- Copy names VERBATIM from the page text. Never add an opportunity you know of that this "
    "page does not name, and never expand or correct a name the page gives.\n"
    "- Name the opportunity, not the organization: 'MIT Research Science Institute', not 'MIT'.\n"
    "- Skip anything for elementary, middle school, undergraduate, graduate or adult "
    "audiences, and skip the page's own navigation, sections and headings.\n"
    "- If the page names none, return []."
)


def parse_names(raw, cap=200):
    """Model output -> a clean, order-preserving, case-insensitively deduped list of names.

    Tolerates the two shapes a model reaches for besides a bare array: objects carrying a
    `name` key, and a wrapper object with one array value. Anything else is dropped rather
    than coerced — a malformed answer must shrink the work list, never invent entries in it.
    """
    if isinstance(raw, dict):
        raw = next((v for v in raw.values() if isinstance(v, list)), [])
    if not isinstance(raw, list):
        return []
    out, seen = [], set()
    for item in raw:
        if isinstance(item, dict):
            item = item.get("name")
        if not isinstance(item, str):
            continue
        name = " ".join(item.split()).strip(" -–—*•\t")
        if not name or len(name) > 160:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
        if len(out) >= cap:
            break
    return out


def name_is_on_page(name, text):
    """FREE gate 1. True when every identity word of the name really appears in the page text.

    The page — not the model — is what vouches that this program exists and belongs here. A
    model handed a page of prose will happily list programs it remembers alongside the ones it
    read, and that is the exact failure the action-item verifier was built to make impossible;
    this is the same answer in code rather than in a prompt.

    Matching is EXACT on normalized text, never fuzzy. `url_repair` measured what a similarity
    ratio does to precisely this judgement (at >= 0.72 it accepted "Summer Research Immersion"
    as proof of "First-year Research Immersion"): the shared words are the category and the
    differing word is the identity, which is backwards for a ratio.
    """
    words = url_repair.identity_words(name)
    if not words:
        return False
    hay = set(_WORD_RE.findall(page_text.normalize_for_match(text or "")))
    return words <= hay


def name_is_resolvable(name, org=""):
    """FREE gate 2. True when this name has enough of its own words to ever be title-proven.

    `url_repair.title_proves` refuses a name with fewer than two identity words, so a name like
    "Debate" or "Summer Internship" cannot clear the evidence bar however good the search is.
    Checking here means we never pay a per-search fee to learn that.
    """
    return len(url_repair.identity_words(name, org)) >= 2


def _name_signature(name):
    """(identity words, the DIGITS `identity_words` throws away).

    The digits are not decoration. `url_repair._words` keeps only tokens of three or more
    characters, so a leading number vanishes: "1-Week Medical Academy" and "3-Week Medical
    Academy" BOTH reduce to {"week", "medical"} — CLAUDE.md's measured 0.95-similarity
    collision, and the digit is the entire difference between them. Carrying the digits
    alongside the identity words is what keeps two genuinely distinct programs distinct, where
    an identity-set test alone would silently suppress one of them.

    Short ALPHABETIC tokens are deliberately NOT carried, though an earlier version did. They
    are almost always a qualifier rather than an identity: measured live 2026-08-27 against the
    catalog, treating them as marks made "Academic Decathlon" fail to match the row it already
    has ("US Academic Decathlon", ec17937) purely on the "us", so the run re-paid for a search
    on a program it already held. A digit changes which program it is; "US" says where.
    """
    toks = re.findall(r"[a-z0-9]+", (name or "").lower())
    return url_repair.identity_words(name), frozenset(t for t in toks if t.isdigit())


def is_known_name(name, existing_rows):
    """FREE gate 3. The id of a catalog row that is unmistakably this same program, or None.

    Deliberately STRICT: the identity words must be EQUAL, hold at least two words, and carry
    the same discarded marks (see `_name_signature`). Never a similarity ratio, and never a
    subset test — `scrape_opportunities` used a 0.85 ratio and it matched 264 catalog pairs of
    which 257 were distinct opportunities.

    The asymmetry drives the strictness. A false positive here silently skips a program we may
    not have — a recall loss nothing downstream can recover, against tenet 2. A false negative
    costs one search, and then `url_dedupe.find_duplicates` catches it at insert time on the
    URL, which is the signal this repo actually trusts. So when in doubt, pay the search.

    The two-word floor keeps the un-identifying names out entirely: "Summer Internship" reduces
    to nothing at all, so it can never suppress anything.
    """
    words, marks = _name_signature(name)
    if len(words) < 2:
        return None
    for row in existing_rows or []:
        if _name_signature(row.get("name") or "") == (words, marks):
            return row.get("id")
    return None


def select_names(raw_names, text, existing_rows, cap=DEFAULT_MAX_NAMES):
    """(keep, dropped) — the three free gates, in order, with a per-page cap. Pure.

    `dropped` maps a reason to the names it cost, so a run that resolves nothing says WHY
    instead of reading as "the page named nothing".
    """
    keep, dropped = [], {"not_on_page": [], "unprovable": [], "already_in_catalog": [],
                         "over_cap": []}
    for name in raw_names:
        if not name_is_on_page(name, text):
            dropped["not_on_page"].append(name)
            continue
        if not name_is_resolvable(name):
            dropped["unprovable"].append(name)
            continue
        known = is_known_name(name, existing_rows)
        if known:
            dropped["already_in_catalog"].append(f"{name} (= {known})")
            continue
        if len(keep) >= cap:
            dropped["over_cap"].append(name)
            continue
        keep.append(name)
    return keep, dropped


def resolve_angle(name, org=""):
    """The narrow search handed to the model for one harvested name."""
    who = f'"{name}"' + (f" run by {org}" if org else "")
    return (f"Find the official page for the high school opportunity {who}. "
            f"Return only its own page.")


def best_resolved_url(resolved_urls, name, org="", timeout=url_repair.DEFAULT_TIMEOUT):
    """FREE. The title-proven URL among a search's grounding results, or None.

    Held to the refind evidence bar minus the one test that cannot apply: refind requires the
    re-found URL to sit on the same registrable domain as the dead one, because a program moves
    pages and not institutions. A harvested name has no prior URL, so there is no domain of
    record to hold it to — `title_proves` carries the whole weight, which is why gate 2 refuses
    a name that cannot be proven before any of this is paid for.

    `url_validate.domain_matches_org` is deliberately NOT a gate here. It measures ~9% false
    positives on this catalog's own live rows (university domain abbreviations no rule derives),
    so gating on it would discard real programs; the caller attaches FLAG_OFFSITE instead and
    the reviewer decides.

    A bare domain IS accepted when its title proves the name — for a dedicated program site
    (`jshs.org`, `nacloweb.org`) the homepage IS the program page, and `is_bare_domain` fires
    correctly on 16% of live catalog rows for exactly that reason. It is only preferred LAST,
    and the caller flags it. That is the one place this differs from refind, where a redirect
    to a homepage means the deep page it used to have was deleted.
    """
    proven, fetched = [], 0
    for sib in resolved_urls or []:
        if fetched >= MAX_SIBLING_FETCH:
            break
        if not sib or url_validate.is_content_mill(sib) or url_validate.is_editorial_url(sib):
            continue
        fetched += 1
        page, final = url_repair._fetch(sib, timeout)
        if not page:
            continue
        final = final or sib
        if not url_repair.title_proves(url_repair.page_title(page), name, org)[0]:
            continue
        proven.append(final)
    for url in proven:
        if not url_validate.is_bare_domain(url):
            return url
    return proven[0] if proven else None


def harvest_names(hub_url, key, timeout=40, min_delay=5, cap=200):
    """PAID (~$0.001): page text in -> the names the page states. Returns (names, text, cost).

    One no-search call, for the same reason `mine_hub_pages.extract_opportunity` is one: there
    is nothing to search for, the page is already in hand. An unfetchable page costs nothing at
    all — there would be nothing to verify an answer against, which is the rule the action-item
    agent settled on.
    """
    from gemini_common import call_gemini, extract_json, estimate_cost, set_min_delay
    set_min_delay(min_delay)
    text, _reason = page_text.fetch_page_text(hub_url, timeout)
    if not text:
        return [], "", 0.0
    user = (f"PAGE URL: {hub_url}\n\nPAGE TEXT:\n{text[:16000]}\n\n"
            f"Return the JSON array of opportunity names now.")
    out, usage = call_gemini(_NAME_SYSTEM, user, key, use_web_search=False,
                             max_tokens=2000, timeout=timeout)
    return parse_names(extract_json(out), cap=cap), text, estimate_cost(usage)


FLAG_SELF_PROMOTED = ("resolved to the same site as the page that named it — may be that "
                      "site's own product rather than an independent program")


def is_self_promoted(url, source_url):
    """True when a page's named program resolves back onto that same page's own site.

    Measured on the first live run (2026-08-27): harvesting 3 marketing listicles produced 3
    rows and ALL THREE were Immerse Education products, two of them from Immerse's own
    listicle — while every independent program the same pages named (Parsons, Otis, Drexel,
    NYU Tisch, Columbia, MAD) failed title-proof. The cause is not the evidence bar being
    wrong, it is WHAT a listicle calls things: a heading like "Drawing: Eye and Idea
    Pre-College Course at Columbia University" is a description, not what Columbia calls the
    course, so no title can prove it — whereas a company names its OWN products canonically in
    its own article, so those sail through. The bar therefore selects self-promotion.

    This is a FLAG, never a rejection: a provider hosting a genuinely real program is exactly
    the Immerse case the operator already ruled on (a mill can host its own real program). The
    reviewer needs to see the pattern, not have the row decided for them.
    """
    if not url or not source_url:
        return False
    a = url_dedupe.registrable_domain(urllib.parse.urlsplit(url).hostname or "")
    b = url_dedupe.registrable_domain(urllib.parse.urlsplit(source_url).hostname or "")
    return bool(a) and a == b


def _row_flags(url, name, org, cand, source_url=""):
    """The honest, free review flags for a resolved row — the scraper's own set."""
    flags = []
    if is_self_promoted(url, source_url):
        flags.append(FLAG_SELF_PROMOTED)
    if url_validate.is_bare_domain(url):
        flags.append(FLAG_BARE_DOMAIN)
    if not url_validate.domain_matches_org(url, org, name):
        flags.append(FLAG_OFFSITE)
    if url_dedupe.is_low_value_path(url):
        flags.append(FLAG_LOW_VALUE)
    if (cand or {}).get("type") not in VALID_TYPES:
        flags.append(FLAG_NO_TYPE)
    return flags


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hubs", nargs="+", help="Page URL(s) that NAME opportunities.")
    ap.add_argument("--hubs-file", help='JSON file: [{"url": ...}, ...] (same shape as the hub registry).')
    ap.add_argument("--from-leads", type=int, nargs="?", const=3, metavar="N",
                    help="Take up to N listicle leads (default 3) that a search run captured "
                         "for free — see discovered_leads.py. A listicle names many programs "
                         "and links none of them, which is exactly this agent's case.")
    ap.add_argument("--preview", action="store_true",
                    help="FREE: fetch the page(s) and report what would be harvested. No model "
                         "call, no search, no writes.")
    ap.add_argument("--max-names", type=int, default=DEFAULT_MAX_NAMES,
                    help=f"Names resolved per page (default {DEFAULT_MAX_NAMES}). Each one is a paid search.")
    ap.add_argument("--mode", default="national")
    ap.add_argument("--min-delay", type=int, default=5)
    ap.add_argument("--timeout", type=int, default=40)
    ap.add_argument("--max-searches", type=int, default=1)
    ap.add_argument("--dry-run", action="store_true",
                    help="PAID (searches at full cost) but writes NO rows — logs the run + a snapshot.")
    args = ap.parse_args()
    safe_console()   # model output can carry characters a cp1252 console cannot encode

    hubs = []
    if args.hubs_file:
        with open(args.hubs_file, encoding="utf-8") as f:
            hubs = [h["url"] for h in json.load(f)]
    hubs += list(args.hubs or [])
    lead_urls = []
    if args.from_leads:
        import discovered_leads
        lead_urls = [l["url"] for l in discovered_leads.pending(discovered_leads.KIND_NAMES,
                                                               limit=args.from_leads)]
        hubs += lead_urls
        print(f"[OK] {len(lead_urls)} listicle lead(s) taken from the queue.")
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

    if args.preview:
        # FREE: prove the page is fetchable and show what the name gates would do to a
        # stand-in list — the page's own text, not a model's reading of it. This cannot show
        # the real names (that needs the model call), so it reports reach, not yield.
        reachable = 0
        for hub_url in hubs:
            text, reason = page_text.fetch_page_text(hub_url, args.timeout)
            if not text:
                print(f"[HUB] {hub_url}: NOT FETCHABLE ({reason}) — costs nothing, harvests nothing.")
                continue
            reachable += 1
            print(f"[HUB] {hub_url}: fetched {len(text)} chars of text. A live run makes 1 "
                  f"naming call (~$0.001), then up to {args.max_names} searches "
                  f"(~$0.02-0.05 each) for names that pass all three free gates.")
            # The excerpt is the point of a free preview: a 200 with a cookie banner and a
            # 200 with a program list are the same char count until you look at one.
            print(f"    text starts: {text[:280].strip()!r}")
        # Price the run over the pages that can actually be harvested, not over every page
        # asked for. An unfetchable page makes no model call at all, so quoting for it inflates
        # the figure the operator is being asked to approve — the same way the agent-cost
        # estimator's failed runs deflated it, in the other direction.
        print(f"\n[PREVIEW] {len(hubs)} page(s), {reachable} fetchable. No model call, no writes. "
              f"Worst case for a live run over the fetchable ones: "
              f"~${(0.001 + 0.05 * args.max_names) * reachable:.2f} "
              f"({reachable} naming call(s) + up to {args.max_names * reachable} searches).")
        return

    if not gemini_key:
        print("[ERROR] GEMINI_API_KEY not set — cannot harvest. (Preview is free without it.)")
        raise SystemExit(1)

    # PAID PATH — reached only on an explicit (approved) live run.
    from supabase_common import supabase_insert_one, supabase_patch
    import scrape_opportunities as so
    today = datetime.date.today().strftime("%Y%m%d")
    mint = next_id_generator({r["id"] for r in (existing or [])})
    run_row = supabase_insert_one(supabase_url, "agent_runs", {
        "agent": "name_harvester",
        "mode": "names" + ("-dryrun" if args.dry_run else ""),
        "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }, service_key)
    run_id = run_row["id"] if run_row else None

    class _A:                                   # minimal args shim for research_seed
        timeout = args.timeout
        max_searches = args.max_searches

    rows, review_by_id, cost, errors, searched, named = [], {}, 0.0, 0, 0, 0
    for hub_url in hubs:
        dom = url_dedupe.registrable_domain(urllib.parse.urlsplit(hub_url).netloc) or "page"
        source = f"names-{dom}-{today}"
        try:
            raw_names, text, c = harvest_names(hub_url, gemini_key, timeout=args.timeout,
                                               min_delay=args.min_delay)
            cost += c
        except Exception as e:
            errors += 1
            print(f"[WARN] naming call failed for {hub_url}: {str(e)[:120]}")
            continue
        if not raw_names:
            print(f"[HUB] {hub_url}: named nothing (or page unfetchable) — nothing to resolve.")
            continue
        named += len(raw_names)
        keep, dropped = select_names(raw_names, text, existing, cap=args.max_names)
        print(f"[HUB] {hub_url}: named {len(raw_names)}, resolving {len(keep)}. "
              + ", ".join(f"{k}={len(v)}" for k, v in dropped.items() if v))
        # Every dropped name is printed, never a head slice. This list IS the record of what a
        # run chose not to look for; truncating it reads as "the page named that many", which is
        # the same silent-cap failure the repo's other reports are careful to avoid. In
        # particular `unprovable` is where a single-token brand name lands (measured on the
        # College Transitions table: CyberPatriot, DECA, iGEM, Model UN) — `title_proves` needs
        # two identity words, so those cannot be verified here and must be visible to a person.
        for reason, names in dropped.items():
            for n in names:
                print(f"    dropped ({reason}): {n}")

        for name in keep:
            try:
                notes, _usage, grounding, c, _att = so.research_seed(
                    resolve_angle(name), "", today, gemini_key, _A)
                cost += c
                searched += 1
                resolved = [x["url"] for x in url_validate.resolve_grounding_chunks(grounding)
                            if x.get("url")]
                url = best_resolved_url(resolved, name, timeout=url_validate.DEFAULT_TIMEOUT)
            except Exception as e:
                errors += 1
                print(f"  [WARN] resolve failed for {name!r}: {str(e)[:120]}")
                continue
            if not url:
                print(f"  [UNPROVEN] {name}  — no grounding page whose title proves it; wrote nothing.")
                continue
            exact, _ = url_dedupe.find_duplicates(url, name, existing)
            if exact:
                print(f"  [DUPE] {name} -> {url} already in the catalog as {exact}.")
                continue
            try:
                cand, c = mine_hub_pages.extract_opportunity(url, gemini_key, timeout=args.timeout,
                                                             min_delay=args.min_delay)
                cost += c
            except Exception as e:
                errors += 1
                print(f"  [WARN] extract failed for {url}: {str(e)[:120]}")
                continue
            # The page proved the NAME; keep the page's own name only if it also proves. The
            # harvested name is the fallback, never overwritten by an unproven one.
            cand = dict(cand or {})
            cand.setdefault("name", name)
            row = build_row(cand, next(mint), source, url, [])
            if not row:
                continue
            row["found_via"] = hub_url
            review_by_id[row["id"]] = {
                "moderation_status": "pending_review", "dup_candidates": None,
                "quality_flags": _row_flags(url, row.get("name"), row.get("org"), cand,
                                            source_url=hub_url) or None}
            rows.append(row)
            existing.append({"id": row["id"], "name": row["name"], "url": row["url"]})
            print(f"  [RESOLVED] {name} -> {url}")

    stamp = snapshot_stamp()
    review_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               f"names_review_{args.mode}_{stamp}.json")
    with open(review_path, "w", encoding="utf-8") as f:
        json.dump({"inserted": [{**r, "review": review_by_id.get(r["id"], {})} for r in rows],
                   "rejected": [], "merged": []}, f, indent=2, ensure_ascii=False)

    if args.dry_run:
        print(f"[DRY RUN] Resolved {len(rows)} row(s); NOTHING written. The run is still logged "
              f"to agent_runs (it cost real money).")
    elif rows:
        tier = insert_rows(supabase_url, service_key, rows, review_by_id)
        print(f"[OK] Inserted {len(rows)} row(s) into opportunities "
              f"(is_active=false, pending_review, tier={tier}).")
    else:
        print("[OK] No name resolved to a proven page — nothing to insert.")

    if run_id is not None:
        supabase_patch(supabase_url, "agent_runs", {"id": f"eq.{run_id}"}, {
            "finished_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "items_processed": searched,
            "items_added": 0 if args.dry_run else len(rows),
            "errors": errors,
            "cost_usd": round(cost, 4),
            "notes": (f"pages={len(hubs)}, named={named}, searched={searched}, "
                      f"resolved={len(rows)}, source-date={today}"
                      + (f", would_have_added={len(rows)}" if args.dry_run else "")),
        }, service_key)

    if lead_urls and not args.dry_run:
        # Real runs only — a dry run read the names but wrote no rows, so the lead is not done.
        import discovered_leads
        n = discovered_leads.mark_processed(lead_urls)
        print(f"[OK] Marked {n} listicle lead(s) processed.")

    print(f"[SUMMARY] {len(hubs)} page(s) named {named}, searched {searched}, resolved "
          f"{len(rows)} row(s), errors {errors}, cost ${cost:.4f}. Wrote {review_path}.")
    print(f"[DONE] Review before activating anything from a source='names-*-{today}' row.")


if __name__ == "__main__":
    main()

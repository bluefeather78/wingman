#!/usr/bin/env python3
"""PROTOTYPE — local-org hub-discovery feeder, stage 0 + stage 1 only.

Turns a natural-language request ("find programs in and around the Bay Area") into a
reviewed list of LOCAL organizations that run APPLICATION-BASED programs for high schoolers,
each with a grounding-resolved URL. That list is the input the existing hub miner
(mine_hub_pages.py) wants — but this prototype STOPS at candidate orgs: it does NOT resolve
hub URLs, emit discovered_leads, or mine. Those are the next phases, deliberately not built
yet so the enumeration QUALITY can be judged first (the whole reason to prototype).

Pipeline this belongs to (only the first two boxes exist here):
    NL request -> [region brief] -> [archetype x region org discovery] -> hub resolution ->
    discovered_leads -> mine_hub_pages.py -> review queue

DECISIONS baked in (operator, 2026-09-01):
  - Gemini-search only (no Google Places / external vendor).
  - APPLICATION-BASED programs only — a program a high schooler applies to or enrolls in, with
    an application/registration step. NOT one-off events, drop-in teen nights, or ticketed
    visits. This is enforced in the prompts (with examples) and is the dominant yield lever.

ACCURACY design, inherited from the scraper (do not "simplify" away):
  - TWO-PHASE per archetype (scrape_opportunities.research_seed): phase 1 prose + search keeps
    grounding chunks; phase 2 JSON, no search. A model-typed org URL is untrustworthy anywhere
    in this repo — the URL must come from a page the search actually retrieved (grounding),
    resolved by url_validate.resolve_grounding_chunks.
  - Silent-search retry is handled inside research_seed (a 0-search call is re-rolled once).
  - Breadth is forced by ARCHETYPE x REGION, with good/bad examples in the prompt — the house
    rule (concrete examples, not adjectives) that fixed the scraper's named-query rate.

COST / TIERS (MARQUEE M9 — a paid API call path):
  - default is --preview: FREE. Prints the region string, the archetypes, and the exact angle
    each WOULD search, then exits before any model call.
  - --live: PAID. Stage 0 (one small region-parse call) + stage 1 (two Gemini calls per
    archetype). Needs fresh explicit approval per run, like every paid agent here. Prints a
    running cost. Nothing is written to Supabase — output is a JSON/stdout review list only.

MARQUEE M8: the three prompts below are sent to a model. Any wording change is a marquee change
(approval first, dedicated commit). Written in house style — YES/NO examples, not adjectives.

    python local_org_discovery.py --request "programs in and around the Bay Area"            # FREE
    python local_org_discovery.py --request "programs in and around the Bay Area" --live     # PAID
    python local_org_discovery.py --request "Boston area STEM" --live --archetypes museums,universities
"""
import argparse
import datetime
import json
import os
import sys

import url_dedupe
import url_validate
from supabase_common import load_dotenv, supabase_get
from agent_common import safe_console

# The region-independent category set. Each is one archetype x region search "angle". Curated to
# LOCAL institutions that run application-based HS programs; a new metro reuses this list wholesale,
# only the region changes. Keep each label describable to a model as a category, not a single org.
ARCHETYPES = [
    ("museums", "science, art, and children's museums, and science centers"),
    ("zoos_gardens", "zoos, aquariums, and botanical gardens"),
    ("universities", "universities and community colleges with pre-college or high-school programs"),
    ("parks_rec", "city or county Parks & Recreation departments with teen programs"),
    ("libraries", "public library systems with teen programs"),
    ("clubs", "Boys & Girls Clubs, YMCA/YWCA, and similar youth organizations"),
    ("arts", "arts, music, theater, and cultural nonprofits with youth programs"),
    ("research", "hospitals, research institutes, and labs with high-school research programs"),
    ("stem_nonprofits", "STEM, coding, and maker nonprofits with structured programs"),
    ("workforce", "city youth-employment, workforce, or civic leadership offices"),
]
_ARCHETYPE_KEYS = [k for k, _ in ARCHETYPES]

# --- prompts (MARQUEE M8) --------------------------------------------------------------------

REGION_SYSTEM = """\
You normalize a short natural-language request into a structured region for a high-school \
opportunity finder. Return STRICT JSON and nothing else:
{"region": "<canonical metro/area name>", "places": ["<constituent city/county>", ...], \
"nearby": true|false}

- "region" is the canonical name of the area the user means. "the Bay Area" -> "San Francisco \
Bay Area"; "around Boston" -> "Greater Boston".
- "places" lists the specific cities/counties a local high schooler could realistically commute \
to within that area. Bay Area -> ["San Francisco","Oakland","Berkeley","San Jose","Palo Alto", \
"Peninsula","Marin County"]. Keep it to the commutable core, 4-10 entries.
- "nearby" is true when the request says "around"/"near"/"and surrounding" — include adjacent \
towns — and false when it names one exact city only.
- If the request names no place at all, return {"region": null, "places": [], "nearby": false}.
"""

# {today} and {angle} are filled by research_seed. {angle} already carries the archetype + region.
DISCOVER_SYSTEM = """\
Today is {today}. You are finding LOCAL organizations that run APPLICATION-BASED programs for \
HIGH SCHOOL students, for this category and area: {angle}

Search the web now, then write up what you find as prose (not a list of bare names). For each \
organization give its name and the URL of its own website that the search returned.

These programs are usually NOT labelled "high school internship". They hide behind other \
language, and you must search for THAT language, not just the word "internship":
- program words: internship, fellowship, apprenticeship, mentorship, research program, \
  pre-college, summer program, youth program, student program.
- HIDDEN words that mean the same thing: "youth council", "teen advisory board", "career quest", \
  "career exploration", "research training", "student researchers", "student fellows", \
  "young scholars", "work-based learning", "open to secondary students", "for students interested \
  in", "educational outreach", "pathways program".
Run several searches mixing this category with those terms and the place names. A program that \
calls itself a "Youth Research Training Program" counts exactly as much as one called an \
"internship" — judge what it IS (an application-based program for high schoolers), not what it is \
named.

What counts (include):
- A LOCAL organization physically in or serving this specific area, that runs a program a high \
schooler APPLIES TO or ENROLLS IN — an application, a registration step, or set cohort dates.
  YES: "Oakland Museum of California runs a Teen Council you apply to each fall."
  YES: "The city Parks & Rec department runs a summer Junior Lifeguard program with registration."
  YES: "A local university's pre-college summer program with an application deadline."

What does NOT count (exclude):
- NATIONAL programs that merely have a location or chapter here. NO: a nationwide contest, an \
online course, a franchise with no local application of its own.
- One-off EVENTS, drop-in teen nights, open houses, ticketed visits, or memberships — these are \
not application-based programs. NO: "Teen Night at the museum", "buy a membership".
- Blogs, listicles, or directories ABOUT programs (those name other people's programs).

Name real organizations you actually found in the search. If the area has few, say so honestly \
rather than padding with national names.
"""

ORG_EXTRACT_SYSTEM = """\
You are extracting a clean list of LOCAL organizations from research notes. Return STRICT JSON: \
an array of {"name": "...", "url": "...", "why": "<one short sentence: the application-based \
program(s) they run for high schoolers>"} and nothing else.

Rules:
- Copy each "url" VERBATIM from the SOURCE PAGES list you are given — never write a URL from \
memory. If an organization in the notes has no matching retrieved URL, omit it.
- Include an organization ONLY if the notes show it is LOCAL to the area AND runs an \
APPLICATION-BASED program (an application, registration, or set cohort dates) for high schoolers. \
The program need NOT be called an "internship" — a "youth council", "research training program", \
"teen advisory board", or "career quest" counts if a high schooler applies or enrols. Judge what \
it is, not what it is named.
- EXCLUDE national programs with a mere local presence, one-off events, drop-in offerings, \
ticketed visits, memberships, and blogs/listicles/directories about other people's programs.
- One entry per organization. Prefer the organization's own homepage or its programs/teens page.
"""


# --- helpers ---------------------------------------------------------------------------------

def _load_catalog_domains():
    """Registrable domains already in the catalog, so discovery can skip orgs we plainly have.
    Best-effort: returns an empty set (and warns) if Supabase is unreachable."""
    load_dotenv()
    supa = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_ANON_KEY", "")
    if not supa or not key:
        print("  [warn] no Supabase creds — skipping catalog dedup")
        return set()
    try:
        rows = supabase_get(supa, "opportunities", {"select": "url", "is_active": "eq.true"}, key)
    except Exception as e:
        print(f"  [warn] catalog read failed ({type(e).__name__}) — skipping dedup")
        return set()
    doms = set()
    for r in rows:
        _, host, _, _ = url_dedupe.split_url(r.get("url") or "")
        d = url_dedupe.registrable_domain(host)
        if d:
            doms.add(d)
    return doms


def parse_region(request, gemini_key, timeout):
    """Stage 0: NL -> region brief. One small paid call. Returns (brief_dict, cost)."""
    from gemini_common import call_gemini, extract_json, estimate_cost
    out, usage = call_gemini(REGION_SYSTEM, request, gemini_key, use_web_search=False,
                             max_tokens=400, timeout=timeout)
    brief = extract_json(out)
    if not isinstance(brief, dict):
        brief = {"region": None, "places": [], "nearby": False}
    return brief, estimate_cost(usage)


def _angle(archetype_label, brief):
    place_hint = ", ".join(brief.get("places") or []) or brief.get("region") or ""
    around = " and surrounding towns" if brief.get("nearby") else ""
    return f"{archetype_label} in {brief.get('region')} ({place_hint}){around}"


def discover_archetype(brief, archetype_label, gemini_key, args):
    """Stage 1 for ONE archetype: two-phase (prose+grounding -> JSON). Returns
    (orgs, cost, searches). Each org is {name, url, why, archetype}."""
    from gemini_common import call_gemini, extract_json, estimate_cost
    from scrape_opportunities import research_seed

    today = datetime.date.today().isoformat()
    angle = _angle(archetype_label, brief)

    class _A:
        timeout = args.timeout
        max_searches = args.max_searches

    notes, usage, grounding, cost, _att = research_seed(
        angle, "", today, gemini_key, _A, system=DISCOVER_SYSTEM)
    searches = (usage.get("server_tool_use") or {}).get("web_search_requests", 0)

    resolved = [x["url"] for x in url_validate.resolve_grounding_chunks(grounding) if x.get("url")]
    if not resolved:
        return [], cost, searches

    source_block = "\n".join(f"- {u}" for u in resolved)
    user = (f"SOURCE PAGES actually retrieved (copy these verbatim where they match):\n"
            f"{source_block}\n\nRESEARCH NOTES:\n{notes}\n\nReturn the JSON array now.")
    text, usage2 = call_gemini(ORG_EXTRACT_SYSTEM, user, gemini_key, use_web_search=False,
                               max_tokens=2000, timeout=args.timeout)
    cost += estimate_cost(usage2)
    orgs = extract_json(text)
    if not isinstance(orgs, list):
        orgs = [orgs] if isinstance(orgs, dict) else []
    return orgs, cost, searches


# --- stage 2: org -> hub URL -> discovered_leads (FREE: fetch + regex, no model) ------------

def resolve_hub_url(org_url, timeout=30):
    """Promote an org/program URL to the org's PROGRAMS INDEX where one is cheaply findable, so the
    miner enumerates a family rather than extracting a single program. FREE. Returns (url, via).

    Reuses the miner's own link machinery (harvest_links / filter_hub_links), so "what is an index
    link" agrees with what the miner will later follow. Ladder, cheapest first:
      sub-hub-index  : the page links a labelled index ("Programs", "Teens") -> use it.
      self-index     : the page itself already links several in-scope programs -> keep it.
      homepage-index : neither, but the org homepage links a programs/teens index -> use that.
      kept-leaf      : none found -> keep the given URL; the miner still classifies+extracts it.
    A fetch failure is never fatal — we keep the URL and let the miner (which fetches again) decide.
    """
    from mine_hub_pages import fetch_html, harvest_links, filter_hub_links

    def index_from(u):
        html = fetch_html(u, timeout)
        if not html:
            return None, 0
        kept, subs = filter_hub_links(harvest_links(html, u), u, off_domain=False)
        if subs:
            return subs[0][0], len(kept)
        return None, len(kept)

    sub, kept_n = index_from(org_url)
    if sub:
        return sub, "sub-hub-index"
    if kept_n >= 3:
        return org_url, "self-index"

    scheme, host, path, _ = url_dedupe.split_url(org_url)
    root = f"{scheme}://{host}/"
    if host and path not in ("", "/"):
        hsub, hkept = index_from(root)
        if hsub:
            return hsub, "homepage-index"
        if hkept >= 3:
            return root, "homepage-index"
    return org_url, "kept-leaf"


def orgs_to_leads(orgs, region, timeout=30):
    """Resolve each org to a hub URL and shape a discovered_leads KIND_HUB / SCOPE_SAME_DOMAIN row.
    FREE. Returns the lead dicts (not yet written)."""
    import discovered_leads
    today = datetime.date.today().isoformat()
    leads = []
    for o in orgs:
        url = (o.get("url") or "").strip()
        if not url:
            continue
        hub_url, via = resolve_hub_url(url, timeout)
        leads.append({
            "url": hub_url, "kind": discovered_leads.KIND_HUB,
            "scope": discovered_leads.SCOPE_SAME_DOMAIN, "seed_id": None,
            "angle": f"local-org discovery: {region} / {o.get('archetype')}",
            "signal": f"{o.get('name')} — resolved {via}",
            "first_seen": today, "status": discovered_leads.STATUS_NEW})
        print(f"    ~ {o.get('name')}: {via}\n        {url}\n        -> {hub_url}")
    return leads


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--request", help='e.g. "programs in and around the Bay Area"')
    ap.add_argument("--from-json", default=None, help="Skip discovery; load orgs from a prior "
                    "--json output and run stage 2 (hub resolution + leads) on them. FREE.")
    ap.add_argument("--live", action="store_true", help="PAID: actually call Gemini (else preview).")
    ap.add_argument("--emit-leads", action="store_true", help="Stage 2: resolve each org to a hub "
                    "URL and append discovered_leads rows (FREE) for mine_hub_pages.py to drain.")
    ap.add_argument("--leads-path", default=None, help="Override the discovered_leads.jsonl path "
                    "(for testing without touching the real queue).")
    ap.add_argument("--archetypes", default="", help="comma-separated subset of: " +
                    ",".join(_ARCHETYPE_KEYS))
    ap.add_argument("--max-orgs", type=int, default=60, help="stop after this many unique orgs.")
    ap.add_argument("--max-searches", type=int, default=3, help="phase-1 search budget per archetype "
                    "(the dominant cost lever — each search bills).")
    ap.add_argument("--timeout", type=int, default=90)
    ap.add_argument("--json", default=None, help="also write the org list here.")
    args = ap.parse_args()

    chosen = [k for k in (args.archetypes.split(",") if args.archetypes else _ARCHETYPE_KEYS)
              if k.strip()]
    bad = [k for k in chosen if k not in _ARCHETYPE_KEYS]
    if bad:
        print(f"[ERROR] unknown archetype(s): {', '.join(bad)}")
        sys.exit(2)
    labels = {k: lbl for k, lbl in ARCHETYPES}

    def emit(orgs, region):
        """Stage 2: resolve hubs + append leads (FREE). Shared by --from-json and the live path."""
        import discovered_leads
        path = args.leads_path or discovered_leads.LEADS_PATH
        print(f"\n=== stage 2: resolving {len(orgs)} org(s) to hub URLs (free) ===")
        leads = orgs_to_leads(orgs, region, args.timeout)
        written = discovered_leads.append_leads(leads, path=path)
        print(f"\n[OK] {written} new KIND_HUB lead(s) -> {path} "
              f"({len(leads) - written} already queued). Drain with mine_hub_pages.py.")

    # Stage-2-only path: run hub resolution + leads on a prior discovery, no paid calls.
    if args.from_json:
        with open(args.from_json, encoding="utf-8") as f:
            saved = json.load(f)
        orgs = saved.get("orgs") or []
        region = (saved.get("brief") or {}).get("region") or saved.get("request") or "unknown"
        print(f"[from-json] {len(orgs)} org(s) from {args.from_json}, region={region!r}")
        emit(orgs, region)
        return

    if not args.request:
        print("[ERROR] pass --request (discovery) or --from-json (stage 2 on a prior run).")
        sys.exit(2)

    if not args.live:
        # FREE preview: no model call. Show what a live run WOULD search.
        print(f'PREVIEW (free). Request: "{args.request}"')
        print(f"Region parsing and the {len(chosen)} archetype searches below run only with --live.\n")
        for k in chosen:
            demo = _angle(labels[k], {"region": args.request, "places": [], "nearby": False})
            print(f"  [{k}] would search: {demo}")
        print("\nAdd --live to spend (Gemini, MARQUEE M9). Est ~2 calls/archetype + 1 region call.")
        return

    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        load_dotenv()
        key = os.environ.get("GEMINI_API_KEY")
    if not key:
        print("[ERROR] GEMINI_API_KEY not set."); sys.exit(1)

    known = _load_catalog_domains()
    print(f"[OK] {len(known)} catalog domain(s) loaded for dedup.\n")

    brief, cost = parse_region(args.request, key, args.timeout)
    print(f"Region brief: {json.dumps(brief, ensure_ascii=False)}  (cost ${cost:.4f})")
    if not brief.get("region"):
        print("[STOP] could not resolve a region from the request."); return

    seen_dom, results, total_cost = set(known), [], cost
    for k in chosen:
        print(f"\n=== archetype: {k} ===")
        orgs, acost, searches = discover_archetype(brief, labels[k], key, args)
        total_cost += acost
        print(f"  {searches} search(es), {len(orgs)} raw org(s), cost ${acost:.4f}")
        for o in orgs:
            url = (o.get("url") or "").strip()
            _, host, _, _ = url_dedupe.split_url(url)
            dom = url_dedupe.registrable_domain(host)
            if not dom:
                continue
            if dom in seen_dom:
                print(f"    - skip (dup domain): {o.get('name')}  [{dom}]")
                continue
            seen_dom.add(dom)
            row = {"name": o.get("name"), "url": url, "why": o.get("why"),
                   "archetype": k, "region": brief.get("region")}
            results.append(row)
            print(f"    + {o.get('name')}  |  {url}")
            if len(results) >= args.max_orgs:
                break
        if len(results) >= args.max_orgs:
            print(f"\n[cap] reached --max-orgs {args.max_orgs}")
            break

    print(f"\n=== {len(results)} unique candidate org(s) === total cost ${total_cost:.4f}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"request": args.request, "brief": brief, "cost_usd": round(total_cost, 4),
                       "orgs": results}, f, indent=1, ensure_ascii=False)
        print(f"[OK] wrote {args.json}")
    if args.emit_leads:
        emit(results, brief.get("region"))
    else:
        print("[NOTE] org candidates only — nothing queued. Add --emit-leads to resolve hubs and "
              "queue them for mine_hub_pages.py.")


if __name__ == "__main__":
    safe_console()
    main()

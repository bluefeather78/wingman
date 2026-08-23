#!/usr/bin/env python3
"""Reputation/review checker: for each active opportunity in the Supabase `opportunities`
catalog, searches (via web_search) for independent evidence of whether it's actually
worthwhile — Reddit/College Confidential/Niche-style discussion, complaints, red flags of
pay-to-play/predatory programs — versus relying only on the org's own marketing copy.

Run far less often than check_deadlines.py — an organization's reputation changes on the
scale of months, not days. By default this script only re-checks rows where
last_reviewed_at is null or more than STALE_AFTER_DAYS (30) days old, so running it more
often than that doesn't re-spend on rows that were just checked. Pass --force to ignore the
staleness filter entirely and re-check every active row.

Same hard rule as extractTrackerInfo's "never invent a date": this script must never
invent a review verdict from thin evidence. Most hyperlocal/niche opportunities won't
have any real independent review presence at all — review_status="insufficient_data" is
a valid, expected, common outcome, not a failure.

THREE THINGS PROTECT THAT RULE, all added 2026-08-23 from the scraper rewrite's findings:

1. TWO PHASES, BECAUSE ASKING FOR JSON IS WHY THIS AGENT NEVER SEARCHED. Until now it made
   one call that demanded a JSON object, and over its whole history that produced 22
   searches across 3089 row-checks — 0.007 per row. A controlled A/B (same row, same
   instructions, arms alternated, only the closing paragraph differing) came out prose 4/4
   searched against JSON 0/4. So phase 1 asks for PROSE and searches; phase 2 turns those
   notes into the schema with no search at all, which is where a strict format is free.
   See gemini_common.py's SEVENTH finding — including the limit on what it proves, because
   an earlier session over-claimed this and had to retract. Do not collapse the two calls.

2. A SILENT CALL WRITES NOTHING. The search decision is still non-deterministic and still
   cannot be forced (gemini_common.py's THIRD finding is correct; do not try again).
   research_reviews() re-sends the identical prompt once — cheap, because a silent call
   pays no per-search fee — and if the retry is silent too the row is SKIPPED. No verdict,
   and crucially no last_reviewed_at stamp, so it stays due instead of being hidden from
   the staleness filter for 30 days behind a verdict nothing was consulted for. Phase 2 is
   skipped as well, so a silent row costs about what the old design cost for everything.

3. review_sources URLS COME FROM THE SEARCH, NOT FROM MEMORY. They are shown to students as
   the evidence behind the verdict, and a model-typed URL is not trustworthy: measured
   2026-08-23, 199 of the 1469 source URLs already in the catalog (13.5%) were dead. Phase 2
   is handed the URLs resolved from phase 1's groundingChunks and told to copy them, and
   clean_sources() then marks each kept source `retrieved: true/false` and drops any
   unretrieved one an HTTP check finds dead. 403/timeout is kept — that is a site blocking
   the checker, not a fabricated citation, and it matters here because 61% of the catalog's
   existing source URLs sit on hosts (Reddit, College Confidential) that never answer a
   checker at all. `retrieved: true` is the only real proof for those, which is exactly what
   phase 1's grounding now supplies.

MEASURED COST, 2026-08-23: $0.0166 for a row that searched once — roughly $20 for a full
1226-row pass, or ~$4 per tranche across the five the 30-day staleness window creates. The
old single-call design cost $1.66 a pass and verified essentially nothing. MAX_SEARCHES is
the lever if that needs to move.

SETUP:
    .env needs SUPABASE_URL, SUPABASE_SERVICE_KEY, GEMINI_API_KEY.
    Run this SQL once in the Supabase SQL editor before first use:

        alter table opportunities
          add column review_status text,     -- 'positive' | 'mixed' | 'negative' | 'insufficient_data'
          add column review_summary text,     -- one-line basis for the verdict
          add column review_sources jsonb,    -- [{ "url": "...", "note": "..." }, ...]
          add column last_reviewed_at timestamptz;

    (Also needs the agent_runs table — see check_deadlines.py's docstring if not
    already created.)

USAGE:
    python check_reviews.py --sample 20   # random 20-row sample of stale/unchecked rows
    python check_reviews.py --all         # every stale/unchecked active row
    python check_reviews.py --all --force # ignore the staleness filter, recheck everything
"""
import argparse
import datetime
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse

from agent_common import add_agent_args, apply_timing, emit_preview, snapshot_stamp
from gemini_common import call_gemini, extract_json, estimate_cost
import url_validate
from supabase_common import load_dotenv, supabase_get, supabase_insert_one, supabase_patch

VALID_STATUS = {"positive", "mixed", "negative", "insufficient_data"}
# How old a review has to be before a default run re-checks it. Lowered from 182 (~6
# months) to 30 on 2026-08-22 so an ad-hoc run picks up genuinely aging rows instead of
# doing nothing at all for half a year — the previous value meant the only way to make
# this agent do anything between passes was --force, i.e. the whole catalog.
# It still exists to keep a plain run idempotent: rows checked yesterday are not re-paid
# for. Raising it makes runs cheaper and staler; lowering it, the reverse.
STALE_AFTER_DAYS = 30


# How many searches phase 1 is asked to make. A SOFT budget on Gemini — it is folded into
# the prompt, not enforced server-side, so the model can exceed it (unlike Claude's
# max_uses). Set to 1 deliberately: at $0.014 per search this is the single biggest lever
# on what a full pass costs, and one good search is what separates a verified verdict from
# an invented one. See the SEVENTH finding in gemini_common.py.
MAX_SEARCHES = 1


def build_research_system(opp):
    """PHASE 1 — prose out, search on. The output format here is load-bearing, not cosmetic.

    This prompt used to end by demanding a JSON object, and that is measurably why this
    agent never searched: 22 searches across 3089 row-checks in its whole history. A
    controlled A/B on 2026-08-23 (same row, same instructions, arms alternated, only the
    closing paragraph differing) came out prose 4/4 searched vs JSON 0/4, with 34 grounding
    chunks against 0. See gemini_common.py's SEVENTH finding for the numbers and for the
    limit on what it proves.

    So: ask for prose here and let extract_review() turn it into JSON in a second,
    search-free call. Do NOT "simplify" this back into one call that asks for JSON.
    """
    return build_research_body(opp) + """

Write up what you find in plain prose. Say what independent commentary actually exists, \
paraphrase the substantive parts, name the sources you genuinely consulted, and finish with \
your overall read: positive, mixed, negative, or insufficient data. No JSON, no schema, no \
markdown fences — just write it. Stay well within a 500-token response."""


def build_research_body(opp):
    """The research instructions both phases share. Kept separate so the two prompts cannot
    drift apart on WHAT counts as evidence while differing only on output shape."""
    return f"""You research the real-world reputation of an extracurricular opportunity (program, \
internship, competition, or research position) for high school students, for a catalog used by \
students and families deciding whether it's worth their time and money.

Search thoroughly with web_search for INDEPENDENT evidence — not the organization's own marketing \
copy. Look specifically for: Reddit threads, College Confidential or Niche.com discussion, parent/\
student forum posts, news coverage, Better Business Bureau or consumer-complaint listings, and any \
other independent commentary. Watch for red flags of low-quality or predatory programs: "pay-to-play" \
setups where acceptance is really just a fee payment, journals/conferences that publish or accept \
nearly everyone who pays, high-pressure marketing, complaints about non-refundable deposits or hidden \
fees, or no verifiable outcomes for past participants. Do NOT treat being expensive or selective, by \
itself, as a red flag — many legitimate, well-regarded programs are both. Only flag negative/mixed \
based on real complaints, credible criticism, or verifiable red flags you actually found.

This matters a lot: most opportunities — especially small or local ones — will have NO independent \
review presence at all. That is a completely normal, expected result. Use "insufficient_data" whenever \
you don't find real independent evidence either way — never invent or infer a sentiment just to give a \
verdict. Only use "positive", "mixed", or "negative" when you found actual independent commentary to \
support it."""


EXTRACT_SYSTEM = """You turn a researcher's written notes about one extracurricular \
opportunity's reputation into a strict JSON record. You are NOT researching — everything you \
need is in the notes, and you must not add anything that is not in them.

You are also given the list of pages the researcher's search actually retrieved. When you cite \
a source, COPY a URL from that list verbatim. Do not write a URL from memory and do not repair, \
shorten or guess at one: a URL that was not retrieved is a fabricated citation, and this field \
is shown to students as the evidence behind the verdict.

If the notes describe no real independent commentary, the answer is "insufficient_data" — that \
is a normal, common, correct outcome, not a failure. Never upgrade it to a sentiment the notes \
do not support.

Respond with ONLY a raw JSON object, no markdown fences, no preamble, no text after the JSON, \
matching exactly this schema: {"review_status": "positive, mixed, negative, or \
insufficient_data", "review_summary": "one short sentence: the basis for this verdict, or why \
data was insufficient", "review_sources": [{"url": "...", "note": "under 15 words on what this \
source said"}]}. At most 3 review_sources entries — pick the most substantive ones. Stay well \
within a 500-token response."""


def clean_sources(sources, grounding):
    """Validate the model's review_sources against what the search actually retrieved.

    Two problems, both measured elsewhere in this repo and both landing in a field that is
    shown to STUDENTS as evidence for a legitimacy verdict:

    1. A model-typed URL is unreliable. In the 2026-08-20 scrape batch 30 of 116 (26%) were
       hard 404s, every one a constructed deep path. There is no reason to expect a citation
       URL to be typed more carefully than a program URL.
    2. `groundingChunks[].web.uri` holds the URL the search really returned. It is the only
       place the real URL exists — the answer text does not carry it back. Resolving it is
       one free HTTP hop.

    So a source URL is kept if it is among the retrieved pages (verified by construction),
    or if it is not but a plain HTTP check finds it live. One that is neither is dropped,
    with its note preserved in `sources_dropped` on the caller's side so the loss is
    visible rather than silent. Both checks are free — no API call, no key.
    """
    out, dropped = [], []
    if not sources:
        return out, dropped
    resolved = {r["url"] for r in url_validate.resolve_grounding_chunks(grounding or {})
                if r.get("url")}
    unknown = [s for s in sources if s.get("url") not in resolved]
    health = url_validate.check_urls([s["url"] for s in unknown]) if unknown else {}
    for s in sources:
        url = s.get("url")
        if url in resolved:
            out.append({**s, "retrieved": True})
            continue
        status = (health.get(url) or {}).get("status")
        if status == url_validate.DEAD:
            dropped.append({**s, "reason": (health.get(url) or {}).get("code")})
        else:
            # Live, or unverifiable (403/timeout) — kept, because a site blocking this
            # checker is not evidence the citation is fake, and dropping on 403 would bin
            # ~9% of perfectly real sources.
            out.append({**s, "retrieved": False})
    return out, dropped


def research_reviews(opp, api_key):
    """PHASE 1 — prose out, search on. (notes, cost, searches, grounding, attempts).

    Retries once on a zero-search response. Retrying rather than re-prompting is the only
    mitigation that works: the search decision is non-deterministic and cannot be forced
    (gemini_common.py's THIRD finding). A silent call is also the cheap one — it pays no
    per-search fee — so the retry costs a fraction of a real check.

    Cost is banked per attempt, not after the loop: an exception on the retry would
    otherwise discard money the first call already spent.
    """
    system = build_research_system(opp)
    user_content = (f"Opportunity: {opp['name']} ({opp.get('org') or 'unknown org'})\n"
                    f"URL: {opp['url']}\nKnown info: {opp.get('summary') or ''}\n\n"
                    f"Search for independent reviews and reputation evidence, then report "
                    f"what you found.")
    cost = 0.0
    notes, usage, extra = "", {}, {}
    for attempt in (1, 2):
        # max_tokens 1200 and thinking_level "low" (call_gemini's default) are load-bearing:
        # at 700, gemini-3.6-flash's thinking tokens alone consumed ~673 and starved the
        # visible output. See gemini_common.py's FOURTH finding.
        notes, usage, extra = call_gemini(system, user_content, api_key, use_web_search=True,
                                          max_tokens=1200, max_searches=MAX_SEARCHES,
                                          return_grounding=True)
        cost += estimate_cost(usage)
        tool_use = usage.get("server_tool_use") or {}
        searches = tool_use.get("web_search_requests", 0)
        for q in tool_use.get("web_search_queries") or []:
            print(f"[search: {q}]", end=" ")
        if searches:
            return notes, cost, searches, extra.get("grounding") or {}, attempt
        if attempt == 1:
            print("[SILENT] no search invoked — retrying once", end=" ")
    return notes, cost, 0, extra.get("grounding") or {}, 2


def extract_review(notes, retrieved_urls, api_key):
    """PHASE 2 — notes plus the REAL retrieved URLs in, strict JSON out. No search.

    Supplying the resolved URLs is the same trick the scraper's phase 2 uses: the model
    copies a URL that was actually fetched instead of recalling one that merely looks right.
    A JSON-only call needs no search, so demanding a strict format is free HERE — the cost
    of that format is paid in phase 1, which is exactly why phase 1 does not use it.
    """
    source_block = "\n".join(f"- {u}" for u in retrieved_urls) or "(none retrieved)"
    user_content = (f"PAGES THE SEARCH ACTUALLY RETRIEVED (copy these verbatim when citing):\n"
                    f"{source_block}\n\nRESEARCHER'S NOTES:\n{notes}\n\n"
                    f"Return the JSON object now.")
    text, usage = call_gemini(EXTRACT_SYSTEM, user_content, api_key, use_web_search=False,
                              max_tokens=1200)
    return extract_json(text) or {}, estimate_cost(usage)


def check_one(opp, api_key):
    """Both phases. (info, cost, searches, grounding, attempts).

    Split because asking for JSON collapses the search rate — measured 4/4 vs 0/4 in a
    controlled A/B, against a historical 22 searches per 3089 row-checks on the old
    single-call JSON prompt. gemini_common.py's SEVENTH finding carries the numbers and the
    limit on what they prove. Do not collapse these back into one call.

    Phase 2 is SKIPPED when phase 1 stayed silent: there is nothing worth extracting from
    notes written without looking, main() will not write the row either way, so a second
    call would buy nothing. That also keeps a fully silent row near the old per-row price.
    """
    notes, cost, searches, grounding, attempts = research_reviews(opp, api_key)
    if not searches:
        return {}, cost, 0, grounding, attempts
    retrieved = [r["url"] for r in url_validate.resolve_grounding_chunks(grounding)
                 if r.get("url")]
    info, extract_cost = extract_review(notes, retrieved, api_key)
    return info, cost + extract_cost, searches, grounding, attempts


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--sample", type=int, help="Check a random N-row sample instead of all stale/unchecked rows.")
    group.add_argument("--all", action="store_true", help="Check every stale/unchecked active row (default if no flag given).")
    parser.add_argument("--force", action="store_true", help="Ignore the staleness filter and recheck every active row.")
    parser.add_argument("--dry-run", action="store_true", help="No writes (opportunities or agent_runs) — just prints "
                                                                 "and dumps results to a local JSON review file.")
    add_agent_args(parser, default_timeout=120)
    args = parser.parse_args()
    apply_timing(args, gemini=True)

    load_dotenv()
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    service_key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    if not supabase_url or not service_key or not gemini_key:
        print("[ERROR] SUPABASE_URL / SUPABASE_SERVICE_KEY / GEMINI_API_KEY not set in .env.")
        sys.exit(1)

    params = {
        "select": "id,name,org,url,summary,review_status,last_reviewed_at",
        "is_active": "eq.true",
        # Stable ordering, so an interrupted batch resumes over the same sequence rather
        # than whatever order PostgREST happens to return.
        "order": "id",
    }
    # There used to be a `"review_status": "is.null"` filter here, added to protect the
    # August 2026 backlog from being re-paid for on every resume. It was removed once that
    # backlog finished (2026-08-22): with every active row now carrying a verdict, it
    # matched nothing, which made STALE_AFTER_DAYS below dead code and meant this agent
    # could never re-check anything again. Staleness is the only recheck rule now.
    if not args.force:
        cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=STALE_AFTER_DAYS)).isoformat()
        params["or"] = f"(last_reviewed_at.is.null,last_reviewed_at.lt.{cutoff})"

    print("[OK] Fetching active catalog rows never reviewed or due for a re-check...")
    candidates = supabase_get(supabase_url, "opportunities", params, service_key)
    print(f"[OK] {len(candidates)} row(s) due for a review check"
          f"{' (staleness filter ignored)' if args.force else f' (unchecked or >{STALE_AFTER_DAYS} days stale)'}.")

    mode = "all"
    items = candidates
    if args.sample:
        mode = "sample"
        items = random.sample(candidates, min(args.sample, len(candidates)))

    # Preview: scope resolved, report and stop before the first (paid) Gemini call.
    if args.preview:
        emit_preview(len(items), "rows", [o.get("name", "?") for o in items],
                     mode=mode + ("-force" if args.force else ""))
        return

    if not items:
        print("[OK] Nothing due for a review check right now.")
        return

    # A dry run logs its agent_runs row too. It skips DATABASE writes, not API calls —
    # it costs exactly as much as a live run — so omitting the row (as this used to do)
    # made real money invisible in every cost total. The "-dryrun" mode suffix is how
    # readers tell the two apart; it avoids needing a new column on agent_runs.
    run_mode = mode + ("-force" if args.force else "") + ("-dryrun" if args.dry_run else "")
    run_row = supabase_insert_one(supabase_url, "agent_runs", {
        "agent": "review_checker",
        "mode": run_mode,
        "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }, service_key)
    run_id = run_row["id"] if run_row else None

    total_cost = 0.0
    updated = 0
    errors = 0
    total_searches = 0
    silent_search_count = 0
    retried = 0
    sources_dropped = 0
    dry_run_results = []

    for i, opp in enumerate(items):
        print(f"[{i + 1}/{len(items)}] {opp['name'][:60]}...", end=" ")
        try:
            info, cost, searches, grounding, attempts = check_one(opp, gemini_key)
            total_cost += cost
            total_searches += searches
            retried += attempts - 1

            # Still silent after the retry. The call is paid for either way, but what it
            # produced is not a review verdict — nothing independent was consulted, and this
            # agent's own rule is that it "must never invent a review verdict from thin
            # evidence". Writing it anyway would be worse than dropping it twice over: the
            # verdict is indistinguishable from a real one, AND stamping last_reviewed_at
            # alongside it would hide the row from the staleness filter for 30 days, so the
            # fabrication would outlive the run that made it. Skipping leaves the row due,
            # and the next pass re-rolls the search decision.
            if searches == 0:
                silent_search_count += 1
                print("[SILENT after retry] no verdict written; row stays due for a "
                      f"re-check, ${cost:.4f}")
                continue

            status = info.get("review_status") if info.get("review_status") in VALID_STATUS else "insufficient_data"
            sources = info.get("review_sources") or []
            if not isinstance(sources, list):
                sources = []
            sources = [s for s in sources if isinstance(s, dict) and s.get("url")][:3]
            # Free: grounding resolution plus an HTTP check. 199 of 1469 review_source URLs
            # in the live catalog (13.5%) were dead when measured on 2026-08-23 — these are
            # shown to students as the evidence behind a legitimacy verdict.
            sources, dropped = clean_sources(sources, grounding)
            sources_dropped += len(dropped)
            changed = status != opp.get("review_status")
            if changed:
                updated += 1
            if args.dry_run:
                dry_run_results.append({
                    "id": opp["id"],
                    "name": opp["name"],
                    "url": opp["url"],
                    "previous_review_status": opp.get("review_status"),
                    "review_status": status,
                    "review_summary": info.get("review_summary"),
                    "review_sources": sources,
                    "sources_dropped": dropped,
                    "web_searches": searches,
                    "attempts": attempts,
                    "cost_usd": round(cost, 4),
                })
            else:
                now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
                supabase_patch(supabase_url, "opportunities", {"id": f"eq.{opp['id']}"}, {
                    "review_status": status,
                    "review_summary": info.get("review_summary"),
                    "review_sources": sources,
                    "last_reviewed_at": now_iso,
                    "updated_at": now_iso,
                }, service_key)
            drop_note = f", {len(dropped)} dead source(s) dropped" if dropped else ""
            print(f"{status}, {searches} search(es){drop_note}, ${cost:.4f}"
                  + (" [changed]" if changed else ""))
        except urllib.error.HTTPError as e:
            errors += 1
            print(f"[ERROR] HTTP {e.code}")
        except Exception as e:
            errors += 1
            print(f"[ERROR] {e}")
        # Rate limiting is now enforced at the API level in gemini_common.call_gemini()
        # (minimum 5 seconds between calls per Gemini's documented rate limit policy),
        # so explicit throttle here is no longer needed.

    print(f"\n[SUMMARY] checked: {len(items)}, changed: {updated}, errors: {errors}, "
          f"cost: ${total_cost:.4f}")
    print(f"[SUMMARY] silent-search retries: {retried}, still silent after retry (no verdict "
          f"written, row stays due): {silent_search_count}/{len(items)}, "
          f"dead source URLs dropped: {sources_dropped}")
    if mode == "sample" and items:
        per_item = total_cost / len(items)
        projected = per_item * len(candidates)
        print(f"[PROJECTED] ~${per_item:.4f}/item -> all {len(candidates)} due rows "
              f"~${projected:.2f} for a full pass.")

    if args.dry_run:
        # Seconds, not just the date — see snapshot_stamp(). Same-day runs used to
        # overwrite each other's already-paid-for output.
        stamp = snapshot_stamp()
        review_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    f"review_check_dry_run_{stamp}.json")
        with open(review_path, "w", encoding="utf-8") as f:
            json.dump(dry_run_results, f, indent=2, ensure_ascii=False)
        print(f"[OK] Wrote dry-run review snapshot: {review_path}")
        print("[DRY RUN] No opportunities were written. The run itself is still logged to "
              "agent_runs (mode='%s') because it cost real money." % run_mode)

    if run_id is not None:
        supabase_patch(supabase_url, "agent_runs", {"id": f"eq.{run_id}"}, {
            "finished_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "items_processed": len(items),
            "items_updated": updated,
            "errors": errors,
            "cost_usd": round(total_cost, 4),
            "total_web_searches": total_searches,
            "silent_search_count": silent_search_count,
        }, service_key)
        print(f"[OK] Logged agent_runs id={run_id}.")


if __name__ == "__main__":
    main()

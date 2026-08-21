#!/usr/bin/env python3
"""Reputation/review checker: for each active opportunity in the Supabase `opportunities`
catalog, searches (via web_search) for independent evidence of whether it's actually
worthwhile — Reddit/College Confidential/Niche-style discussion, complaints, red flags of
pay-to-play/predatory programs — versus relying only on the org's own marketing copy.

Run far less often than check_deadlines.py (1-2x/year is the intent, not monthly): by
default this script only re-checks rows where last_reviewed_at is null or more than 6
months old, so accidentally running it more often doesn't re-spend on rows that were
just checked. Pass --force to ignore that staleness filter.

Same hard rule as extractTrackerInfo's "never invent a date": this script must never
invent a review verdict from thin evidence. Most hyperlocal/niche opportunities won't
have any real independent review presence at all — review_status="insufficient_data" is
a valid, expected, common outcome, not a failure.

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

from gemini_common import call_gemini, extract_json, estimate_cost
from supabase_common import load_dotenv, supabase_get, supabase_insert_one, supabase_patch

VALID_STATUS = {"positive", "mixed", "negative", "insufficient_data"}
STALE_AFTER_DAYS = 182  # ~6 months


def build_system(opp):
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
support it.

Respond with ONLY a raw JSON object, no markdown fences, no preamble, no text after the JSON, matching \
exactly this schema: {{"review_status": "positive, mixed, negative, or insufficient_data", \
"review_summary": "one short sentence: the basis for this verdict, or why data was insufficient", \
"review_sources": [{{"url": "...", "note": "under 15 words on what this source said"}}]}}. At most 3 \
review_sources entries — pick the most substantive ones. Stay well within a 500-token response."""


def check_one(opp, api_key):
    system = build_system(opp)
    user_content = (f"Opportunity: {opp['name']} ({opp.get('org') or 'unknown org'})\n"
                     f"URL: {opp['url']}\nKnown info: {opp.get('summary') or ''}\n\n"
                     f"Search for independent reviews/reputation evidence and extract per the schema.")
    # max_tokens raised from 700 -> 1200 and thinking_level defaults to "low" in
    # call_gemini() as of 2026-08-18: at 700, gemini-3.6-flash's thinking tokens alone
    # (~673 of 700 without thinking_level control) starved the visible JSON output,
    # silently truncating review_summary/review_sources. See gemini_common.py's
    # "FOURTH finding" docstring for the full root-cause writeup.
    text, usage = call_gemini(system, user_content, api_key, use_web_search=True, max_tokens=1200)
    info = extract_json(text)
    searches = (usage.get("server_tool_use") or {}).get("web_search_requests", 0)
    return info, estimate_cost(usage), searches


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--sample", type=int, help="Check a random N-row sample instead of all stale/unchecked rows.")
    group.add_argument("--all", action="store_true", help="Check every stale/unchecked active row (default if no flag given).")
    parser.add_argument("--force", action="store_true", help="Ignore the staleness filter and recheck every active row.")
    parser.add_argument("--dry-run", action="store_true", help="No writes (opportunities or agent_runs) — just prints "
                                                                 "and dumps results to a local JSON review file.")
    args = parser.parse_args()

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
        "review_status": "is.null",  # TEMP: only rows without review status
        "order": "id",  # TEMP: stable ordering
    }
    if not args.force:
        cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=STALE_AFTER_DAYS)).isoformat()
        params["or"] = f"(last_reviewed_at.is.null,last_reviewed_at.lt.{cutoff})"

    print("[OK] Fetching all active + unchecked (review_status IS NULL) catalog rows from Supabase...")
    candidates = supabase_get(supabase_url, "opportunities", params, service_key)
    print(f"[OK] {len(candidates)} row(s) due for a review check"
          f"{' (staleness filter ignored)' if args.force else f' (unchecked or >{STALE_AFTER_DAYS} days stale)'}.")

    mode = "all"
    items = candidates
    if args.sample:
        mode = "sample"
        items = random.sample(candidates, min(args.sample, len(candidates)))

    if not items:
        print("[OK] Nothing due for a review check right now.")
        return

    run_id = None
    if not args.dry_run:
        run_row = supabase_insert_one(supabase_url, "agent_runs", {
            "agent": "review_checker",
            "mode": mode + ("-force" if args.force else ""),
            "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }, service_key)
        run_id = run_row["id"] if run_row else None

    total_cost = 0.0
    updated = 0
    errors = 0
    total_searches = 0
    silent_search_count = 0
    dry_run_results = []

    for i, opp in enumerate(items):
        print(f"[{i + 1}/{len(items)}] {opp['name'][:60]}...", end=" ")
        try:
            info, cost, searches = check_one(opp, gemini_key)
            total_cost += cost
            total_searches += searches
            # Silent skip-search (see gemini_common.py's docstring): particularly misleading
            # here, since "insufficient_data" from a 0-search call looks identical to
            # "insufficient_data" from a thorough real search that found nothing — the
            # verdict text can't distinguish "we looked and found nothing" from "we didn't
            # actually look."
            if searches == 0:
                silent_search_count += 1
            status = info.get("review_status") if info.get("review_status") in VALID_STATUS else "insufficient_data"
            sources = info.get("review_sources") or []
            if not isinstance(sources, list):
                sources = []
            sources = [s for s in sources if isinstance(s, dict) and s.get("url")][:3]
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
                    "web_searches": searches,
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
            silent = " [SILENT: no search invoked]" if searches == 0 else ""
            print(f"{status}, {searches} search(es){silent}, ${cost:.4f}" + (" [changed]" if changed else ""))
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
          f"silent (no-search) checks: {silent_search_count}/{len(items)}, cost: ${total_cost:.4f}")
    if mode == "sample" and items:
        per_item = total_cost / len(items)
        projected = per_item * len(candidates)
        print(f"[PROJECTED] ~${per_item:.4f}/item -> all {len(candidates)} due rows "
              f"~${projected:.2f} for a full pass.")

    if args.dry_run:
        run_date = datetime.date.today().strftime("%Y%m%d")
        review_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    f"review_check_dry_run_{run_date}.json")
        with open(review_path, "w", encoding="utf-8") as f:
            json.dump(dry_run_results, f, indent=2, ensure_ascii=False)
        print(f"[OK] Wrote dry-run review snapshot: {review_path}")
        print("[DRY RUN] No writes performed (opportunities or agent_runs).")
        return

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

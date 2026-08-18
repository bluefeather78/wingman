#!/usr/bin/env python3
"""Deadline/status checker: for each active opportunity in the Supabase `opportunities`
catalog, checks (via web_search) whether it's currently running, closed for the cycle, or
permanently discontinued, finds every deadline milestone, and estimates the next
registration-opens date from prior-cycle timing when it isn't published — never inventing
a date with no basis.

The system prompt below is adapted directly from script.js's extractTrackerInfo() (used
by the Tracker's "check for updates" feature) — same search discipline (URL + org base
site, discontinued-program detection, multi-deadline handling, opens-date estimation,
"never invent a date" rule) — trimmed to just the catalog-relevant fields (drops the
Tracker-only action_items/requirements/apply_url fields).

SETUP:
    .env needs SUPABASE_URL, SUPABASE_SERVICE_KEY, GEMINI_API_KEY.
    Run this SQL once in the Supabase SQL editor before first use:

        alter table opportunities
          add column status text,
          add column deadlines jsonb,
          add column opens_date text,
          add column was_estimated boolean default false,
          add column deadline_note text,
          add column last_checked_at timestamptz;

        create table agent_runs (
            id                bigint generated always as identity primary key,
            agent             text not null,
            mode              text,
            started_at        timestamptz not null,
            finished_at       timestamptz,
            items_processed   integer default 0,
            items_added       integer default 0,
            items_updated     integer default 0,
            items_deleted     integer default 0,
            emails_subscribed integer default 0,
            errors            integer default 0,
            cost_usd          numeric,
            notes             text
        );

USAGE:
    python check_deadlines.py --sample 20   # random 20-row sample, prints measured cost
    python check_deadlines.py --all         # full active catalog
"""
import argparse
import datetime
import os
import random
import sys
import urllib.error

from gemini_common import call_gemini, extract_json, estimate_cost
from supabase_common import load_dotenv, supabase_get, supabase_insert_one, supabase_patch

VALID_STATUS = {"running", "not_running", "unknown"}


def today_label():
    return datetime.date.today().strftime("%B %-d, %Y") if os.name != "nt" else datetime.date.today().strftime("%B %#d, %Y")


def base_domain(url):
    try:
        from urllib.parse import urlparse
        netloc = urlparse(url).netloc
        return netloc or url
    except Exception:
        return url


def build_system(opp):
    root = base_domain(opp["url"])
    today = today_label()
    return f"""You extract current status/deadline data for an extracurricular opportunity (program, \
internship, competition, or research position), for a catalog used by high school students. Today's \
date is {today}.

Search thoroughly with web_search:
- Start with the given URL.
- If that page's deadline/cycle information looks stale (from a past year, or missing entirely), also \
search the organization's base website (e.g. {root}) for a more current version of this program's page.
- Look explicitly for language indicating the program is discontinued, paused, cancelled, or not \
accepting applications this cycle (e.g. "program has ended," "not running this year," "no longer \
offered"). If you find this, set status to "not_running" — do not guess a future deadline for a program \
you've determined isn't running.

Multiple deadline milestones — this matters a lot:
- Many programs have MORE THAN ONE deadline (e.g. an early-bird deadline before a later regular/final \
deadline). Find and list EVERY distinct deadline milestone you can, each with a short specific label \
(e.g. "Early Bird Registration", "Regular Registration", "Final Deadline") and its own date, in \
chronological order. If there's genuinely only one, list just that one.

Registration/application OPENS date — pay particular, deliberate attention to this:
- Actively search for the date applications/registration OPEN, not just when they close.
- If you can't find an explicit opens date but the program is recurring, ESTIMATE it from the prior \
cycle's opens date (e.g. applications opened January 10 last cycle, program is annual -> estimate a \
similar date this cycle) and set was_estimated true if any part of what you're returning is estimated.
- Only leave opens_date null if you genuinely found no opens date and have no reasonable prior-cycle \
basis to estimate one.

Date reasoning:
- If every deadline you found has already passed relative to today, and the program appears to run on \
a regular annual/recurring cycle, ESTIMATE next cycle's dates from the prior cycle's timing. Set \
was_estimated to true and say what it's based on in deadline_note (e.g. "Estimated from the 2025 cycle; \
2027 dates not yet posted").
- Only mark status "running" if you found real evidence the program is currently active or has a \
future confirmed/estimated date. Use "unknown" if you found genuinely nothing usable after searching \
both the URL and the base site.
- Never invent a specific date with no basis — every date must come from something you actually found, \
whether confirmed or reasonably estimated from a real prior cycle.

Respond with ONLY a raw JSON object, no markdown fences, no preamble, no text after the JSON, matching \
exactly this schema: {{"status": "running, not_running, or unknown", "deadlines": [{{"label": "short \
specific label", "date_iso": "YYYY-MM-DD"}}], "opens_date": "YYYY-MM-DD or null", "was_estimated": true \
or false, "deadline_note": "one short sentence: status/estimate basis/caveat, or null"}}. Stay well \
within a 600-token response: at most 3 deadlines entries. Never truncate mid-value or leave the JSON \
unclosed — drop the least important deadline entry first if you need to shorten, but keep at least the \
earliest one if any exist."""


def check_one(opp, api_key):
    system = build_system(opp)
    user_content = (f"Opportunity: {opp['name']} ({opp.get('org') or 'unknown org'})\n"
                     f"URL: {opp['url']}\nKnown info: {opp.get('summary') or ''}\n\n"
                     f"Fetch this URL (and the base site if needed), and extract current status/deadline "
                     f"details per the schema. Look carefully for multiple deadline milestones.")
    # max_tokens raised from 800 -> 1200 and thinking_level defaults to "low" in
    # call_gemini() as of 2026-08-18: same silent-truncation root cause found and fixed in
    # check_reviews.py (thinking tokens were starving the visible JSON output at low
    # budgets). This script had never been run live, so the bug was unconfirmed here, but
    # the architecture is identical — see gemini_common.py's "FOURTH finding" docstring.
    text, usage = call_gemini(system, user_content, api_key, use_web_search=True, max_tokens=1200)
    info = extract_json(text)
    searches = (usage.get("server_tool_use") or {}).get("web_search_requests", 0)
    return info, estimate_cost(usage), searches


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--sample", type=int, help="Check a random N-row sample instead of the full catalog.")
    group.add_argument("--all", action="store_true", help="Check every active row (default if no flag given).")
    args = parser.parse_args()

    load_dotenv()
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    service_key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    if not supabase_url or not service_key or not gemini_key:
        print("[ERROR] SUPABASE_URL / SUPABASE_SERVICE_KEY / GEMINI_API_KEY not set in .env.")
        sys.exit(1)

    print("[OK] Fetching active catalog from Supabase...")
    all_active = supabase_get(supabase_url, "opportunities", {
        "select": "id,name,org,url,summary,status,deadlines",
        "is_active": "eq.true",
    }, service_key)
    print(f"[OK] {len(all_active)} active rows.")

    mode = "all"
    items = all_active
    if args.sample:
        mode = "sample"
        items = random.sample(all_active, min(args.sample, len(all_active)))

    run_row = supabase_insert_one(supabase_url, "agent_runs", {
        "agent": "deadline_checker",
        "mode": mode,
        "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }, service_key)
    run_id = run_row["id"] if run_row else None

    total_cost = 0.0
    updated = 0
    errors = 0
    total_searches = 0
    silent_search_count = 0

    for i, opp in enumerate(items):
        print(f"[{i + 1}/{len(items)}] {opp['name'][:60]}...", end=" ")
        try:
            info, cost, searches = check_one(opp, gemini_key)
            total_cost += cost
            total_searches += searches
            # Silent skip-search: use_web_search=True but Gemini answered from training data
            # instead of invoking googleSearch (see gemini_common.py's docstring). For this
            # script specifically, a 0-search result means status/deadlines were NOT verified
            # live this run, even though last_checked_at still gets stamped with "now" below.
            if searches == 0:
                silent_search_count += 1
            status = info.get("status") if info.get("status") in VALID_STATUS else "unknown"
            deadlines = info.get("deadlines") or []
            if not isinstance(deadlines, list):
                deadlines = []
            deadlines = [d for d in deadlines if isinstance(d, dict) and d.get("date_iso")]
            changed = status != opp.get("status") or deadlines != (opp.get("deadlines") or [])
            if changed:
                updated += 1
            supabase_patch(supabase_url, "opportunities", {"id": f"eq.{opp['id']}"}, {
                "status": status,
                "deadlines": deadlines,
                "opens_date": info.get("opens_date"),
                "was_estimated": bool(info.get("was_estimated")),
                "deadline_note": info.get("deadline_note"),
                "last_checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }, service_key)
            silent = " [SILENT: no search invoked]" if searches == 0 else ""
            print(f"{status}, {searches} search(es){silent}, ${cost:.4f}" + (" [changed]" if changed else ""))
        except urllib.error.HTTPError as e:
            errors += 1
            print(f"[ERROR] HTTP {e.code}")
        except Exception as e:
            errors += 1
            print(f"[ERROR] {e}")

    print(f"\n[SUMMARY] checked: {len(items)}, updated: {updated}, errors: {errors}, "
          f"silent (no-search) checks: {silent_search_count}/{len(items)}, cost: ${total_cost:.4f}")
    if mode == "sample" and items:
        per_item = total_cost / len(items)
        projected = per_item * len(all_active)
        print(f"[PROJECTED] ~${per_item:.4f}/item -> full catalog ({len(all_active)} active rows) "
              f"~${projected:.2f} for a full pass.")

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

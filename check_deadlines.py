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

NOTE (2026-08-18): this script's --all/batch mode is no longer the primary way deadline data
stays current — a full-catalog pass previously tripped Gemini's googleSearch grounding quota
partway through (see the plan doc's "on-demand deadline checking" update for the full
incident writeup). The primary mechanism is now server.py's on-demand, cross-user-cached
GET /api/opportunities/<id>/deadline endpoint, which reuses this module's check_one() and
VALID_STATUS directly and triggers a check only when a real user adds/loads a tracked
opportunity whose cached data (7-day TTL) has gone stale. This script remains useful as a
manual bulk-backfill/cleanup tool — e.g. after a large scraper pass adds many new rows at
once, or a deliberate full-catalog refresh — just run it with awareness that a full --all
pass on a large catalog can still exhaust the same shared quota the on-demand endpoint uses.

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
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request

from gemini_common import extract_json, estimate_cost
from supabase_common import load_dotenv, supabase_get, supabase_insert_one, supabase_patch

VALID_STATUS = {"running", "not_running", "unknown"}

# ---------- Claude Haiku API call (for deadline checking) ----------
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL = "claude-haiku-4-5-20251001"
CLAUDE_MAX_TOKENS = 1200


def call_claude(system, user_content, api_key, use_web_search=False):
    """Call Claude Haiku with web search AND web fetch enforced for deadline extraction.
    web_search finds candidate pages (e.g. an org's FAQ/key-dates subpage); web_fetch then
    retrieves the FULL text of a specific known URL (the given opportunity URL, or a URL
    surfaced by a prior search/fetch) — search alone only returns short result snippets,
    which is why deadline info buried on a subpage (not the top-level URL) was previously
    getting missed even though the prompt told Claude to "fetch" the page. Both tools are
    supported on Haiku and web_fetch carries no extra per-call charge (token cost only), so
    this doesn't change per-check pricing model.
    Returns (text, usage) tuple matching the shape of call_gemini() for compatibility."""
    body = {
        "model": CLAUDE_MODEL,
        "max_tokens": CLAUDE_MAX_TOKENS,
        "system": system,
        "messages": [{"role": "user", "content": user_content}],
    }
    if use_web_search:
        body["tools"] = [
            {"type": "web_search_20250305", "name": "web_search", "max_uses": 3},
            # max_content_tokens bounds how much of any one fetched page counts against
            # CLAUDE_MAX_TOKENS's shared input budget; a handful of subpage fetches per
            # check is normal (main URL + org FAQ/dates page), max_uses caps runaway cases.
            {"type": "web_fetch_20250910", "name": "web_fetch", "max_uses": 5, "max_content_tokens": 4000},
        ]

    req = urllib.request.Request(
        ANTHROPIC_URL,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )

    with urllib.request.urlopen(req) as resp:
        response_data = json.loads(resp.read())

    # Extract text content from response
    text = "\n".join(b.get("text", "") for b in response_data.get("content", []) if b.get("type") == "text")

    # Build usage dict in same format as gemini_common for compatibility. NOTE: search/fetch
    # calls come back as content blocks of type "server_tool_use" (not "tool_use" — a plain
    # tool_use block is only ever a *client*-defined tool call, which this script has none
    # of), so the previous code here — filtering for type=="tool_use" — always counted zero,
    # meaning every check was mislabeled a "silent search" downstream regardless of whether a
    # real search actually ran. The API's own usage.server_tool_use block is authoritative
    # and already has the right counts (confirmed against a live response) — use that instead
    # of re-deriving it from content blocks.
    usage = {
        "input_tokens": response_data.get("usage", {}).get("input_tokens", 0),
        "output_tokens": response_data.get("usage", {}).get("output_tokens", 0),
        "server_tool_use": response_data.get("usage", {}).get("server_tool_use") or {},
    }

    return text, usage


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

YOU MUST use the web_search and web_fetch tools to gather current information before answering. Do \
not rely on training data alone — always search AND fetch for live, current deadline/status \
information. These are two different tools with two different jobs:
- web_search finds candidate pages (it only returns short snippets, not full page text).
- web_fetch retrieves the FULL text of one specific URL you already know — the given opportunity URL, \
or any URL a web_search result surfaced. Deadline/cycle details are very often on a subpage rather \
than the main program page (an FAQ, "How to Apply," "Key Dates," "Timeline," or "Deadlines" page), and \
that detail usually will NOT appear in a search snippet — you have to fetch the actual subpage to see it.

Work thoroughly:
- fetch the given URL directly first.
- If that page's deadline/cycle information looks stale (from a past year) or missing, search the \
organization's base website (e.g. {root}) for a more current or more specific page — explicitly try \
queries like "site:{root} FAQ", "site:{root} how to apply", "site:{root} key dates" / "deadlines" / \
"timeline" — then fetch the most promising result(s). Don't stop at the landing page's absence of a \
date; a program's real deadline is frequently published only on one of these subpages.
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

**Estimation Logic** — when explicit deadline info for this year is NOT clearly available:
- Check for historical patterns from past years (if visible on the site or in search results).
- Note when similar programs in the same category typically open/close (e.g., summer programs often \
close Jan-Mar, internships close rolling, academic competitions typically deadline in fall).
- Look for clues in the site content (e.g., "Applications open in fall" or "Rolling admissions until \
April" or "Next cycle opens in January").
- If you find a clear pattern or reasonable basis, estimate and set was_estimated=true, explaining \
the basis in deadline_note.
- If no clear pattern emerges and no explicit deadlines are found, set status to "unknown" and explain \
why in deadline_note.
- DO NOT guess without basis — every date must come from real evidence you found.

Date reasoning:
- If every deadline you found has already passed relative to today, and the program appears to run on \
a regular annual/recurring cycle, ESTIMATE next cycle's dates from the prior cycle's timing. Set \
was_estimated to true and say what it's based on in deadline_note (e.g. "Estimated from the 2025 cycle; \
2027 dates not yet posted").
- Only mark status "running" if you found real evidence the program is currently active or has a \
future confirmed/estimated date. Use "unknown" if you found genuinely nothing usable after searching \
and fetching both the given URL and likely subpages of the base site.
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
                     f"Fetch this URL directly, then search and fetch subpages of the org's site (FAQ, "
                     f"how to apply, key dates) if needed, and extract current status/deadline details "
                     f"per the schema. Look carefully for multiple deadline milestones.")
    # Using Claude Haiku (claude-haiku-4-5-20251001) with web search + web fetch enforced in prompt.
    # max_tokens set to 1200 to allow web search/fetch results without token starvation.
    text, usage = call_claude(system, user_content, api_key, use_web_search=True)
    info = extract_json(text)
    searches = (usage.get("server_tool_use") or {}).get("web_search_requests", 0)

    # Note: searches may be 0 if Claude answers from training data (silent skip).
    # This is tracked via the "source" flag in server.py ("fresh, real search" vs "fresh, silent search")
    # so users can see whether the result was live-verified or not.
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
    # NOTE: this script calls Claude (call_claude/check_one), not Gemini — it must read
    # ANTHROPIC_API_KEY. It previously read GEMINI_API_KEY here (a leftover from before the
    # Gemini->Claude migration for deadline checking) and passed that value as check_one()'s
    # api_key, which would have sent a Gemini key to the Anthropic API and failed auth on
    # every row. This mode is currently unused (see module docstring — on-demand checking via
    # server.py is primary) so it likely went unnoticed, but it's fixed here for correctness.
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not supabase_url or not service_key or not anthropic_key:
        print("[ERROR] SUPABASE_URL / SUPABASE_SERVICE_KEY / ANTHROPIC_API_KEY not set in .env.")
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
            info, cost, searches = check_one(opp, anthropic_key)
            total_cost += cost
            total_searches += searches
            # Silent skip-search: use_web_search=True but Claude answered from training data
            # instead of invoking web_search. A 0-search result means status/deadlines were
            # NOT verified live this run, even though last_checked_at still gets stamped with
            # "now" below.
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
        # Rate limiting is now enforced at the API level in gemini_common.call_gemini()
        # (minimum 5 seconds between calls per Gemini's documented rate limit policy),
        # so explicit throttle here is no longer needed.

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

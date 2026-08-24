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

TWO PHASES (2026-08-23), and the split is the accuracy design. check_one() makes a PROSE
call with tools on, then a second, tool-free call that turns those notes into the schema.
The reason is measured on the Gemini side and this agent showed the same shape: demanding a
JSON answer collapses the search rate (A/B: prose 4/4 searched, JSON 0/4; this agent's own
history is 59 searches across 1218 row-checks on the old single JSON call). What it
fabricates when it does not look are DATES, which the app renders as authoritative, so this
is where a silent call does the most visible damage. See gemini_common.py's SEVENTH finding.

A still-silent call (after one retry) now WRITES NOTHING — in the batch loop and in
server.py's interactive endpoint alike. That is not merely cautious: check_one() returns an
empty info in that case, so writing it would blank the row's real status and
important_dates AND stamp last_checked_at, destroying good data and then hiding the damage
behind the 7-day TTL. The interactive path falls back to the cached value and deliberately
does not stamp, so the next request re-rolls the search decision.

MEASURED COST: $0.0676 for a row that searched once, against a historical median of $0.0790
for the interactive checks that really did search (36 of them in deadline_check_log). The
two-phase version is CHEAPER per verified check, because MAX_SEARCHES caps web_search at 1
where it used to allow 3. The old sub-cent figures in agent_runs (id=14, id=16 at
~$0.0010/row) are the price of not looking, not a cheaper way of looking. A full --all pass
now projects to roughly $84 — read the note below before running one.

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
          add column important_dates jsonb,
          add column was_estimated boolean default false,
          add column important_date_note text,
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

# Note: no apply_timing here — this script uses its own module-level throttle (set_min_delay
# above), not gemini_common's, because check_one() is shared with server.py's interactive path.
from agent_common import add_agent_args, emit_preview, snapshot_stamp
from gemini_common import extract_json
# Costed with claude_common's rates, not gemini_common's: check_one() calls THIS module's
# local call_claude() against Anthropic, and pricing a Claude call at Gemini's per-token
# and per-search rates ($0.75/$3.75 + $0.014/search) is simply the wrong bill. Every
# deadline_check_log row written before 2026-08-22 carries that mispricing.
from claude_common import estimate_cost
from supabase_common import load_dotenv, supabase_get, supabase_insert_one, supabase_patch

VALID_STATUS = {"running", "not_running", "unknown"}

# ---------- Claude Haiku API call (for deadline checking) ----------
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL = "claude-haiku-4-5-20251001"
CLAUDE_MAX_TOKENS = 1200

# Rate limiting and timeout for this module's own call_claude().
#
# Both default to permissive values ON PURPOSE, because check_one() below is shared with
# server.py's INTERACTIVE per-opportunity deadline check (GET /api/opportunities/<id>/
# deadline). A process-wide inter-call delay there would make one user's request block on
# another's. main() raises the delay to --min-delay (default 5) for batch runs only.
#
# This module previously had NO throttle and NO timeout, while carrying a comment in main()
# claiming rate limiting was "enforced at the API level in gemini_common.call_gemini()" —
# untrue, since nothing here goes through gemini_common. Unthrottled batch runs are the
# likely cause of the grounding-quota exhaustion recorded in this file's docstring, and an
# untimed urlopen() could hang a batch indefinitely on one bad row.
BATCH_MIN_DELAY_SECS = 5

# Web searches phase 1 may make. Anthropic ENFORCES this server-side (max_uses), so
# unlike Gemini it is a hard ceiling, and it is the only per-call fee in this agent
# ($0.01/search). Set to 1 deliberately. web_fetch is NOT capped alongside it: it is
# free and it is the tool that actually reaches the FAQ/key-dates subpages the dates
# live on.
MAX_SEARCHES = 1
_min_delay_secs = 0.0
_default_timeout_secs = 120
_last_call_time = 0.0


def set_min_delay(secs):
    """Set the minimum seconds between Anthropic calls from this module (--min-delay)."""
    global _min_delay_secs
    if secs is not None:
        _min_delay_secs = max(0.0, float(secs))
    return _min_delay_secs


def set_default_timeout(secs):
    """Set the HTTP read timeout for calls from this module (--timeout). Note a
    client-side timeout does not stop or refund server-side work already in flight."""
    global _default_timeout_secs
    if secs is not None:
        _default_timeout_secs = max(1.0, float(secs))
    return _default_timeout_secs


def _enforce_rate_limit():
    global _last_call_time
    elapsed = time.time() - _last_call_time
    if elapsed < _min_delay_secs:
        time.sleep(_min_delay_secs - elapsed)
    _last_call_time = time.time()


def extract_source_urls(response_data):
    """Every URL Claude's server tools ACTUALLY retrieved, in order, deduped.

    Claude's answer for grounding metadata. `web_search_tool_result` blocks carry the real
    result URLs and `web_fetch_tool_result` blocks the fetched one — and unlike Gemini's
    `groundingChunks`, these are the destination URLs already, with no redirect hop to
    resolve. They exist for the same reason: a URL the model TYPES is unreliable, and this
    is the only place the retrieved one lives. Phase 2 is handed this list so it grounds its
    answer on pages that were really read.
    """
    urls = []
    for block in response_data.get("content") or []:
        btype = block.get("type")
        if btype == "web_search_tool_result":
            for item in block.get("content") or []:
                if isinstance(item, dict) and item.get("url"):
                    urls.append(item["url"])
        elif btype == "web_fetch_tool_result":
            inner = block.get("content")
            if isinstance(inner, dict) and inner.get("url"):
                urls.append(inner["url"])
    return list(dict.fromkeys(urls))


def call_claude(system, user_content, api_key, use_web_search=False, max_searches=None,
                return_sources=False):
    """Call Claude Haiku with web search AND web fetch enforced for deadline extraction.
    web_search finds candidate pages (e.g. an org's FAQ/key-dates subpage); web_fetch then
    retrieves the FULL text of a specific known URL (the given opportunity URL, or a URL
    surfaced by a prior search/fetch) — search alone only returns short result snippets,
    which is why deadline info buried on a subpage (not the top-level URL) was previously
    getting missed even though the prompt told Claude to "fetch" the page. Both tools are
    supported on Haiku and web_fetch carries no extra per-call charge (token cost only), so
    this doesn't change per-check pricing model.
    Returns (text, usage) tuple matching the shape of call_gemini() for compatibility."""
    _enforce_rate_limit()
    body = {
        "model": CLAUDE_MODEL,
        "max_tokens": CLAUDE_MAX_TOKENS,
        "system": system,
        "messages": [{"role": "user", "content": user_content}],
    }
    if use_web_search:
        body["tools"] = [
            # Anthropic ENFORCES max_uses server-side, unlike Gemini's max_searches, which
            # is only a number folded into the prompt. So this is a real cost ceiling: it is
            # the only per-call fee here ($0.01/search), and it is what MAX_SEARCHES tunes.
            {"type": "web_search_20250305", "name": "web_search",
             "max_uses": max_searches if max_searches is not None else 3},
            # web_fetch stays generous and is NOT reduced alongside it: it carries no
            # per-call charge (token cost only), and it is the tool that actually gets the
            # dates. Deadlines live on FAQ/key-dates subpages that a search snippet never
            # shows, so the estimation logic in the prompt depends on fetching them.
            # max_content_tokens bounds how much of any one page counts against
            # CLAUDE_MAX_TOKENS's shared input budget.
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

    with urllib.request.urlopen(req, timeout=_default_timeout_secs) as resp:
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

    # Third element only on request, so the existing 2-tuple call sites are untouched —
    # same convention as gemini_common's return_grounding.
    if return_sources:
        return text, usage, extract_source_urls(response_data)
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
    this_year = datetime.date.today().year
    next_year = this_year + 1
    return f"""You extract current status/deadline data for an extracurricular opportunity (program, \
internship, competition, or research position), for a catalog used by high school students. Today's \
date is {today}.

YOU MUST use web_search and web_fetch to gather current information before answering — do not rely on \
training data alone. web_search finds candidate pages (snippets only). web_fetch retrieves the FULL \
text of one specific URL you already know. Deadline/cycle details are very often on a subpage (FAQ, \
"How to Apply," "Key Dates," "Timeline," "Deadlines") rather than the main landing page, and usually \
will NOT appear in a search snippet — you must fetch the subpage to see it.

GOAL: capture as many pertinent dates as you can find or reasonably estimate. Estimating from a prior \
cycle is expected and encouraged, not a fallback of last resort — a well-justified estimate is always \
better than an empty field.

SEARCH STEPS (do all of these, in order):
1. Fetch the given URL directly.
2. Search "site:{root} {next_year}" and "site:{root} {this_year}" for a current/upcoming-cycle page — \
orgs often publish a separate year-specific page distinct from the evergreen landing page.
3. ALWAYS ALSO search for the most recent PAST cycle (e.g. "site:{root} {this_year} deadline" and, using \
the year before {this_year} — compute it yourself from {today} — "site:{root} <that year>"), even if \
step 2 succeeded. This is your estimation basis and is mandatory, not optional: you need it either to \
confirm the pattern behind a found date or to construct an estimate when nothing current is posted.
4. Search "site:{root} FAQ", "how to apply", "key dates", "deadlines", "timeline" and fetch the best hits.
5. Look explicitly for closure language: "cycle closed," "not running this year," "applications no longer \
accepted," etc. DISTINGUISH between: (a) current cycle is closed but program recurs (e.g., "2026 closed, 2027 \
opening Fall") → status="running" (the program itself is ongoing), still extract dates for the next cycle; \
(b) program is permanently discontinued (e.g., "no longer offered," "program ended") → status="not_running", \
do not estimate future dates. Evidence of recurrence ("Next cycle in Fall", "2027 details TBA", "Check back \
for 2027") → treat as "running" with forward-dated important_dates.

ESTIMATION LOGIC (single source of truth — apply in this order):
a. Found explicit current/upcoming-cycle dates → use them, was_estimated=false for those entries.
b. No current-cycle dates found, but you found last cycle's real dates AND the program looks recurring \
(no evidence it's discontinued) → roll each date forward by ~1 year (or to the next plausible \
occurrence), was_estimated=true, status="running". This is the expected path when a new cycle's page \
isn't live yet — use it; don't default to "unknown."
c. Found only a vague pattern (e.g. "opens in fall," "rolling through spring") → construct a concrete \
estimated date from it (pick a reasonable specific day within the stated window), was_estimated=true, \
explain the basis briefly in important_date_note.
d. Current cycle is explicitly closed (e.g., "2026 applications closed") BUT organization states or implies \
the program will recur (e.g., "2027 opens Fall 2026") → status="running", extract/estimate dates for the \
future cycle from explicit month/season language, was_estimated=true. This is the expected path when a new \
cycle isn't yet open — capture the forward-looking dates.
e. Found genuinely nothing current AND nothing from any prior cycle after completing all search steps \
above → status="unknown". This should be rare — only after step 3 has actually been tried and failed.

IMPORTANT DATES — capture every distinct pertinent date, not just one deadline:
- Include, whenever they exist or can be estimated: registration/application opens, early-bird deadline, \
regular/final deadline, notification/decision date, and event start/end dates (for a conference/symposium). \
Programs frequently have multiple deadlines — list each distinctly with its own label and type ("opens", \
"deadline", "event_start", "event_end", "other").
- Actively search for the OPENS date specifically, not just the close — this is the field most often \
missed. Estimate it from the prior cycle if not explicitly posted (was_estimated=true).
- Every date you reason about must appear as a structured entry in "important_dates" — never leave a date \
mentioned only in prose in important_date_note. If you have enough basis to write a date into the note, \
you have enough basis to add the matching structured entry.
- Only omit a date category if you found no information for it AND no prior-cycle basis to estimate it.

SELF-CHECK before you finish:
- Every date you report must be on or after {today}. If one is in the past, roll it forward to \
its next real occurrence and say it is estimated. Drop it ONLY if the program is discontinued or \
the event was genuinely a one-off — never report a past date, and never let a past date be the \
reason you report no date at all.
- If every date you found belongs to a cycle that has already ended, and nothing says the program \
is discontinued, you MUST write the rolled-forward estimates out explicitly, date by date \
("Application deadline: estimated 2027-04-19, from 2026-04-20 rolled forward one year"). \
"The next cycle is not posted yet" is a true statement and an unacceptable final answer on its \
own: the student needs to know roughly WHEN to come back, and last cycle's timing is the best \
evidence anyone has for that. This holds even if you have run out of search budget — the \
roll-forward needs no further searching, only the dates you already have.
- Prefer reporting a reasonably-estimated date over omitting it. Only leave a category out if \
step (e) above genuinely applies.

Write this up in plain prose. State the status and why, list every date you found or estimated \
with its label and whether it is confirmed or estimated, and name the pages you actually \
fetched. No JSON, no schema, no markdown fences — just write it. Stay well within a \
1000-token response."""


# PHASE 2. Notes plus the real fetched URLs in, strict JSON out, no tools. The schema and the
# agreement rules live here rather than in phase 1 because a strict output format is free on a
# call that does not need to search — and ruinous on one that does.
EXTRACT_SYSTEM = """You turn a researcher's written notes about one extracurricular \
opportunity's application cycle into a strict JSON record. You are NOT researching — \
everything you need is in the notes, and you must not add a date, a status or a caveat that \
is not in them.

Today's date is {today}.

RULES, in order:
- Every date the notes reason about must become a structured entry in "important_dates" — \
never leave a date mentioned only in prose in "important_date_note". If the notes give enough \
basis to write a date into the note, that date gets its own structured entry.
- Every specific date in "important_date_note" must have a matching entry in \
"important_dates", and vice versa — the two must agree.
- Every date must be on or after {today}, and a past date is never reported as-is. If the \
notes give the dates of a cycle that has already ended and do NOT say the program is \
discontinued, project each one onto its next annual occurrence — same month and day, plus the \
smallest whole number of years that lands on or after {today} — set "was_estimated": true, and \
say in the note that they are estimated from the last cycle. That is arithmetic on what the \
notes already establish, not research, and it is required: an empty "important_dates" for a \
program that visibly ran last year tells the student nothing about when to come back. Drop a \
past date only when the notes say the program is discontinued, or describe a genuine one-off \
event with no sign of recurrence.
- "was_estimated" is true if ANY reported date came from a prior cycle or a vague pattern \
rather than an explicitly posted current-cycle date.
- If the notes say the program is permanently discontinued, status is "not_running" and you \
report no future dates. If they say the current cycle is closed but the program recurs, \
status is "running" with forward-dated entries — the ones the notes give if they give them, \
otherwise the projection described above.
- If the notes found nothing at all — no current dates AND no past-cycle dates to project \
from — status is "unknown" with an empty important_dates. That is a valid outcome; never invent \
one to fill the schema. It does NOT apply when the notes carry a past cycle's real dates: those \
get projected, per the rule above.

Respond with ONLY a raw JSON object, no markdown fences, no preamble, no text after the JSON, \
matching exactly this schema: {{"status": "running, not_running, or unknown", \
"important_dates": [{{"label": "short specific label", "date_iso": "YYYY-MM-DD", "type": \
"opens, deadline, event_start, event_end, or other"}}], "was_estimated": true or false, \
"important_date_note": "one short sentence: status/estimate basis/caveat, or null"}}. Keep the \
response well within a 1000-token budget: up to 8 important_dates entries, ordered \
chronologically. If you must shorten, drop the least specific/least useful entry first (e.g. a \
duplicate "other" note) — never drop opens/deadline/event dates before that."""


def build_extract_system():
    return EXTRACT_SYSTEM.format(today=today_label())


def research_deadlines(opp, api_key, retry_on_silent=True):
    """PHASE 1 — prose out, tools on. (notes, cost, searches, sources, attempts).

    Retries once on a zero-search answer. Re-rolling, not re-prompting: the search decision
    is non-deterministic and cannot be forced. Cost is banked per attempt so an exception on
    the retry cannot discard what the first call already spent.
    """
    system = build_system(opp)
    user_content = (f"Opportunity: {opp['name']} ({opp.get('org') or 'unknown org'})\n"
                    f"URL: {opp['url']}\nKnown info: {opp.get('summary') or ''}\n\n"
                    f"Fetch this URL directly, then search and fetch subpages of the org's "
                    f"site (FAQ, how to apply, key dates) if needed, and report the current "
                    f"status and every relevant date — registration open/close, event dates, "
                    f"notifications — not just a single deadline.")
    cost = 0.0
    notes, usage, sources = "", {}, []
    attempts = 2 if retry_on_silent else 1
    for attempt in range(1, attempts + 1):
        notes, usage, sources = call_claude(system, user_content, api_key,
                                            use_web_search=True, max_searches=MAX_SEARCHES,
                                            return_sources=True)
        cost += estimate_cost(usage)
        searches = (usage.get("server_tool_use") or {}).get("web_search_requests", 0)
        if searches or attempt == attempts:
            return notes, cost, searches, sources, attempt
        print("  [SILENT] no search invoked — retrying once", flush=True)
    return notes, cost, 0, sources, attempts


def extract_deadlines(opp, notes, sources, api_key):
    """PHASE 2 — notes plus the pages actually fetched in, strict JSON out. No tools."""
    source_block = "\n".join(f"- {u}" for u in sources) or "(none retrieved)"
    user_content = (f"Opportunity: {opp['name']} ({opp.get('org') or 'unknown org'})\n"
                    f"PAGES ACTUALLY FETCHED:\n{source_block}\n\n"
                    f"RESEARCHER'S NOTES:\n{notes}\n\nReturn the JSON object now.")
    text, usage = call_claude(build_extract_system(), user_content, api_key,
                              use_web_search=False)
    return extract_json(text) or {}, estimate_cost(usage)


def check_one(opp, api_key, retry_on_silent=True):
    """Both phases. (info, cost, searches, attempts).

    TWO CALLS, and the split is the accuracy design — see check_reviews.py and
    gemini_common.py's SEVENTH finding. Demanding JSON collapses the search rate; measured
    on Gemini at 4/4 vs 0/4, and this agent's own history shows the same shape (59 searches
    across 1218 row-checks on the old single-call JSON prompt). Phase 1 asks for prose so
    the tools actually get used; phase 2 turns those notes into the schema with no tools at
    all, which is where a strict format costs nothing.

    What it fabricates when it does not look are DATES, which the app renders as
    authoritative — so this is the agent where a silent call does the most visible damage.

    `retry_on_silent` defaults to True for the interactive path too. That costs a user one
    extra round-trip on top of the second phase, and it is the right trade because
    server.py caches the answer for 7 days: one silent result is served to every student who
    opens that opportunity for a week.

    Phase 2 is SKIPPED when phase 1 stayed silent — notes written without looking are not
    worth converting, and the caller can see `searches == 0` and label the result.
    """
    notes, cost, searches, sources, attempts = research_deadlines(opp, api_key, retry_on_silent)
    if not searches:
        return {}, cost, 0, attempts
    info, extract_cost = extract_deadlines(opp, notes, sources, api_key)
    return info, cost + extract_cost, searches, attempts


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--sample", type=int, help="Check a random N-row sample instead of the full catalog.")
    group.add_argument("--all", action="store_true", help="Check every active row (default if no flag given).")
    parser.add_argument("--dry-run", action="store_true",
                        help="No writes (opportunities or agent_runs) — still calls the API at "
                             "full cost, but dumps results to a local JSON file instead.")
    add_agent_args(parser, default_timeout=120, default_min_delay=BATCH_MIN_DELAY_SECS)
    args = parser.parse_args()
    # Batch runs get a real throttle; the module default stays 0 for server.py's
    # interactive on-demand check, which shares check_one() with this script.
    set_min_delay(args.min_delay)
    set_default_timeout(args.timeout)

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
        "select": "id,name,org,url,summary,status,important_dates",
        "is_active": "eq.true",
    }, service_key)
    print(f"[OK] {len(all_active)} active rows.")

    mode = "all"
    items = all_active
    if args.sample:
        mode = "sample"
        items = random.sample(all_active, min(args.sample, len(all_active)))

    # Preview: scope resolved, report and stop before the first (paid) Claude call.
    if args.preview:
        emit_preview(len(items), "rows", [o.get("name", "?") for o in items], mode=mode)
        return

    # Dry runs are logged too: they skip DATABASE writes, not API calls, so they cost the
    # same as a live run. The "-dryrun" mode suffix is how readers tell them apart.
    run_mode = mode + ("-dryrun" if args.dry_run else "")
    run_row = supabase_insert_one(supabase_url, "agent_runs", {
        "agent": "deadline_checker",
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
    dry_run_results = []

    for i, opp in enumerate(items):
        print(f"[{i + 1}/{len(items)}] {opp['name'][:60]}...", end=" ")
        try:
            info, cost, searches, attempts = check_one(opp, anthropic_key)
            total_cost += cost
            total_searches += searches
            retried += attempts - 1
            # Silent skip-search: use_web_search=True but Claude answered from training data
            # instead of invoking web_search. A 0-search result means status/important_dates
            # were NOT verified live this run. check_one() re-rolls the search decision once
            # before giving up; this counts what survives the retry, and such a row is now
            # SKIPPED rather than written.
            #
            # Skipping matters more here than it looks. check_one() returns an empty info on
            # a silent call, so writing it would blank the row's real status and
            # important_dates AND stamp last_checked_at — destroying good data and then
            # hiding the damage behind the staleness filter. Leaving the row untouched keeps
            # what is there and leaves it due, so the next pass re-rolls.
            if searches == 0:
                silent_search_count += 1
                print(f"[SILENT after retry] nothing written; row keeps its existing dates "
                      f"and stays due, ${cost:.4f}")
                continue
            status = info.get("status") if info.get("status") in VALID_STATUS else "unknown"
            important_dates = info.get("important_dates") or []
            if not isinstance(important_dates, list):
                important_dates = []
            important_dates = [d for d in important_dates if isinstance(d, dict) and d.get("date_iso")]
            changed = status != opp.get("status") or important_dates != (opp.get("important_dates") or [])
            if changed:
                updated += 1
            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
            if args.dry_run:
                dry_run_results.append({
                    "id": opp["id"],
                    "name": opp["name"],
                    "url": opp.get("url"),
                    "status": status,
                    "important_dates": important_dates,
                    "was_estimated": bool(info.get("was_estimated")),
                    "important_date_note": info.get("important_date_note"),
                    "changed": changed,
                    "web_searches": searches,
                    "cost_usd": round(cost, 4),
                })
            else:
                supabase_patch(supabase_url, "opportunities", {"id": f"eq.{opp['id']}"}, {
                    "status": status,
                    "important_dates": important_dates,
                    "was_estimated": bool(info.get("was_estimated")),
                    "important_date_note": info.get("important_date_note"),
                    "dates_last_checked_at": now_iso,
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
        # No explicit sleep here: this module's own call_claude() enforces --min-delay
        # between calls (see _enforce_rate_limit above). This comment previously claimed
        # the throttle came from gemini_common.call_gemini(), which was wrong — nothing in
        # this script goes through gemini_common, so batch runs were entirely unthrottled.

    print(f"\n[SUMMARY] checked: {len(items)}, updated: {updated}, errors: {errors}, "
          f"silent (no-search) checks after retry: {silent_search_count}/{len(items)}, "
          f"silent-search retries: {retried}, cost: ${total_cost:.4f}")
    if mode == "sample" and items:
        per_item = total_cost / len(items)
        projected = per_item * len(all_active)
        print(f"[PROJECTED] ~${per_item:.4f}/item -> full catalog ({len(all_active)} active rows) "
              f"~${projected:.2f} for a full pass.")

    if args.dry_run:
        # Seconds, not just the date — see snapshot_stamp(). Same-day runs used to
        # overwrite each other's already-paid-for output.
        stamp = snapshot_stamp()
        dry_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                f"deadline_check_dry_run_{stamp}.json")
        with open(dry_path, "w", encoding="utf-8") as f:
            json.dump(dry_run_results, f, indent=2, ensure_ascii=False)
        print(f"[OK] Wrote dry-run deadline snapshot: {dry_path}")
        print("[DRY RUN] No writes performed.")

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

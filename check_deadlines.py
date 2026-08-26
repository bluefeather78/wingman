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

THREE more ways a check can produce nothing writable, all routed through the one shared
deadline_write_decision() so the batch loop and the interactive endpoint cannot drift:

  * phase 1 never searched          -> {}    (the original guard, above)
  * phase 2's JSON was unreadable   -> None  (2026-08-24). This used to collapse into {} and
    be WRITTEN as an authoritative status=unknown with no dates: one garbled response wiped
    a row's real deadlines and the stamp then served that hole to every student for 7 days.
    "We looked but cannot read the answer" is not "there is nothing to find".
  * searched, parsed, found nothing, and the row already HAS dates -> keep them. A verified
    empty result is far more often a search miss than a program withdrawing its dates; a
    genuinely dead program comes back as not_running, which still writes.

A row with no existing dates is still written and stamped on an empty result — there is
nothing to lose, and not stamping would re-bill that row on every view forever.

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

ESCALATION LOOP (2026-08-25, G1): phase 1 is now a LOOP of up to ESCALATION_RUNGS rounds
(current cycle -> prior cycle -> subpages), each capped at one web_search, each injecting a
distinct strategy, stopping as soon as a self-reported found-signal is satisfied, then running
phase 2 once over the UNION of everything fetched. This fixes the old single-call cap where
MAX_SEARCHES=1 meant only the FIRST of the prompt's ordered searches (current cycle) ever ran,
so the prior-cycle estimation basis never did. check_one() now returns a 5-tuple ending in
`site_reached`; deadline_write_decision() gained an `unreachable-fallback` outcome and a
`rolling` status carve-out. See DEADLINE_AND_TASK_PLAN.md P2-P4.

SETUP:
    .env needs SUPABASE_URL, SUPABASE_SERVICE_KEY, ANTHROPIC_API_KEY.
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
# Rung 4 (P5) draws its allowed third-party listing domains from the operator's allowlist.
# This is the ONLY off-domain sourcing the deadline loop is permitted, and it degrades to
# "keep nothing off-domain" when the table is absent — see _load_trusted_domains below.
import aggregators_common
# P6c: capture the fetched page CONTENT (not just URLs) so a NON-estimated date can be verified
# against it in code (page_text.date_is_on_page) — the deadline analogue of the task quote check.
import source_capture
import page_text

# "rolling" (2026-08-25, G3): a genuine continuous-admission / always-open program, for
# which an EMPTY important_dates is the CORRECT answer — there is no cycle to find. It is
# distinct from "running" (has or will have dated cycles) and from "unknown" (we could not
# find out). The client maps it to an "open now — apply anytime" state; deadline_write_decision
# writes it even with zero dates, the same carve-out "not_running" gets, because the empty
# list is the answer rather than a search miss. See G3 in DEADLINE_AND_TASK_PLAN.md.
VALID_STATUS = {"running", "not_running", "rolling", "unknown"}

# Statuses whose CORRECT answer is an empty important_dates, so the "found nothing, keep the
# existing dates" guard must NOT swallow them — an empty list from one of these is a real
# finding, not a search miss.
EMPTY_IS_VALID_STATUS = {"not_running", "rolling"}

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
                return_sources=False, return_captured=False, cache_system=False):
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
    # Prompt caching (2026-08-25, escalation loop): the escalation loop makes up to 3 phase-1
    # calls for ONE opportunity, seconds apart, with a BYTE-IDENTICAL system prompt (only the
    # per-round user_content differs). Marking the system block ephemeral lets rounds 2-3 read
    # it from cache instead of re-billing ~1500 tokens each. A single-round early-exit pays a
    # small (~25%) cache-WRITE premium on the system tokens only, which is dwarfed by the
    # saving whenever a second round runs. Anthropic silently declines to cache a prompt below
    # the model's minimum cacheable length, so this can only help or no-op, never error.
    # estimate_cost() already folds cache_creation/cache_read tokens into the bill.
    system_field = system
    if cache_system:
        system_field = [{"type": "text", "text": system,
                         "cache_control": {"type": "ephemeral"}}]
    body = {
        "model": CLAUDE_MODEL,
        "max_tokens": CLAUDE_MAX_TOKENS,
        "system": system_field,
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

    # Extra elements only on request, so the existing 2-tuple call sites are untouched —
    # same convention as gemini_common's return_grounding.
    if return_captured:
        # P6c: the URL list (for phase 2 + the rung-4 trust filter, unchanged) PLUS the fetched
        # CONTENT as CapturedSource objects, so a non-estimated date can be verified against the
        # page it should have come from. parse_captured_sources ignores encrypted search
        # snippets — a date is verified only against a page we actually fetched.
        return (text, usage, extract_source_urls(response_data),
                source_capture.parse_captured_sources(response_data))
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
do not estimate future dates; (c) the program admits CONTINUOUSLY with no application cycle or deadline at \
all — a rolling or always-open program (e.g., "join anytime", "rolling admissions with no deadline", a \
standing club/advisory board/volunteer role that is simply always open) → status="rolling", with an EMPTY \
important_dates, because there genuinely is no date to find. Use "rolling" ONLY when the page positively \
says applications are continuously open with no deadline — NOT when you merely failed to find a deadline \
(that is "unknown"), and NOT when a deadline exists but the opening is rolling (that is "running" with the \
deadline you found). Evidence of recurrence ("Next cycle in Fall", "2027 details TBA", "Check back \
for 2027") → treat as "running" with forward-dated important_dates.

ESTIMATION LOGIC (single source of truth — apply in this order):
a. Found explicit current/upcoming-cycle dates → use them, was_estimated=false for those entries.
b. No current-cycle dates found, but you found last cycle's real dates AND the program looks recurring \
(no evidence it's discontinued) → roll each date forward by ~1 year (or to the next plausible \
occurrence), was_estimated=true, status="running". This is the expected path when a new cycle's page \
isn't live yet — use it; don't default to "unknown."
b2. The current cycle publishes SOME real dates (very often just the deadline) but NOT the opening, \
and a prior cycle published both → do NOT simply roll last cycle's opening forward a year. Take the \
opens-to-deadline INTERVAL from the prior cycle and apply it to this cycle's real deadline — if \
applications opened 10 weeks before last cycle's deadline, estimate this cycle's opening 10 weeks \
before the posted one. was_estimated=true. Whenever a cycle has shifted, the interval survives and the \
calendar date does not, so this is the more accurate estimate; it is also the single most common way an \
opening date can be recovered at all.
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
- An "opens" entry is REQUIRED whenever the program has any application or registration step. It is \
the single most decision-relevant date a student has: the app marks a program HAPPENING NOW the \
moment its first date has passed, so a program carrying only a deadline reads as "not started yet" \
right up until the day it closes. If the current cycle's opening is not posted, project the prior \
cycle's (was_estimated=true). Omit it only if no cycle you found ever published one.
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
- If you report a deadline but NO opens date, say in one clause why — "rolling admissions, no \
published open date", "no prior cycle found", "registration is continuous". A deadline with no \
opens date and no explanation is an incomplete answer, not a complete one.
- Never report an estimated date as today's date. An estimate must be what its own stated basis \
computes to — "~10 weeks before the January 10 deadline" is late October, not today. Anchoring an \
estimate to the current date makes a program read as open right now when it is not; omit the date \
instead if you cannot work out the real one.

Write this up in plain prose. State the status and why, list every date you found or estimated \
with its label and whether it is confirmed or estimated, and name the pages you actually \
fetched. No JSON, no schema, no markdown fences — just write it. Stay well within a \
1000-token response.

END your response with EXACTLY these three lines and nothing after them (they are read by a \
machine to decide whether more searching is needed — answer only about what YOU did THIS \
round):
SITE_REACHED: yes|no
FOUND_CONFIRMED_DATES: yes|no
FOUND_PRIOR_CYCLE_BASIS: yes|no
where SITE_REACHED is whether you successfully fetched the program's OWN web page this round \
(no = it was unreachable, an empty JS-only shell, or you only reached third-party pages); \
FOUND_CONFIRMED_DATES is "yes" only when you found the current/upcoming cycle's dates \
EXPLICITLY posted AND they include the registration/application OPENING date (or the program \
has no application step, e.g. it is rolling/always-open) — a confirmed deadline with NO opening \
date is "no", because the opening still has to be recovered from a prior cycle; and \
FOUND_PRIOR_CYCLE_BASIS is whether you found a prior cycle's real dates that can be used as an \
estimation basis. Say "no" honestly rather than guessing — a "no" simply runs one more search."""


# PHASE 2. Notes plus the real fetched URLs in, strict JSON out, no tools. The schema and the
# agreement rules live here rather than in phase 1 because a strict output format is free on a
# call that does not need to search — and ruinous on one that does.
EXTRACT_SYSTEM = """You turn a researcher's written notes about one extracurricular \
opportunity's application cycle into a strict JSON record. You are NOT researching — \
everything you need is in the notes, and you must not add a date, a status or a caveat that \
is not in them.

Today's date is {today}.

PRIORITY — this app exists so a student never misses a deadline. An empty "important_dates" \
gives the student no reminder, no calendar entry and no "happening now" signal, so a \
well-founded estimate is ALWAYS better than an empty field. When you estimate a date from a \
window or range, round toward the EARLIER edge, never the later one: a date shown before the \
true deadline is fail-safe — a student who acts on it finishes in time — while a date shown \
after it causes the exact miss this app exists to prevent. Bias every uncertain deadline \
estimate early. This does NOT license inventing dates: an estimate must still (a) be on or \
after {today}, (b) stay inside the window the notes actually support — the near edge of the \
stated range, not a date conjured months before it — and (c) be marked "estimated": true with \
the real window kept in the note, so the student is prompted to confirm rather than trust a \
placeholder. A past-dated or visibly-wrong estimate is worse than an empty field: the first \
reads as "you already missed it" and makes a student abandon a program still open to them, the \
second teaches them to ignore every date the app shows.

RULES, in order:
- Every date the notes reason about must become a structured entry in "important_dates" — \
never leave a date mentioned only in prose in "important_date_note". If the notes give enough \
basis to write a date into the note, that date gets its own structured entry.
- A date WINDOW, month, season or range the notes commit to ("abstract deadlines typically \
October-December", "opens in fall", "regionals in early spring") is a date you are reasoning \
about — it MUST become a concrete structured entry with "estimated": true, not be left as prose \
in the note. Materialise it by picking one representative day inside the stated window and \
routing it through the on-or-after-{today} check below: for a "deadline", take the EARLIEST \
day in the window (so the student is warned before the soonest real cutoff); for "opens", \
"event_start" or a bare season, take the FIRST day of the window. Never collapse a multi-part \
cycle into one entry — an abstract deadline, a regional event and a national event named as \
three separate windows are three separate entries. This whole materialisation rule applies \
ONLY to a running program: if the notes say the program is suspended, discontinued or not \
running this cycle, status is "not_running" and you report NO future dates — do not materialise \
a window for it. A well-founded estimate beats an empty field, but a FABRICATED deadline for a \
program that is not accepting anyone is worse than either — it tells a student to prepare for \
something that will not happen.
- A window whose early edge is already in the PAST (today falls inside or after the window's \
start) is the dangerous case: do NOT roll the whole window a full year forward — that hides a \
deadline still imminent THIS cycle, which is the exact miss this app prevents. For a "deadline", \
use the EARLIEST day of the window that is still on or after {today}. For an "opens" date whose \
day has already passed, OMIT the structured entry rather than anchoring it to {today}, and say \
in the note that registration has likely already opened. Only roll a window forward a year when \
it lies ENTIRELY in the past.
- A window may stay prose-only ONLY when the note explains why there is genuinely no date to \
give at all (e.g. "rolling admissions, no published open date"). A merely typical or historical \
pattern ("deadlines are usually in the fall", "historically October-December") is NOT such a \
case — that is exactly the situation you must materialise into a dated entry, not exempt.
- Every date OR date-window in "important_date_note" — a specific day, a month, a season or a \
range — must have a matching entry in "important_dates", and vice versa — the two must agree. A \
vague summary that names no window ("dates vary by cycle") does not satisfy this: if the notes \
give any window at all, name it and date it.
- Every date must be on or after {today}, and a past date is never reported as-is. If the \
notes give the dates of a cycle that has already ended and do NOT say the program is \
discontinued, project each one onto its next annual occurrence — same month and day, plus the \
smallest whole number of years that lands on or after {today} — set "was_estimated": true, and \
say in the note that they are estimated from the last cycle. That is arithmetic on what the \
notes already establish, not research, and it is required: an empty "important_dates" for a \
program that visibly ran last year tells the student nothing about when to come back. Drop a \
past date only when the notes say the program is discontinued, or describe a genuine one-off \
event with no sign of recurrence.
- If the notes mention a registration or application OPENING date at all — this cycle's, or one \
projected from a prior cycle — it MUST appear as an entry with "type": "opens". This is the date \
the app reads to decide whether a program is open right now, and it is the one most often lost \
between the research and the schema. Never drop it to save space, and never demote it to "other".
- An estimated date must be the RESULT of the arithmetic its basis implies, and must never be \
today's date. If the notes say an opening falls ~10 weeks before a January 10 deadline, the answer \
is late October — not today, and not "now". Substituting today is a specific observed failure \
(2026-08-24): a row was given an opening of that very day while its own note said ~10-11 weeks \
before a 2027-01-10 deadline, which wrongly made the program read as open right now. If you cannot \
do the arithmetic, omit the date rather than anchoring it to today.
- Set "estimated" PER DATE: true if that specific date came from a prior cycle, an interval, or \
a vague pattern; false only if it is explicitly posted for the current cycle. A row routinely \
mixes the two — a confirmed deadline beside a projected opening — and the app shows this marker \
next to each date, so getting it per-entry is what stops a real date being labelled a guess and, \
worse, a guess being shown as fact. Do NOT also write "(estimated)" into the label; the field is \
what the app renders.
- "was_estimated" is true if ANY reported date came from a prior cycle or a vague pattern \
rather than an explicitly posted current-cycle date — it is the row-level roll-up of the \
per-date flags above, not a substitute for them.
- If the notes say the program is permanently discontinued, status is "not_running" and you \
report no future dates. If they say the current cycle is closed but the program recurs, \
status is "running" with forward-dated entries — the ones the notes give if they give them, \
otherwise the projection described above.
- If the notes say the program admits CONTINUOUSLY with no application cycle or deadline at \
all (rolling/always-open — e.g. "join anytime", "rolling with no deadline", a standing club or \
advisory board), status is "rolling" with an EMPTY important_dates: there is genuinely no date \
to report, and materialising one would be a fabrication. Use "rolling" ONLY when the notes \
positively say admission is continuous with no deadline — if the notes merely found no date, \
that is "unknown", and if they found a deadline, that is "running" with the deadline.
- If the notes found nothing at all — no current dates AND no past-cycle dates to project \
from — status is "unknown" with an empty important_dates. That is a valid outcome; never invent \
one to fill the schema. It does NOT apply when the notes carry a past cycle's real dates: those \
get projected, per the rule above.

The notes may end with three machine lines (SITE_REACHED / FOUND_CONFIRMED_DATES / \
FOUND_PRIOR_CYCLE_BASIS). They are search-progress flags for another system — ignore them \
entirely; they are NOT dates and NOT status.

Respond with ONLY a raw JSON object, no markdown fences, no preamble, no text after the JSON, \
matching exactly this schema: {{"status": "running, not_running, rolling, or unknown", \
"important_dates": [{{"label": "short specific label", "date_iso": "YYYY-MM-DD", "type": \
"opens, deadline, event_start, event_end, or other", "estimated": true or false}}], \
"was_estimated": true or false, \
"important_date_note": "one short sentence: status/estimate basis/caveat, or null"}}. Keep the \
response well within a 1000-token budget: up to 8 important_dates entries, ordered \
chronologically. If you must shorten, drop the least specific/least useful entry first (e.g. a \
duplicate "other" note) — never drop opens/deadline/event dates before that."""


def build_extract_system():
    return EXTRACT_SYSTEM.format(today=today_label())


# ---------- the escalation loop ("program source finder") ----------
# G1: MAX_SEARCHES caps web_search at ONE query per call, but a single call was told to run
# current-cycle, prior-cycle AND subpage searches in sequence — so only the first ever ran,
# and the prior-cycle search (the estimation basis, the thing that recovers an opening date)
# never happened. The fix is a LOOP of up to N rounds, each capped at one search, each
# injecting a DISTINCT strategy, reading a cheap self-reported found-signal to stop as soon
# as it is satisfied, then running phase 2 ONCE over the union of everything fetched.
#
# max_uses is a CEILING, not a target, and Anthropic bills per search PERFORMED (~$0.01), so
# early-exit already means "pay only until found" — a row whose current cycle is posted stops
# after one round at roughly the old single-call price. Only the hard rows (SPA, unposted
# cycle) climb the ladder, and those are exactly the rows that returned nothing before.
#
# Built deliberately as a reusable seam: P6 (task aggregator discovery) reuses this same
# prose-search-ON, per-round-max_uses:1, grounding-resolved-sources machinery — only the
# phase-2 EXTRACT differs (dates here, verified tasks there). Rung 4 (trusted third-party
# listings) lands with P5, drawing from the trusted_aggregators allowlist.
RUNGS = [
    ("current cycle",
     "FOCUS THIS ROUND: fetch the given URL and search the program's OWN site for the CURRENT "
     "or upcoming cycle's dates. This is the normal case."),
    ("prior cycle",
     "FOCUS THIS ROUND: search the program's OWN site specifically for the MOST RECENT PAST "
     "cycle's real dates — last cycle's opening AND deadline. These are the estimation basis "
     "when the current cycle is not yet posted, and this is the round that recovers an opening "
     "date via the prior cycle's opens-to-deadline interval."),
    ("subpages",
     "FOCUS THIS ROUND: search the program's OWN site for a FAQ, 'How to Apply', 'Key Dates', "
     "'Timeline' or 'Deadlines' subpage and fetch the best hit — deadline details usually live "
     "on a subpage that a top-level search snippet never shows."),
    # Rung 4 (trusted third-party listings) — P5, wired 2026. Reached ONLY when rungs 1-3
    # could not satisfy the found-signals (a hard row: SPA, site down, unposted cycle), and
    # ONLY when the operator's trusted_aggregators allowlist is non-empty. The {domains}
    # placeholder is filled at round time with that allowlist, and this round's sources are
    # trust-filtered before phase 2 (research_deadlines) so an untrusted third-party page can
    # never ground a date. Every date it yields is an ESTIMATE, forced estimated=true.
    ("trusted third-party",
     "FOCUS THIS ROUND: the program's own site did not yield dates. Search ONLY these "
     "operator-approved third-party listing / guide sites for THIS program's dates, and no "
     "other off-site source: {domains}. Anything you find on one of these is an ESTIMATE, not "
     "a confirmation — set \"estimated\": true for every date whose only support is one of "
     "these sites, and name the site in the note (e.g. \"from lumiere-education.com's listing\")."),
]
# Rungs 1-4. Rung 4 is a NO-OP unless the trusted_aggregators allowlist has domains, so this
# is safe to set to 4 before the table exists: the loop skips rung 4 when there is nothing to
# search, leaving pre-P5 behaviour (rungs 1-3) exactly unchanged. See research_deadlines.
ESCALATION_RUNGS = 4

_SIGNAL_RE = {
    "site_reached": ("SITE_REACHED",),
    "confirmed": ("FOUND_CONFIRMED_DATES",),
    "prior_basis": ("FOUND_PRIOR_CYCLE_BASIS",),
}


def _parse_signals(notes):
    """(prose_without_signal_lines, {site_reached, confirmed, prior_basis}).

    The three machine lines phase 1 is told to append are read here and STRIPPED, so phase 2
    never sees them (it is told to ignore them too, as defense in depth). A missing line reads
    as False — a silent/garbled round is treated as 'not satisfied', which just runs one more
    rung; the cost of a false 'no' is one extra search, of a false 'yes' a missed date, so we
    err toward 'no'.
    """
    signals = {"site_reached": False, "confirmed": False, "prior_basis": False}
    kept = []
    for line in (notes or "").splitlines():
        stripped = line.strip()
        matched = False
        for key, prefixes in _SIGNAL_RE.items():
            for prefix in prefixes:
                if stripped.upper().startswith(prefix + ":"):
                    value = stripped.split(":", 1)[1].strip().lower()
                    signals[key] = value.startswith("y")
                    matched = True
                    break
            if matched:
                break
        if not matched:
            kept.append(line)
    return "\n".join(kept).strip(), signals


def _search_round(opp, api_key, focus, retry_on_silent):
    """One phase-1 rung — prose out, tools on, ONE search. (notes, cost, searches, sources,
    attempts). Retries once on a zero-search answer (re-rolling, not re-prompting: the search
    decision is non-deterministic and cannot be forced). Cost is banked per attempt so an
    exception on the retry cannot discard what the first call already spent."""
    system = build_system(opp)
    user_content = (f"Opportunity: {opp['name']} ({opp.get('org') or 'unknown org'})\n"
                    f"URL: {opp['url']}\nKnown info: {opp.get('summary') or ''}\n\n"
                    f"{focus}\n\n"
                    f"Report the current status and every relevant date — registration "
                    f"open/close, event dates, notifications — not just a single deadline.")
    cost = 0.0
    notes, usage, sources, captured = "", {}, [], []
    attempts = 2 if retry_on_silent else 1
    for attempt in range(1, attempts + 1):
        notes, usage, sources, captured = call_claude(
            system, user_content, api_key, use_web_search=True, max_searches=MAX_SEARCHES,
            return_captured=True, cache_system=True)
        cost += estimate_cost(usage)
        searches = (usage.get("server_tool_use") or {}).get("web_search_requests", 0)
        if searches or attempt == attempts:
            return notes, cost, searches, sources, attempt, captured
        print("  [SILENT] no search invoked — retrying once", flush=True)
    return notes, cost, 0, sources, attempts, captured


RUNG_TRUSTED_THIRD_PARTY = "trusted third-party"


def _load_trusted_domains():
    """The operator's trusted third-party domains for rung 4, or [] when the allowlist is
    absent/unreachable. [] makes rung 4 a no-op — 'keep nothing off-domain', the degrade-not-
    break behaviour DEADLINE_AND_TASK_PLAN.md §5 specifies. Reads env creds (batch main()
    has run load_dotenv; the interactive process has them from app.config) and goes through
    aggregators_common's cached policy so a burst of checks is one Supabase read."""
    policy = aggregators_common.get_policy(
        os.environ.get("SUPABASE_URL", "").rstrip("/"),
        os.environ.get("SUPABASE_SERVICE_KEY", ""))
    return policy.trusted_domains()


def research_deadlines(opp, api_key, retry_on_silent=True, trusted_domains=None):
    """PHASE 1 as an ESCALATION LOOP — up to ESCALATION_RUNGS rounds, each a distinct strategy,
    stopping as soon as a found-signal is satisfied. Returns
    (combined_notes, cost, total_searches, union_sources, total_attempts, site_reached).

    site_reached is the OR across rounds of whether the program's OWN page was fetched — the
    G2/G3 signal that separates "we looked and there is nothing" (write a real answer) from
    "we could not reach the site" (leave the row due, do not freeze a hole for 7 days).

    Rung 4 ("trusted third-party", P5) is reached only when rungs 1-3 could not satisfy the
    found-signals AND the operator's allowlist is non-empty; its focus is filled with the
    allowlist and ITS sources are trust-filtered before they join the union, so an untrusted
    page can never reach phase 2. `trusted_domains` is loaded lazily when None, so neither
    call site (batch main / interactive route) had to change.
    """
    if trusted_domains is None:
        trusted_domains = _load_trusted_domains()
    all_notes, all_sources, all_captured = [], [], []
    total_cost, total_searches, total_attempts = 0.0, 0, 0
    site_reached = False
    for idx, (name, focus) in enumerate(RUNGS[:ESCALATION_RUNGS]):
        if name == RUNG_TRUSTED_THIRD_PARTY:
            # No allowlist -> nothing to search -> keep nothing off-domain. Skipping (rather
            # than searching the whole web) is what makes ESCALATION_RUNGS=4 safe before the
            # table exists.
            if not trusted_domains:
                continue
            focus = focus.format(domains=", ".join(trusted_domains))
        notes, cost, searches, sources, attempts, captured = _search_round(
            opp, api_key, focus, retry_on_silent)
        total_cost += cost
        total_searches += searches
        total_attempts += attempts
        clean, sig = _parse_signals(notes)
        if clean:
            all_notes.append(f"[Round {idx + 1} — {name}]\n{clean}")
        if name == RUNG_TRUSTED_THIRD_PARTY:
            # HARD guarantee: only trusted-domain pages from this rung reach phase 2. A
            # rung-4 date is therefore always grounded on an allowlisted site, and the focus
            # forced it estimated=true. (Rungs 1-3 search the program's OWN site and are NOT
            # trust-filtered — the plan gates rung 4's contribution, not the own-site rungs,
            # so own-site recall is unchanged.) The captured CONTENT is filtered the same way,
            # so a date is never "verified" against an untrusted third-party page.
            sources = [u for u in sources
                       if aggregators_common.domain_matches(u, trusted_domains)]
            captured = [c for c in captured
                        if aggregators_common.domain_matches(c.url, trusted_domains)]
        all_sources.extend(sources)
        all_captured.extend(captured)
        site_reached = site_reached or sig["site_reached"]
        # Confirmed current-cycle dates INCLUDING the opening are the best case — stop.
        if sig["confirmed"]:
            print(f"  [rung {idx + 1}/{name}] confirmed dates found — stopping early",
                  flush=True)
            break
        # Once the prior-cycle rung has run, a prior-cycle basis is enough to ESTIMATE the
        # opening from the interval; no need to climb further.
        if idx >= 1 and sig["prior_basis"]:
            print(f"  [rung {idx + 1}/{name}] prior-cycle basis found — stopping early",
                  flush=True)
            break
    combined = "\n\n".join(all_notes)
    union_sources = list(dict.fromkeys(all_sources))
    # Dedupe captured by URL, keeping the first (same rule parse_captured_sources uses).
    seen, union_captured = set(), []
    for c in all_captured:
        if c.url not in seen:
            seen.add(c.url)
            union_captured.append(c)
    return (combined, total_cost, total_searches, union_sources, total_attempts,
            site_reached, union_captured)


def extract_deadlines(opp, notes, sources, api_key):
    """PHASE 2 — notes plus the pages actually fetched in, strict JSON out. No tools."""
    source_block = "\n".join(f"- {u}" for u in sources) or "(none retrieved)"
    user_content = (f"Opportunity: {opp['name']} ({opp.get('org') or 'unknown org'})\n"
                    f"PAGES ACTUALLY FETCHED:\n{source_block}\n\n"
                    f"RESEARCHER'S NOTES:\n{notes}\n\nReturn the JSON object now.")
    text, usage = call_claude(build_extract_system(), user_content, api_key,
                              use_web_search=False)
    # None (not {}) when the JSON could not be parsed at all. The caller MUST be able to
    # tell "the model answered and found nothing" from "we could not read the answer" —
    # collapsing the two into {} is what let a garbled phase 2 be written to the catalog as
    # an authoritative status=unknown with no dates. See deadline_write_decision().
    return extract_json(text), estimate_cost(usage)


def check_one(opp, api_key, retry_on_silent=True):
    """Both phases. (info, cost, searches, attempts, site_reached).

    `info` carries THREE distinguishable outcomes, and every caller must tell them apart
    before writing anything (use deadline_write_decision below rather than re-deriving it):
        {}    phase 1 never searched, even after the retry — nothing was looked at.
        None  phase 1 searched but phase 2's JSON could not be parsed — we looked, but we
              cannot read what came back.
        dict  a real answer, which may still legitimately be status=unknown with no dates.

    `site_reached` (G2/G3) is whether any rung fetched the program's OWN page. It is passed to
    deadline_write_decision so an empty result on a row whose site we could NOT reach leaves
    the row due (unreachable-fallback) instead of stamping a hole for 7 days.

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
    notes, cost, searches, sources, attempts, site_reached, captured = research_deadlines(
        opp, api_key, retry_on_silent)
    if not searches:
        return {}, cost, 0, attempts, site_reached
    info, extract_cost = extract_deadlines(opp, notes, sources, api_key)
    # P6c: mark each date verified against the captured page content, IN PLACE. This does not
    # change check_one's return shape — both call sites read the enriched dates straight out of
    # info["important_dates"] — and it never deletes a date (see _verify_dates).
    verify_dates_against_capture(info, captured)
    return info, cost + extract_cost, searches, attempts, site_reached


def verify_dates_against_capture(info, captured):
    """Mark each date in `info` verified/unverified against the captured page content, and
    attach the URL it was found on. Returns the count of NON-estimated dates that could NOT be
    found — the quality signal (the deadline analogue of the task demotion rate).

    NEVER deletes a date (T7): an estimated/projected date is absent from every page by design,
    and a matcher false negative must not lose a real deadline. So an unverifiable confirmed
    date is only MARKED (`verified: false`), left in place for the client to render and for the
    summary to count.
    """
    if not isinstance(info, dict):
        return 0
    dates = info.get("important_dates")
    if not isinstance(dates, list):
        return 0
    pages = [(c.url, c.text) for c in (captured or []) if getattr(c, "text", "")]
    unverified = 0
    for d in dates:
        if not isinstance(d, dict) or not d.get("date_iso"):
            continue
        if d.get("estimated"):
            # A projected date is not on any page by construction; mark it, count nothing.
            d["verified"] = False
            continue
        hit = next((url for url, txt in pages
                    if page_text.date_is_on_page(d["date_iso"], txt)), None)
        d["verified"] = hit is not None
        if hit:
            d["source_url"] = hit
        else:
            unverified += 1
    return unverified


# ---------- What to do with a check's result ----------
# Shared by BOTH call sites (main() below and app/routes/opportunities.py's interactive
# endpoint) so the two cannot drift about when a row may be overwritten. Every "do not
# write" branch also means "do not stamp dates_last_checked_at", which is the whole point:
# stamping hides the hole behind the 7-day TTL and serves it to every student for a week.
SOURCE_VERIFIED = "fresh, real search"
SOURCE_SILENT = "unverified-fallback"       # phase 1 never searched
SOURCE_UNPARSED = "unparsed-fallback"       # searched, but phase 2's JSON was unreadable
SOURCE_KEPT = "kept-existing"               # searched, found nothing, row already had dates
SOURCE_UNREACHABLE = "unreachable-fallback"  # searched, empty, program's OWN site not reached


class DeadlineDecision:
    """(write?, the normalized fields, a log source, a human reason)."""

    __slots__ = ("write", "status", "important_dates", "was_estimated", "note",
                 "source", "reason")

    def __init__(self, write, source, reason, status=None, important_dates=None,
                 was_estimated=False, note=None):
        self.write = write
        self.source = source
        self.reason = reason
        self.status = status
        self.important_dates = important_dates or []
        self.was_estimated = was_estimated
        self.note = note


def normalize_deadline_info(info):
    """One raw phase-2 dict -> the four column values, coerced. Never raises."""
    info = info if isinstance(info, dict) else {}
    status = info.get("status") if info.get("status") in VALID_STATUS else "unknown"
    dates = info.get("important_dates")
    if not isinstance(dates, list):
        dates = []
    dates = [d for d in dates if isinstance(d, dict) and d.get("date_iso")]
    return status, dates, bool(info.get("was_estimated")), info.get("important_date_note")


def missing_opens_date(dates):
    """True when a result carries a deadline but no registration-opens date.

    Worth counting rather than shrugging at, because of what it does downstream: the app
    marks an opportunity HAPPENING NOW once its FIRST date has passed, so a row whose only
    date is a deadline can never say that — it reads "not started yet" right until the day it
    closes, which is the opposite of the truth for a student who could apply today. Measured
    2026-08-24: 13 of the 34 active rows that carry any dates (38%) had no opens entry.
    """
    types = {d.get("type") for d in (dates or []) if isinstance(d, dict)}
    return "deadline" in types and "opens" not in types


def deadline_write_decision(info, searches, existing_dates=None, site_reached=True):
    """Decide whether a check's result may be written over the catalog row.

    Five outcomes, four of which write nothing and leave the row DUE for a re-check:

      searches == 0        phase 1 never looked. check_one returns {}, so writing it blanks
                           the row's real dates. Long-standing guard; unchanged.
      info is None         phase 1 DID look but phase 2's JSON was unreadable. This used to
                           fall through to `{}` and be written as an authoritative
                           status=unknown with no dates — a garbled response silently wiping
                           good deadlines, then locked in for 7 days by the stamp.
      empty, site NOT      G2: we searched but never reached the program's OWN page (SPA, site
      reached              down, only third-party hits) and found no dates. Writing an empty
                           `unknown` here freezes a hole for 7 days over a transient outage.
                           Leave the row due so the next view auto-retries — the same reasoning
                           the silent-search guard uses, extended to "we could not read the
                           page" from "we never looked". Applies even with NO existing dates:
                           an unreachable site is not evidence of absence.
      empty, site reached, a verified "found nothing" ON A REACHED SITE is more often a search
      the row has dates    miss than a program withdrawing its dates (a genuinely dead program
                           comes back as not_running, a genuinely open-ended one as rolling —
                           both handled below). Keep what is there and leave the row due.
      anything else        write and stamp.

    Exceptions to the "empty" rules (these write even with zero dates):
      * status in EMPTY_IS_VALID_STATUS (not_running / rolling). "Discontinued" and
        "always-open, no deadline" are real, student-visible answers for which an empty
        important_dates is CORRECT, not a failure — so neither the unreachable nor the
        keep-existing guard may swallow them.
      * an empty `unknown` on a REACHED site with NO existing dates is written and stamped:
        there is nothing to preserve, the page really was read, and not stamping would re-bill
        that row on every single view forever to re-learn the same nothing.

    `site_reached` defaults True so a caller that cannot supply it (or a genuinely-empty row)
    behaves exactly as before; only an explicit False routes to the unreachable branch.
    """
    if not searches:
        return DeadlineDecision(False, SOURCE_SILENT,
                                "no web search was invoked, even after a retry")
    if info is None:
        return DeadlineDecision(False, SOURCE_UNPARSED,
                                "the search ran but the extracted JSON was unreadable")

    status, dates, was_estimated, note = normalize_deadline_info(info)
    empty_is_valid = status in EMPTY_IS_VALID_STATUS
    if not dates and not empty_is_valid:
        if not site_reached:
            return DeadlineDecision(False, SOURCE_UNREACHABLE,
                                    "found no dates and never reached the program's own page; "
                                    "leaving the row due to retry rather than freezing a hole")
        if existing_dates or []:
            return DeadlineDecision(False, SOURCE_KEPT,
                                    "found no dates; keeping the ones already on the row")
    return DeadlineDecision(True, SOURCE_VERIFIED, "verified by a real search",
                            status=status, important_dates=dates,
                            was_estimated=was_estimated, note=note)


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--sample", type=int, help="Check a random N-row sample instead of the full catalog.")
    group.add_argument("--all", action="store_true", help="Check every active row (default if no flag given).")
    # Targeted re-checks. Without this the only scopes were "everything" (~$84) and "a random
    # sample", so acting on a known, specific gap — say the rows carrying a deadline but no
    # registration-opens date — meant paying for the whole catalog to fix a handful of rows.
    # Same shape as the scraper's --seed-ids, and it pairs with clear_deadline_cache.py, which
    # takes ids too. Note the interactive endpoint checks staleness and this does not: an id
    # given here is re-checked whether or not its cache is fresh, which is the point.
    group.add_argument("--ids", nargs="+", metavar="ID",
                       help="Check these specific opportunity ids, ignoring the 7-day cache.")
    group.add_argument("--missing-opens", action="store_true",
                       help="Check every active row that has a deadline but no registration-opens "
                            "date. That gap makes an opportunity unable to read 'Happening Now'.")
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
    elif args.ids:
        mode = "ids"
        by_id = {o["id"]: o for o in all_active}
        items = [by_id[i] for i in args.ids if i in by_id]
        for missing in [i for i in args.ids if i not in by_id]:
            print(f"[WARN] No ACTIVE opportunity with id {missing!r} — skipped.")
    elif args.missing_opens:
        mode = "missing-opens"
        items = [o for o in all_active if missing_opens_date(o.get("important_dates"))]
        print(f"[OK] {len(items)} active row(s) carry a deadline but no opens date.")

    if not items:
        print("[OK] Nothing to check — the selected scope matched no rows.")
        return

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
    unparsed_count = 0
    kept_count = 0
    unreachable_count = 0
    missing_opens_count = 0
    unverified_dates_count = 0
    retried = 0
    dry_run_results = []

    for i, opp in enumerate(items):
        print(f"[{i + 1}/{len(items)}] {opp['name'][:60]}...", end=" ")
        try:
            info, cost, searches, attempts, site_reached = check_one(opp, anthropic_key)
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
            decision = deadline_write_decision(info, searches, opp.get("important_dates"),
                                               site_reached=site_reached)
            if not decision.write:
                if decision.source == SOURCE_SILENT:
                    silent_search_count += 1
                elif decision.source == SOURCE_UNPARSED:
                    unparsed_count += 1
                elif decision.source == SOURCE_UNREACHABLE:
                    unreachable_count += 1
                else:
                    kept_count += 1
                print(f"[{decision.source}] nothing written ({decision.reason}); row keeps "
                      f"its existing dates and stays due, ${cost:.4f}")
                continue
            status = decision.status
            important_dates = decision.important_dates
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
                    "was_estimated": decision.was_estimated,
                    "important_date_note": decision.note,
                    "changed": changed,
                    "web_searches": searches,
                    "cost_usd": round(cost, 4),
                })
            else:
                supabase_patch(supabase_url, "opportunities", {"id": f"eq.{opp['id']}"}, {
                    "status": status,
                    "important_dates": important_dates,
                    "was_estimated": decision.was_estimated,
                    "important_date_note": decision.note,
                    "dates_last_checked_at": now_iso,
                    "updated_at": now_iso,
                }, service_key)
            no_opens = missing_opens_date(important_dates)
            if no_opens:
                missing_opens_count += 1
            # P6c quality signal: dates the model reported as CONFIRMED (not estimated) that
            # we could not find on any page we fetched. High counts are the signal that would
            # justify acting on verification (e.g. downgrading), the way the task agent watches
            # its demotion rate — do not act speculatively, watch this first.
            row_unverified = sum(1 for d in important_dates
                                 if isinstance(d, dict) and not d.get("estimated")
                                 and d.get("verified") is False)
            unverified_dates_count += row_unverified
            print(f"{status}, {searches} search(es), ${cost:.4f}"
                  + (" [changed]" if changed else "")
                  + (" [no opens date]" if no_opens else "")
                  + (f" [{row_unverified} unverified date(s)]" if row_unverified else ""))
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
          f"unreadable extractions: {unparsed_count}/{len(items)}, "
          f"searched-but-empty (row kept its dates): {kept_count}/{len(items)}, "
          f"empty + site unreachable (left due): {unreachable_count}/{len(items)}, "
          f"deadline but no opens date: {missing_opens_count}, "
          f"confirmed dates not found on any fetched page: {unverified_dates_count}, "
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

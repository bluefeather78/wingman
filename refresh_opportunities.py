#!/usr/bin/env python3
"""Opportunity refresh agent: for each active opportunity in the Supabase `opportunities`
catalog, READS THE PROGRAM'S LIVE PAGE and updates all fields EXCEPT those managed by the
deadline and review agents. Runs to keep opportunity info current (eligibility, pricing,
program structure, location format, etc.) without interfering with deadline/review tracking.

*** MARQUEE M1 (MARQUEE_DECISIONS.md) — do not change without Shama's approval ***
This agent fills metadata by FETCHING AND READING each program's own page (a free plain-HTTP GET
via page_text.fetch_page_text), never from model memory. Plain HTTP returns the page's real bytes,
so the text is held — proof the page was read, and the option to verify. A fetch failure (non-200,
blank/JS shell, TLS error) SKIPS the row (no write, no stamp, retry next run) — it does not fall
back to memory. This was reversed once, silently, inside an unrelated commit; M1 exists so it
cannot happen again. The extraction call runs use_web_search=False on purpose and correctly: the
page text is handed to the model in the prompt, so there is nothing to search and no memory answer
to invite.

Since 2026-08-28 the fetch has a HEADLESS-BROWSER FALLBACK (allow_browser=True → page_text runs a
headless Chromium when plain HTTP fails). This is fully M1-consistent — the browser still reads the
program's LIVE page (running its JS, real fingerprint), never memory — and it exists because ~22%
of catalog pages bot-wall or JS-render against a plain-HTTP client (measured recovery: 156 of 329,
47%; catalog fetchability 78%→88%). Playwright is an OPTIONAL install; absent it, the fetch degrades
to plain HTTP and this agent stays runnable stdlib-only. A page the browser also cannot read is
still SKIPPED, never invented — the M1 rule is unchanged, only the fetcher got more capable.

Fields NEVER touched by this agent (reserved for other agents):
  - status, important_dates, was_estimated, important_date_note, dates_last_checked_at (deadline agent)
  - review_status, review_summary, review_sources, last_reviewed_at (review agent)
  - url, link_status, link_status_code, link_checked_at, link_dead_since (check_links.py)
  - is_active, source (system fields)

Fields this agent CAN update:
  - name, org, summary, type, price, state, location, intl, season, category,
    eligibility, grade_min, grade_max, cost, subject_tags, contact_email

*** WHY `url` IS NOT IN THAT LIST, ADDED 2026-08-23 ***
It used to be. This agent calls Gemini with use_web_search=False — deliberately, for cost —
and then wrote whatever `url` came back straight onto a live, student-facing catalog row.
That is the EXACT mechanism behind the scraper's measured 26% dead-link rate: with no search,
the model writes URLs from memory, and they come back with the right host and a path off by
one segment (`juilliard.edu/music/pre-college` for the real
`.../music/preparatory-division/juilliard-pre-college`). Every one of those 30 dead URLs was
a constructed deep path. See SCRAPER_PLAN.md and url_validate.py.

The scraper's fix — take the URL from `groundingChunks[].web.uri`, i.e. a page the search
actually retrieved — is not available here, because there is no search to ground against.
So this agent does not write the field at all. `check_links.py` owns link health: it verifies
every URL over plain HTTP for free and deactivates the ones that are provably gone. A URL
that needs REPLACING is a human edit in the admin console's Edit modal, on a row the link
checker has already put in front of someone.

The damage this avoids is asymmetric and that is the whole argument: a stale-but-working URL
costs a student nothing, and a confidently-rewritten wrong one sends them to a 404 with no
signal that anything happened. Do not put `url` back without a grounded source for it.

Reads each program's live page (free plain-HTTP GET, then gemini-3.5-flash-lite extracts from the
fetched text). The fetch itself is free; only the extraction call costs money, and only for rows
that actually fetch — a blank/JS shell or a non-200 is skipped cheaply (~1300 rows, roughly a
fraction of a cent each that fetches). GEMINI_API_KEY is required and is the ONLY key this
agent needs: the contact-email fallback also runs on Gemini as of 2026-08-29 (MARQUEE M9
provider swap), so ANTHROPIC_API_KEY is no longer used here at all.

WALL TIME: dominated by the rate limiter, not by the API. gemini_common enforces a minimum
delay between every Gemini call (default 5s, see --min-delay), so 1200+ rows is roughly
1200 x 5s ~= 100 minutes at the default. An earlier version of this docstring claimed
"15-25 minutes", which predated the rate limiter and was never true alongside it. Lower
--min-delay shortens the run but risks the HTTP 429s the delay was introduced to fix.

SETUP:
    .env needs SUPABASE_URL, SUPABASE_SERVICE_KEY, GEMINI_API_KEY.
    Writes to the shared `agent_runs` table (schema in check_deadlines.py's docstring) as
    agent='metadata_refresher', so runs show up in the admin console alongside the other three.

USAGE:
    python refresh_opportunities.py --preview      # resolve scope + count only, no API calls, free
    python refresh_opportunities.py --sample 10    # random 10-row sample, prints projected cost
    python refresh_opportunities.py --all          # every active row
    python refresh_opportunities.py --dry-run      # calls the API but writes nothing; dumps JSON
"""
import argparse
import datetime
import json
import os
import random
import re
import sys
import urllib.error

import page_text
from agent_common import add_agent_args, apply_timing, clean_email, emit_preview, snapshot_stamp
from contact_email_common import resolve_contact_email
from gemini_common import call_gemini, extract_json, estimate_cost
from supabase_common import load_dotenv, supabase_get, supabase_insert_one, supabase_patch

# Windows consoles default stdout to cp1252, which raises UnicodeEncodeError on any character
# outside that codepage — an opportunity name with a ʻokina (ʻ), a curly quote or an em
# dash. A crash in a PROGRESS print then aborts the whole (paid, ~2h) run: a full-catalog pass
# died at row 984/1488 on 2026-08-28 printing "Summer Science Program (SSP) ʻBiochemistry".
# Force UTF-8 with replacement so no console print can ever kill the loop again. Guarded: not
# every stream supports reconfigure (a pipe/StringIO under pytest does not), and this must not
# itself raise at import. This is console output only — it touches NOTHING in MARQUEE M1.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

VALID_TYPES = {'Program', 'Internship', 'Competition', 'Research', 'Volunteer', 'Journal', 'Conference'}
VALID_SUBJECTS = ['Mixed', 'STEM', 'Medicine', 'Humanities', 'Art', 'Business', 'Engineering',
                  'Computer Science', 'Mathematics', 'Biology', 'Physics', 'Astronomy',
                  'Chemistry', 'Leadership', 'Law', 'Logic', 'Education']
VALID_PRICE = {'Free', 'Paid'}
VALID_LOCATION = {'In-Person', 'Remote', 'In-Person and Remote'}
VALID_INTL = {'International Students', 'Domestic Students'}
VALID_SEASON = {'Summer', 'Year-Long', 'Spring', 'Fall', 'Winter'}

# Queue marker from activation_refresh_schema.sql: a row the console activated and enqueued
# for a metadata refresh. This agent is the DRAIN — it clears the marker once it successfully
# reads the row's page (reason == 'ok'), whether or not any field changed, so the console's
# "awaiting refresh" list empties as rows are processed. A one-shot queue flag, not a
# staleness clock. Absent until the migration is run; the fetch below degrades and the drain
# then no-ops (see _get_opportunities).
ACTIVATION_REFRESH_COLUMN = "activation_refresh_queued_at"
_queue_col_enabled = True


def _get_opportunities(supabase_url, params, service_key):
    """supabase_get for the opportunities table, tolerant of activation_refresh_schema.sql
    not being run. If the queue column is in the select and PostgREST 400s, drop it and
    latch it off for the rest of the run — the drain then simply does nothing, and the
    metadata refresh itself is unaffected."""
    global _queue_col_enabled
    try:
        return supabase_get(supabase_url, "opportunities", params, service_key)
    except urllib.error.HTTPError as e:
        sel = params.get("select", "")
        if e.code == 400 and _queue_col_enabled and ACTIVATION_REFRESH_COLUMN in sel:
            _queue_col_enabled = False
            trimmed = [c for c in sel.split(",") if c != ACTIVATION_REFRESH_COLUMN]
            return supabase_get(supabase_url, "opportunities",
                                dict(params, select=",".join(trimmed)), service_key)
        raise


def build_system(opp):
    today = datetime.date.today().isoformat()
    # MARQUEE M1 (MARQUEE_DECISIONS.md): this agent fills metadata by READING THE PROGRAM'S
    # LIVE PAGE, never from model memory. check_one() fetches the page and passes its TEXT into
    # this prompt; the model's only job is to extract fields FROM that text. Do NOT rewrite this
    # back toward "recall from memory" / "you have no web access" — that reversal (done silently
    # inside an unrelated commit) is exactly what M1 exists to prevent. Restoring memory-mode
    # requires Shama's explicit approval and its own dedicated commit.
    return f"""You extract catalog metadata for a high-school extracurricular opportunity \
(program, internship, competition, or research position) from the text of its OWN web page, \
which is provided to you below. Today's date is {today}.

Fill each field ONLY from the page text you are given. If the page does not state something, \
return null for it — never guess, never fill it in from what programs of this kind usually look \
like, and never carry over a value the page does not support. A null leaves the existing curated \
value in place; a plausible invention silently overwrites one, so null is the correct, safe answer \
whenever the page is silent.

GOAL: core program metadata students use to judge relevance — a one-paragraph summary, \
eligibility, pricing, format, location, grade range, subject area. NOT deadlines or program \
status (a separate agent with live search handles those) and NOT the URL (see below).

DO NOT return a URL. You are given the program's URL for identification only — it is maintained \
by a separate checker, and any URL written here is discarded (a separate marquee rule, P3).

Schema: {{"name": "string or null", "org": "string or null", "summary": "one paragraph \
or null", "type": "one of {', '.join(sorted(VALID_TYPES))} or null", \
"price": "Free or Paid or null", "location": "In-Person, Remote, or In-Person and Remote or null", \
"intl": "International Students or Domestic Students or null", "season": "Summer, Year-Long, \
Spring, Fall, or Winter or null", "eligibility": "concise description or null", \
"grade_min": "integer (9-12) or null", "grade_max": "integer (9-12) or null", \
"cost": "string (e.g. '$2000-3500') or null", "subject_tags": "[array of tags from the list: \
{', '.join(VALID_SUBJECTS)}] or null", "contact_email": "a real contact email address for the \
program if you find one (e.g. admissions or info@), else null — never guess or construct one"}}.

Return ONLY the raw JSON object, no markdown, no preamble. Keep response under 800 tokens. \
For anything the page does not state, use null rather than guessing."""


def _default_fetch(url):
    """M1 fetch: the free plain-HTTP GET first, then a headless-browser fallback when it fails.

    allow_browser=True is the 2026-08-28 addition. The browser still reads the LIVE page (it
    runs the page's own JS and presents a real fingerprint) — never model memory — so MARQUEE
    M1 is intact and, if anything, strengthened: we now read what a student's browser sees, so
    the ~22% of catalog pages that bot-wall or JS-render (measured recovery: 156 of 329, 47%)
    stop being silently skipped. Playwright is an OPTIONAL install; if absent the fallback
    degrades to plain HTTP and this agent stays runnable stdlib-only.
    """
    return page_text.fetch_page_text(url, allow_browser=True)


def check_one(opp, gemini_key, fetch=None):
    """MARQUEE M1 (MARQUEE_DECISIONS.md): read the program's LIVE page and extract metadata
    FROM IT. Never from model memory.

    Returns (info, cost, reason):
      reason == 'ok'       -> info is the parsed dict; the caller may write validated fields.
      reason == 'no-fetch' -> the page could not be read; info is {}. The caller MUST skip:
                              no write, no stamp, retry next run. NEVER fall back to memory —
                              that is the reversal M1 exists to prevent. A blank row is honest;
                              an invented one is not.
      reason == 'unparsed' -> the page was read but the model's JSON was unreadable; info is
                              None. The caller keeps existing values and does not stamp.

    The fetch is a FREE plain-HTTP GET (page_text.fetch_page_text): it returns the page's real
    bytes, so we hold the text — proof the page was read, and the option to code-verify. A
    blank/JS shell or a non-200 comes back as a failure reason and is skipped, rather than
    letting the model "quote" from nothing (that blank-shell path is how "read the page"
    quietly degrades back into "return nothing", the M1 failure mode). `fetch` is injectable
    for tests. A fetch failure here is evidence about our HTTP client, never about the program
    (check_links measured ~9% of the catalog 403ing a non-browser agent), so the row is skipped
    and retried, never condemned.
    """
    if fetch is None:
        fetch = _default_fetch
    page, reason = fetch(opp.get("url"))
    if reason != "ok" or not (page or "").strip():
        return {}, 0.0, "no-fetch"

    system = build_system(opp)
    user_content = (f"Program: {opp['name']} ({opp.get('org') or 'unknown org'})\n"
                    f"URL (identification only — do not return a URL): {opp['url']}\n\n"
                    f"PAGE TEXT (extract ONLY from this):\n{page[:16000]}\n\n"
                    f"Return the schema JSON now. Null for anything the page does not state.")
    # use_web_search=False is CORRECT here and is not the M1 violation: the page's text is
    # already in the prompt above, so there is nothing to search for and no memory answer to
    # invite. The fetch happened over the real page (free HTTP, above).
    text, usage = call_gemini(system, user_content, gemini_key, use_web_search=False,
                              max_tokens=1200, model="gemini-3.5-flash-lite")
    cost = estimate_cost(usage)
    # Bank the cost BEFORE the parse (the call is already billed); a parse failure is a skip,
    # not an error, and must not discard the spend or fall through to memory.
    try:
        info = extract_json(text)
    except (ValueError, json.JSONDecodeError):
        return None, cost, "unparsed"
    if not isinstance(info, dict):
        return None, cost, "unparsed"
    return info, cost, "ok"


def clean_update_dict(info):
    """Extract and validate fields from the API response, dropping nulls and invalid values."""
    update = {}

    # String fields. `url` is deliberately NOT among them — see the module docstring. The
    # model still sees a url in its input (for identification) and may still echo one back;
    # dropping it here is what makes that harmless, and is the half that has to hold even
    # if the prompt is later reworded.
    for field in ["name", "org", "summary", "eligibility"]:
        val = info.get(field)
        if isinstance(val, str) and val.strip():
            update[field] = val.strip()

    # `cost` is a string like "$2000-3500", but the model routinely returns a bare "0"
    # (or "$0", "0.00") for a free program — a valid non-empty string, so without this it
    # OVERWRITES a good curated value ("Free", "No cost; volunteer hours count...") with a
    # meaningless "0". Pricing free/paid is already carried by the `price` enum, so a
    # numeric-zero cost carries no information and is dropped rather than written.
    cost = info.get("cost")
    if isinstance(cost, str) and cost.strip():
        cost = cost.strip()
        stripped = cost.lstrip("$").replace(",", "").strip()
        if not re.fullmatch(r"0+(\.0+)?", stripped):
            update["cost"] = cost

    contact_email = clean_email(info.get("contact_email"))
    if contact_email:
        update["contact_email"] = contact_email

    # Enum fields
    if info.get("type") in VALID_TYPES:
        update["type"] = info["type"]
    if info.get("price") in VALID_PRICE:
        update["price"] = info["price"]
    if info.get("location") in VALID_LOCATION:
        update["location"] = info["location"]
    if info.get("intl") in VALID_INTL:
        update["intl"] = info["intl"]
    if info.get("season") in VALID_SEASON:
        update["season"] = info["season"]

    # Integer fields (grade bounds)
    grade_min = info.get("grade_min")
    if isinstance(grade_min, int) and 9 <= grade_min <= 12:
        update["grade_min"] = grade_min
    grade_max = info.get("grade_max")
    if isinstance(grade_max, int) and 9 <= grade_max <= 12:
        update["grade_max"] = grade_max

    # Array field (subject tags)
    tags = info.get("subject_tags")
    if isinstance(tags, list):
        valid_tags = [t for t in tags if isinstance(t, str) and t in VALID_SUBJECTS]
        if valid_tags:
            update["subject_tags"] = valid_tags

    return update


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--sample", type=int, help="Check a random N-row sample instead of all rows.")
    group.add_argument("--all", action="store_true", help="Check every active row (default if no flag given).")
    group.add_argument("--ids", type=str, default=None,
                       help="Comma-separated opportunity ids to refresh (e.g. ec18771,ec18772). "
                            "Ignores the is_active filter, so it works on queued rows the scraper "
                            "just produced or a just-activated set — the way the new-angle pipeline "
                            "enriches rows before or right after review.")
    group.add_argument("--pending", action="store_true",
                       help="Refresh queued rows (is_active=false, moderation_status pending/null) "
                            "so a scraped batch can be enriched from its live pages before a human "
                            "reviews it, instead of landing in the queue thin.")
    group.add_argument("--awaiting-refresh", action="store_true",
                       help="Refresh only ACTIVE rows currently enqueued for a metadata refresh "
                            "(activation_refresh_queued_at is set) — rows activated in the admin "
                            "console and not yet read from their live page. This DRAINS the "
                            "console's Awaiting-refresh queue: each row processed here has its "
                            "marker cleared. Needs activation_refresh_schema.sql.")
    parser.add_argument("--dry-run", action="store_true", help="No writes — just prints and dumps results to JSON.")
    parser.add_argument("--exclude-source", type=str, default=None, help="Exclude opportunities with this source value.")
    parser.add_argument("--skip-contact-email", action="store_true",
                        help="Don't run the contact-email lookup (contact_email_common."
                             "resolve_contact_email) alongside the metadata refresh. On by "
                             "default because most rows resolve for free — see that "
                             "module's docstring for why this agent's own Gemini call "
                             "essentially never knows a program's contact address from "
                             "training data alone.")
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
    # GEMINI_API_KEY is the only key this agent needs. The page fetch is a FREE plain-HTTP GET
    # (MARQUEE M1), the metadata extraction is Gemini, and as of the M9 provider swap the
    # contact-email fallback is Gemini too — so a multi-candidate contact page always resolves
    # rather than being left unresolved for want of a second key.

    select = ("id,name,org,url,summary,type,price,location,intl,season,eligibility,"
              "grade_min,grade_max,cost,subject_tags,contact_email,"
              + ACTIVATION_REFRESH_COLUMN)

    # --ids / --pending target queued or just-activated rows (they ignore the is_active
    # filter), which is how the new-angle pipeline enriches a scraped batch from its live
    # pages. Everything else is the classic "refresh the active catalog" path.
    if args.ids:
        id_list = [x.strip() for x in args.ids.split(",") if x.strip()]
        print(f"[OK] Fetching {len(id_list)} row(s) by id (is_active ignored)...")
        items = _get_opportunities(supabase_url,
                                   {"select": select, "id": f"in.({','.join(id_list)})"}, service_key)
        missing = set(id_list) - {o["id"] for o in items}
        if missing:
            print(f"[WARN] {len(missing)} id(s) not found: {sorted(missing)}")
        mode = "ids"
        all_active = items
    elif args.pending:
        # A NULL moderation_status must be spelled out separately: `NOT IN (…)` is NULL in SQL,
        # and `in.(pending_review)` never matches a NULL, so a plain filter would miss every
        # pre-review-column row. Same trap the console's queue filter documents.
        print("[OK] Fetching queued rows (is_active=false, moderation pending/null)...")
        items = _get_opportunities(supabase_url, {
            "select": select, "is_active": "eq.false",
            "or": "(moderation_status.is.null,moderation_status.eq.pending_review)",
        }, service_key)
        mode = "pending"
        all_active = items
    elif args.awaiting_refresh:
        # Drain the console's Awaiting-refresh queue: active rows that carry the marker set at
        # activation. The FILTER (not just the select) references the queue column, so
        # _get_opportunities' select-only fallback cannot rescue a missing migration here —
        # catch that 400 and say which file to run rather than dumping a stack trace.
        print("[OK] Fetching active rows awaiting a metadata refresh (draining the queue)...")
        try:
            items = supabase_get(supabase_url, "opportunities", {
                "select": select, "is_active": "eq.true",
                ACTIVATION_REFRESH_COLUMN: "not.is.null",
            }, service_key)
        except urllib.error.HTTPError as e:
            if e.code == 400:
                print(f"[ERROR] The activation-refresh queue column is missing — run "
                      f"activation_refresh_schema.sql in the Supabase SQL editor first.")
                sys.exit(1)
            raise
        mode = "awaiting"
        all_active = items
        print(f"[OK] {len(items)} row(s) awaiting refresh.")
    else:
        print("[OK] Fetching all active catalog rows from Supabase...")
        params = {"select": select, "is_active": "eq.true"}
        if args.exclude_source:
            params["source"] = f"neq.{args.exclude_source}"
        all_active = _get_opportunities(supabase_url, params, service_key)
        filter_note = f" (excluding source='{args.exclude_source}')" if args.exclude_source else ""
        print(f"[OK] {len(all_active)} active rows{filter_note}.")
        mode = "all"
        items = all_active
        if args.sample:
            mode = "sample"
            items = random.sample(all_active, min(args.sample, len(all_active)))

    # Preview: scope is now fully resolved, so report it and stop before the first
    # (paid) Gemini call. Nothing below this line runs.
    if args.preview:
        emit_preview(len(items), "rows", [o.get("name", "?") for o in items], mode=mode)
        return

    # agent_runs row: insert now, patch with the totals at the end. Dry runs are logged
    # too — they skip DATABASE writes, not API calls, so they cost the same as a live run
    # and must show up in cost totals. The "-dryrun" mode suffix distinguishes them.
    run_mode = mode + ("-dryrun" if args.dry_run else "")
    run_row = supabase_insert_one(supabase_url, "agent_runs", {
        "agent": "metadata_refresher",
        "mode": run_mode,
        "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }, service_key)
    run_id = run_row["id"] if run_row else None

    total_cost = 0.0
    updated = 0
    errors = 0
    fetched = 0                 # rows whose live page we actually read (MARQUEE M1)
    skipped_unfetchable = 0     # page could not be read -> skipped, NEVER written from memory
    unparsed = 0                # page read but model JSON unreadable -> skipped, no stamp
    # total_web_searches / silent_search_count are agent_runs columns the console reads. This
    # agent fetches a KNOWN url rather than doing discovery search of its own, so both stay 0.
    total_searches = 0
    silent_search_count = 0
    dry_run_results = []
    contact_found = 0
    contact_model_calls = 0
    dequeued = 0                # activation-refresh markers cleared (rows drained off the queue)

    for i, opp in enumerate(items):
        # The stdout reconfigure above makes this print encoding-safe on Windows; just
        # truncate the name for a tidy progress line. (The old encode/decode round-trip here
        # did nothing — UTF-8 happily keeps the very characters cp1252 stdout then choked on.)
        opp_name = opp['name'][:60]
        print(f"[{i + 1}/{len(items)}] {opp_name}...", end=" ")
        try:
            info, cost, reason = check_one(opp, gemini_key)
            total_cost += cost

            # MARQUEE M1: a page we could not read is SKIPPED — never written from memory and
            # never stamped, so the row stays due and the next run retries it. A page read but
            # unreadable is likewise skipped (keep whatever curated values are already there).
            if reason == "no-fetch":
                skipped_unfetchable += 1
                print(f"unfetchable page — skipped (no write), ${cost:.4f}")
                continue
            if reason == "unparsed":
                unparsed += 1
                print(f"page read, response unreadable — skipped, ${cost:.4f}")
                continue
            fetched += 1

            # Extract only valid, non-null fields — from what the PAGE stated.
            updates = clean_update_dict(info)

            # contact_email: if the page-read didn't surface one, chain the regex-first lookup
            # (contact_email_common) for any row that doesn't already have one, so a normal
            # pass fills it without needing find_contact_emails.py run separately.
            if not args.skip_contact_email and "contact_email" not in updates and not opp.get("contact_email"):
                email, c_cost, used_model, _ = resolve_contact_email(opp, gemini_key)
                total_cost += c_cost
                contact_model_calls += 1 if used_model else 0
                if email:
                    updates["contact_email"] = email
                    contact_found += 1

            # Real page-derived field count, captured before any bookkeeping column
            # (updated_at, the queue-marker clear) is folded into the PATCH.
            n_fields = len(updates)

            # DRAIN the activation queue: this row has now been run through refresh, so its
            # "awaiting refresh" marker is cleared — whether or not any field changed (a page
            # that states nothing new has still been read). Only rows actually queued are
            # touched, so no extra writes on a normal --all pass; skipped entirely in dry-run
            # (no writes) and when the column is absent (feature off).
            queued = _queue_col_enabled and bool(opp.get(ACTIVATION_REFRESH_COLUMN))

            if not updates:
                if queued and not args.dry_run:
                    supabase_patch(supabase_url, "opportunities", {"id": f"eq.{opp['id']}"},
                                   {ACTIVATION_REFRESH_COLUMN: None}, service_key)
                    dequeued += 1
                print(f"page read, nothing the page changed, ${cost:.4f}"
                      + (" [dequeued]" if queued and not args.dry_run else ""))
                if args.dry_run:
                    dry_run_results.append({
                        "id": opp["id"], "name": opp["name"], "url": opp["url"],
                        "changes": {}, "cost_usd": round(cost, 4),
                    })
                continue

            if args.dry_run:
                dry_run_results.append({
                    "id": opp["id"], "name": opp["name"], "url": opp["url"],
                    "changes": updates, "cost_usd": round(cost, 4),
                })
            else:
                # Apply changes to the database
                updates["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                if queued:
                    updates[ACTIVATION_REFRESH_COLUMN] = None  # drained in the same PATCH
                    dequeued += 1
                supabase_patch(supabase_url, "opportunities", {"id": f"eq.{opp['id']}"}, updates, service_key)

            updated += 1
            print(f"{n_fields} field(s) from page, ${cost:.4f}"
                  + (" [dequeued]" if queued and not args.dry_run else ""))
        except urllib.error.HTTPError as e:
            errors += 1
            print(f"[ERROR] HTTP {e.code}")
        except Exception as e:
            errors += 1
            print(f"[ERROR] {e}")
        # No explicit sleep here. gemini_common._enforce_rate_limit() already holds every
        # call to --min-delay seconds apart (default 5), and it stamps its timestamp at
        # call START, so any per-item sleep shorter than that window is simply absorbed by
        # it and does nothing. This used to be time.sleep(2.0), which was a no-op unless a
        # call returned in under 3s. To slow this agent down, raise --min-delay.

    print(f"\n[SUMMARY] checked: {len(items)}, pages read: {fetched}, updated: {updated}, "
          f"unfetchable(skipped): {skipped_unfetchable}, unreadable(skipped): {unparsed}, "
          f"errors: {errors}, contact emails found: {contact_found} "
          f"({contact_model_calls} model call(s)), activation-queue drained: {dequeued}, "
          f"cost: ${total_cost:.4f}  "
          f"(metadata read from each program's live page — MARQUEE M1)")
    if mode == "sample" and items:
        per_item = total_cost / len(items)
        projected = per_item * len(all_active)
        print(f"[PROJECTED] ~${per_item:.4f}/item -> all {len(all_active)} active rows "
              f"~${projected:.2f} for a full pass.")

    if args.dry_run:
        # Seconds, not just the date: a second run on the same day used to overwrite the
        # first one's file, and a dry run has already paid the API in full by this point.
        stamp = snapshot_stamp()
        refresh_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    f"refresh_opportunities_dry_run_{stamp}.json")
        with open(refresh_path, "w", encoding="utf-8") as f:
            json.dump(dry_run_results, f, indent=2, ensure_ascii=False)
        print(f"[OK] Wrote dry-run refresh snapshot: {refresh_path}")
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
            "notes": f"pages_read={fetched}, unfetchable={skipped_unfetchable}, "
                     f"unparsed={unparsed}, contact_emails_found={contact_found}, "
                     f"contact_model_calls={contact_model_calls}, "
                     f"activation_queue_drained={dequeued}",
        }, service_key)
        print(f"[OK] Logged agent_runs id={run_id}.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Opportunity refresh agent: for each active opportunity in the Supabase `opportunities`
catalog, searches for current metadata and updates all fields EXCEPT those managed by the
deadline and review agents. Runs monthly to keep opportunity info current (eligibility,
pricing, program structure, location format, etc.) without interfering with deadline/review
tracking.

Fields NEVER touched by this agent (reserved for other agents):
  - status, important_dates, was_estimated, important_date_note, dates_last_checked_at (deadline agent)
  - review_status, review_summary, review_sources, last_reviewed_at (review agent)
  - is_active, source (system fields)

Fields this agent CAN update:
  - name, org, summary, url, type, price, state, location, intl, season, category,
    eligibility, grade_min, grade_max, cost, subject_tags

Run roughly 1x/month on all active opportunities. Uses gemini-3.5-flash-lite for cost
efficiency (~$0.075/MTok input, $0.30/MTok output). With 1200+ rows, expect ~$10-15/month
cost and 20-40 minutes wall time (includes Gemini rate limiting).

SETUP:
    .env needs SUPABASE_URL, SUPABASE_SERVICE_KEY, GEMINI_API_KEY.
    (Does NOT need agent_runs table — this is primarily for cost tracking, not operational
    recovery — but if you want to log runs, add the same agent_runs schema from check_deadlines.py)

USAGE:
    python refresh_opportunities.py --sample 10    # random 10-row sample, prints projected cost
    python refresh_opportunities.py --all          # every active row
    python refresh_opportunities.py --dry-run      # no writes, just prints results to JSON
"""
import argparse
import datetime
import json
import os
import random
import sys
import time
import urllib.error

from gemini_common import call_gemini, extract_json, estimate_cost
from supabase_common import load_dotenv, supabase_get, supabase_patch

VALID_TYPES = {'Program', 'Internship', 'Competition', 'Research', 'Volunteer', 'Journal', 'Conference'}
VALID_SUBJECTS = ['Mixed', 'STEM', 'Medicine', 'Humanities', 'Art', 'Business', 'Engineering',
                  'Computer Science', 'Mathematics', 'Biology', 'Physics', 'Astronomy',
                  'Chemistry', 'Leadership', 'Law', 'Logic', 'Education']
VALID_PRICE = {'Free', 'Paid'}
VALID_LOCATION = {'In-Person', 'Remote', 'In-Person and Remote'}
VALID_INTL = {'International Students', 'Domestic Students'}
VALID_SEASON = {'Summer', 'Year-Long', 'Spring', 'Fall', 'Winter'}


def build_system(opp):
    today = datetime.date.today().isoformat()
    return f"""You research current metadata for an extracurricular opportunity (program, \
internship, competition, or research position) listed in a high school opportunity catalog. \
Today's date is {today}.

YOU MUST use web_search and web_fetch to verify CURRENT information — do not rely on training \
data alone. Search thoroughly for the program's official website, latest announcements, and any \
recent program updates.

GOAL: extract current, accurate metadata about the opportunity — not deadline/status tracking \
(that's handled separately), but core program info that students need to decide if it's relevant.

SEARCH STEPS:
1. Fetch the program's main URL directly to see current information.
2. Search for recent announcements, 2025-2026 program details, and any changes.
3. Look for eligibility info, pricing/cost details, format (in-person/remote), and location.
4. Search for program type/category context (is it truly a {list(VALID_TYPES)[0]}? etc.).

EXTRACTION: return a JSON object with ONLY the fields you found current information for.
Omit fields you couldn't verify or where current info is unavailable.

Schema: {{"name": "string or null", "org": "string or null", "summary": "one paragraph \
or null", "url": "string or null", "type": "one of {', '.join(sorted(VALID_TYPES))} or null", \
"price": "Free or Paid or null", "location": "In-Person, Remote, or In-Person and Remote or null", \
"intl": "International Students or Domestic Students or null", "season": "Summer, Year-Long, \
Spring, Fall, or Winter or null", "eligibility": "concise description or null", \
"grade_min": "integer (9-12) or null", "grade_max": "integer (9-12) or null", \
"cost": "string (e.g. '$2000-3500') or null", "subject_tags": "[array of tags from the list: \
{', '.join(VALID_SUBJECTS)}] or null"}}.

Return ONLY the raw JSON object, no markdown, no preamble. Keep response under 800 tokens. \
Do NOT include deadline/status fields — those are handled separately. For fields you cannot \
verify, use null rather than guessing."""


def check_one(opp, api_key):
    system = build_system(opp)
    user_content = (f"Opportunity: {opp['name']} ({opp.get('org') or 'unknown org'})\n"
                    f"URL: {opp['url']}\nCurrent summary: {opp.get('summary') or 'none'}\n\n"
                    f"Search for current information about this opportunity and extract per the schema.")
    text, usage = call_gemini(system, user_content, api_key, use_web_search=True, max_tokens=1200,
                              model="gemini-3.5-flash-lite")
    info = extract_json(text)
    searches = (usage.get("server_tool_use") or {}).get("web_search_requests", 0)
    return info, estimate_cost(usage), searches


def clean_update_dict(info):
    """Extract and validate fields from the API response, dropping nulls and invalid values."""
    update = {}

    # String fields
    for field in ["name", "org", "summary", "url", "eligibility", "cost"]:
        val = info.get(field)
        if isinstance(val, str) and val.strip():
            update[field] = val.strip()

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
    parser.add_argument("--dry-run", action="store_true", help="No writes — just prints and dumps results to JSON.")
    parser.add_argument("--exclude-source", type=str, default=None, help="Exclude opportunities with this source value.")
    args = parser.parse_args()

    load_dotenv()
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    service_key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    if not supabase_url or not service_key or not gemini_key:
        print("[ERROR] SUPABASE_URL / SUPABASE_SERVICE_KEY / GEMINI_API_KEY not set in .env.")
        sys.exit(1)

    print("[OK] Fetching all active catalog rows from Supabase...")
    params = {
        "select": "id,name,org,url,summary,type,price,location,intl,season,eligibility,grade_min,grade_max,cost,subject_tags",
        "is_active": "eq.true",
    }
    if args.exclude_source:
        params["source"] = f"neq.{args.exclude_source}"
    all_active = supabase_get(supabase_url, "opportunities", params, service_key)
    filter_note = f" (excluding source='{args.exclude_source}')" if args.exclude_source else ""
    print(f"[OK] {len(all_active)} active rows{filter_note}.")

    mode = "all"
    items = all_active
    if args.sample:
        mode = "sample"
        items = random.sample(all_active, min(args.sample, len(all_active)))

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

            if searches == 0:
                silent_search_count += 1

            # Extract only valid, non-null fields from the response
            updates = clean_update_dict(info)

            if not updates:
                print(f"{searches} search(es), no changes, ${cost:.4f}")
                if args.dry_run:
                    dry_run_results.append({
                        "id": opp["id"],
                        "name": opp["name"],
                        "url": opp["url"],
                        "changes": {},
                        "web_searches": searches,
                        "cost_usd": round(cost, 4),
                    })
                continue

            changed = True
            if args.dry_run:
                dry_run_results.append({
                    "id": opp["id"],
                    "name": opp["name"],
                    "url": opp["url"],
                    "changes": updates,
                    "web_searches": searches,
                    "cost_usd": round(cost, 4),
                })
            else:
                # Apply changes to the database
                updates["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                supabase_patch(supabase_url, "opportunities", {"id": f"eq.{opp['id']}"}, updates, service_key)

            updated += 1
            silent = " [SILENT: no search invoked]" if searches == 0 else ""
            print(f"{len(updates)} field(s) changed, {searches} search(es){silent}, ${cost:.4f}")
        except urllib.error.HTTPError as e:
            errors += 1
            print(f"[ERROR] HTTP {e.code}")
        except Exception as e:
            errors += 1
            print(f"[ERROR] {e}")
        time.sleep(1.0)  # Rate limiting

    print(f"\n[SUMMARY] checked: {len(items)}, updated: {updated}, errors: {errors}, "
          f"silent (no-search) checks: {silent_search_count}/{len(items)}, cost: ${total_cost:.4f}")
    if mode == "sample" and items:
        per_item = total_cost / len(items)
        projected = per_item * len(all_active)
        print(f"[PROJECTED] ~${per_item:.4f}/item -> all {len(all_active)} active rows "
              f"~${projected:.2f} for a full pass.")

    if args.dry_run:
        run_date = datetime.date.today().strftime("%Y%m%d")
        refresh_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    f"refresh_opportunities_dry_run_{run_date}.json")
        with open(refresh_path, "w", encoding="utf-8") as f:
            json.dump(dry_run_results, f, indent=2, ensure_ascii=False)
        print(f"[OK] Wrote dry-run refresh snapshot: {refresh_path}")
        print("[DRY RUN] No writes performed.")


if __name__ == "__main__":
    main()

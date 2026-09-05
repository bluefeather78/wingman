#!/usr/bin/env python3
"""One-off backfill: generates `subject_tags` (a short freeform tag array) for every
opportunities row that's missing it.

Why this exists: `subject_tags` was only ever populated for opportunity-finder seed rows
and freshly-scraped candidates (see migrate_to_supabase.py / agents/scrape_opportunities.py) — the
original 1141 wingman-seed rows never got it, leaving ~86% of the live catalog without it
as of 2026-08-20. script.js's preFilter() is being switched from the older single-value
`subject` field to `subject_tags` for its relevance-boost matching, so this script closes
that gap before `subject` is dropped from the table.

Uses gemini-3.5-flash-lite (same model server.py's MESSAGES_MODEL pins for interactive
/api/messages calls) rather than the heavier gemini-3.6-flash default in gemini_common —
this is plain structured extraction from text already in the row (name/org/summary), no
web search needed, so the cheaper/faster model is a better fit.

SETUP:
    .env needs SUPABASE_URL, SUPABASE_SERVICE_KEY, GEMINI_API_KEY.

USAGE:
    python backfill_subject_tags.py --dry-run          # print first batch's parsed result, no writes
    python backfill_subject_tags.py --sample 25         # backfill a random 25-row sample, prints cost
    python backfill_subject_tags.py                     # backfill every row missing subject_tags
"""
import argparse
import datetime
import random
import sys
import urllib.error

import os
# This script lives under scripts/one-off/ but imports the repo-root shared libraries below by bare name
# (gemini_common, supabase_common), the way every root script does.
# Running it as `python scripts/one-off/backfill_subject_tags.py` puts its OWN directory on sys.path, not the
# repo root, so the root has to be added explicitly.
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from wingman.gemini_common import call_gemini, extract_json, estimate_cost
from wingman.supabase_common import load_dotenv, supabase_get, supabase_patch, supabase_insert_one
import os

MODEL = "gemini-3.5-flash-lite"
BATCH_SIZE = 25
MAX_TOKENS = 2000

VALID_SUBJECTS = ['Mixed', 'STEM', 'Medicine', 'Humanities', 'Art', 'Business', 'Engineering',
                   'Computer Science', 'Mathematics', 'Biology', 'Physics', 'Astronomy',
                   'Chemistry', 'Leadership', 'Law', 'Logic', 'Education']

SYSTEM = f"""You generate short subject tags for extracurricular opportunities (programs, \
internships, competitions, research, journals, conferences) in a catalog used to match high \
school students to opportunities that fit their interests.

For each opportunity given (id, name, org, summary, and its existing single-category \
"subject" field), produce a "subject_tags" array of 3-5 short tags (1-3 words each) \
describing what it's actually about. Mix one or two broader category tags — reuse the \
existing "subject" value as a tag when it's a good fit, otherwise pick from this list: \
{', '.join(VALID_SUBJECTS)} — with 2-3 more specific tags naming the actual field, skill, or \
activity (e.g. "Robotics", "Marketing", "Astrophysics", "Olympiad", "Creative Writing", \
"Public Health"). Base tags only on what the name/org/summary actually describe — never \
invent detail that isn't implied by the given text.

Respond with ONLY a raw JSON array, no markdown, no preamble, no text after the array, with \
exactly one entry per opportunity given, in the same order, matching: \
[{{"id":"...","subject_tags":["tag1","tag2","tag3"]}}]"""


def build_user_content(rows):
    compact = [{"id": r["id"], "name": r["name"], "org": r.get("org"),
                "summary": (r.get("summary") or "")[:300], "subject": r.get("subject")}
               for r in rows]
    import json as _json
    return _json.dumps(compact)


def tag_batch(rows, api_key):
    user_content = build_user_content(rows)
    text, usage = call_gemini(SYSTEM, user_content, api_key, use_web_search=False,
                               max_tokens=MAX_TOKENS, model=MODEL)
    arr = extract_json(text)
    if not isinstance(arr, list):
        raise ValueError("Response was not a JSON array")
    return arr, estimate_cost(usage)


def clean_tags(item, valid_ids):
    rid = item.get("id")
    if rid not in valid_ids:
        return None
    tags = item.get("subject_tags")
    if not isinstance(tags, list):
        return None
    tags = [str(t).strip() for t in tags if isinstance(t, (str,)) and str(t).strip()]
    tags = tags[:5]
    if not tags:
        return None
    return rid, tags


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", help="Process one batch, print parsed result, write nothing.")
    group.add_argument("--sample", type=int, help="Backfill a random N-row sample instead of the full gap.")
    args = parser.parse_args()

    load_dotenv()
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    service_key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    if not supabase_url or not service_key or not gemini_key:
        print("[ERROR] SUPABASE_URL / SUPABASE_SERVICE_KEY / GEMINI_API_KEY not set in .env.")
        sys.exit(1)

    print("[OK] Fetching rows missing subject_tags...")
    rows = supabase_get(supabase_url, "opportunities", {
        "select": "id,name,org,summary,subject",
        "subject_tags": "is.null",
    }, service_key)
    print(f"[OK] {len(rows)} rows missing subject_tags.")
    if not rows:
        return

    mode = "all"
    if args.sample:
        mode = "sample"
        rows = random.sample(rows, min(args.sample, len(rows)))
    elif args.dry_run:
        mode = "dry-run"
        rows = rows[:BATCH_SIZE]

    run_id = None
    if mode != "dry-run":
        run_row = supabase_insert_one(supabase_url, "agent_runs", {
            "agent": "subject_tags_backfill",
            "mode": mode,
            "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }, service_key)
        run_id = run_row["id"] if run_row else None

    total_cost = 0.0
    updated = 0
    errors = 0
    batches = [rows[i:i + BATCH_SIZE] for i in range(0, len(rows), BATCH_SIZE)]

    for bi, batch in enumerate(batches):
        print(f"[batch {bi + 1}/{len(batches)}] tagging {len(batch)} rows...", end=" ")
        try:
            arr, cost = tag_batch(batch, gemini_key)
            total_cost += cost
            valid_ids = {r["id"] for r in batch}
            parsed = [clean_tags(item, valid_ids) for item in arr]
            parsed = [p for p in parsed if p]
            print(f"{len(parsed)}/{len(batch)} tagged, ${cost:.4f}")

            if args.dry_run:
                for rid, tags in parsed:
                    print(f"  {rid}: {tags}")
                break

            for rid, tags in parsed:
                supabase_patch(supabase_url, "opportunities", {"id": f"eq.{rid}"},
                                {"subject_tags": tags, "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}, service_key)
                updated += 1
        except urllib.error.HTTPError as e:
            errors += 1
            print(f"[ERROR] HTTP {e.code}")
        except Exception as e:
            errors += 1
            print(f"[ERROR] {e}")

    print(f"\n[SUMMARY] rows: {len(rows)}, updated: {updated}, errors: {errors}, cost: ${total_cost:.4f}")

    if run_id is not None:
        supabase_patch(supabase_url, "agent_runs", {"id": f"eq.{run_id}"}, {
            "finished_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "items_processed": len(rows),
            "items_updated": updated,
            "errors": errors,
            "cost_usd": round(total_cost, 4),
        }, service_key)
        print(f"[OK] Logged agent_runs id={run_id}.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Repair the junk-named survivors from the 2026-08-26 pair resolutions. FREE (HTTP only).

Round 2 collapsed 30 identical-URL pairs to one survivor each, and the operator flagged a side
effect: ~5 survivors kept a junk name ('Columbia', 'Michigan State University', 'Pre-College')
because the fuller, better name was on the LOSER. This walks pair_resolution_20260826.json and,
for each survivor, tries the losers' names as merge candidates against the survivor's own page —
renaming the survivor only when the loser's name is proven by the survivor's page title and the
survivor's own name is not (best-copy-wins, tenet 7). Every rename is backed by page-title
evidence, logged, and appended to the survivor's quality_flags so it is hand-reversible.

    python repair_survivor_names.py              # preview (no writes)
    python repair_survivor_names.py --commit      # apply
"""
import argparse
import datetime
import json
import os

import sys
# This script lives under scripts/one-off/ but imports the repo-root shared libraries below by bare name
# (scrape_opportunities, supabase_common, url_repair), the way every root script does.
# Running it as `python scripts/one-off/repair_survivor_names.py` puts its OWN directory on sys.path, not the
# repo root, so the root has to be added explicitly.
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from agents import scrape_opportunities as so
from wingman import url_repair
from wingman.supabase_common import load_dotenv, supabase_get, supabase_patch

# Anchored at the REPO ROOT, not at this file's directory: the 2026-09-04 tidy-up
# moved this script down a level and `dirname(__file__)` no longer means the root.
REPO = ROOT
PAIRS = os.path.join(REPO, "tests", "fixtures", "pair_resolution_20260826.json")
_SELECT = ("id,name,org,summary,eligibility,grade_min,grade_max,"
           "subject_tags,contact_email,url,quality_flags")


def _fetch_row(supabase_url, key, opp_id):
    rows = supabase_get(supabase_url, "opportunities",
                        {"select": _SELECT, "id": f"eq.{opp_id}", "limit": "1"}, key)
    return (rows or [None])[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true", help="Apply the renames (default: preview).")
    args = ap.parse_args()

    load_dotenv()
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_ANON_KEY")
    if not supabase_url or not key:
        print("[ERROR] SUPABASE_URL and a key must be set in .env.")
        raise SystemExit(1)

    with open(PAIRS, encoding="utf-8") as f:
        pairs = json.load(f)["pairs"]

    day = datetime.date.today().strftime("%Y-%m-%d")
    proposed = 0
    for pair in pairs:
        survivor_id = pair.get("survivor")
        if not survivor_id or not pair.get("losers"):
            continue
        survivor = _fetch_row(supabase_url, key, survivor_id)
        if not survivor:
            print(f"[skip] survivor {survivor_id} not found")
            continue
        page, _final = url_repair._fetch(survivor.get("url") or "")
        title = url_repair.page_title(page or "")
        # Try each loser's fields as a merge candidate; take the first that improves the name.
        chosen = None
        for loser_id in pair["losers"]:
            loser = _fetch_row(supabase_url, key, loser_id)
            if not loser:
                continue
            patch, notes = so.merge_row(loser, survivor, title)
            if "name" in patch:
                chosen = (loser_id, patch, notes)
                break
        if not chosen:
            continue
        loser_id, patch, notes = chosen
        proposed += 1
        print(f"[RENAME] {survivor_id}: '{survivor.get('name')}' -> '{patch['name']}' "
              f"(from loser {loser_id}; title: {title[:60]!r})")
        if not args.commit:
            continue
        patch["quality_flags"] = list(survivor.get("quality_flags") or []) + \
            [f"merged {day}: {n}" for n in notes]
        patch["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        try:
            supabase_patch(supabase_url, "opportunities", {"id": f"eq.{survivor_id}"}, patch, key)
            print(f"          committed ({', '.join(notes)})")
        except Exception as e:
            print(f"          [WARN] PATCH failed: {str(e)[:160]}")

    verb = "renamed" if args.commit else "would rename"
    print(f"\n[RESULT] {verb} {proposed} survivor(s) with page-title evidence."
          + ("" if args.commit else " Re-run with --commit to apply."))


if __name__ == "__main__":
    main()

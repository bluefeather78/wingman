#!/usr/bin/env python3
"""One-off migration: loads wingman's existing opportunities.json (1142 rows)
plus the curated seed data from the sibling `opportunity finder/` project (78
rows) into the Supabase `opportunities` table.

Not part of the regular dev loop — run manually once (or again, safely; it
upserts on `id` so re-running just refreshes existing rows and skips/merges
duplicates rather than doubling the table).

SETUP:
    Add to .env (repo root):
        SUPABASE_URL=https://<project-ref>.supabase.co
        SUPABASE_SERVICE_KEY=<service_role key, from Project Settings -> API>
    Create the `opportunities` table first by running the SQL in the plan
    (CLAUDE.md / the migration plan doc) in the Supabase SQL editor.

USAGE:
    python migrate_to_supabase.py            # migrate + insert
    python migrate_to_supabase.py --dry-run  # print what would be inserted, no writes
"""
import json
import os
import sys
import urllib.error
import urllib.request

WINGMAN_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "opportunities.json")
FINDER_JSON = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "opportunity finder", "opportunities.json"
)

VALID_SUBJECTS = ['Mixed', 'STEM', 'Medicine', 'Humanities', 'Art', 'Business', 'Engineering',
                   'Computer Science', 'Mathematics', 'Biology', 'Physics', 'Astronomy',
                   'Chemistry', 'Leadership', 'Law', 'Logic', 'Education']

WINGMAN_LOCATIONS = {'Remote', 'In-Person', 'In-Person and Remote'}

# finder `category` -> wingman `type`. RESEARCH keeps its own "Research" type
# (wingman already uses that value for 23 existing entries) rather than
# collapsing into the generic "Program" bucket.
CATEGORY_TO_TYPE = {
    'SUMMER_PROGRAM': 'Program',
    'OTHER': 'Program',
    'RESEARCH': 'Research',
    'INTERNSHIP': 'Internship',
    'COMPETITION': 'Competition',
    'VOLUNTEER': 'Volunteer',
    'JOURNAL': 'Journal',
    'CONFERENCE': 'Conference',
}

CORE_FIELDS = ('id', 'name', 'org', 'summary', 'url', 'subject', 'type',
               'price', 'state', 'location', 'intl', 'season')
BATCH_SIZE = 500


def load_dotenv(path=".env"):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            if key and not os.environ.get(key):
                os.environ[key] = value


def normalize_url(url):
    return (url or "").strip().rstrip("/").lower()


def next_id_generator(existing_ids):
    nums = [int(i[2:]) for i in existing_ids if i.startswith("ec") and i[2:].isdigit()]
    n = (max(nums) if nums else 18220) + 1
    while True:
        yield f"ec{n}"
        n += 1


def load_wingman_rows():
    with open(WINGMAN_JSON, "r", encoding="utf-8") as f:
        rows = json.load(f)
    out = []
    for r in rows:
        row = {k: r.get(k) for k in CORE_FIELDS}
        row.update({
            "category": None, "description": None, "eligibility": None,
            "grade_min": None, "grade_max": None, "cost": None, "deadline": None,
            "subject_tags": None, "is_active": True, "source": "wingman-seed",
        })
        out.append(row)
    return out


def map_finder_row(opp, mint_id):
    title = (opp.get("title") or "").strip()
    url = (opp.get("url") or "").strip()
    category = opp.get("category") or "OTHER"
    opp_type = CATEGORY_TO_TYPE.get(category, "Program")

    cost = opp.get("cost") or ""
    price = "Free" if "free" in cost.lower() else ("Paid" if cost else None)

    location = opp.get("location") or ""
    location = location if location in WINGMAN_LOCATIONS else None

    tags = opp.get("subject_tags") or []
    if not isinstance(tags, list):
        tags = [tags] if tags else []
    subject = next((t for t in tags if t in VALID_SUBJECTS), "Mixed")

    return {
        "id": mint_id,
        "name": title,
        "org": None,
        "summary": opp.get("description"),
        "url": url,
        "subject": subject,
        "type": opp_type,
        "price": price,
        "state": None,
        "location": location,
        "intl": None,
        "season": None,
        "category": category,
        "description": opp.get("description"),
        "eligibility": opp.get("eligibility"),
        "grade_min": opp.get("grade_min"),
        "grade_max": opp.get("grade_max"),
        "cost": cost or None,
        "deadline": opp.get("deadline"),
        "subject_tags": tags or None,
        "is_active": bool(opp.get("is_active", True)),
        "source": "opportunity-finder",
    }


def build_rows():
    wingman_rows = load_wingman_rows()
    seen_urls = {normalize_url(r["url"]) for r in wingman_rows}
    existing_ids = {r["id"] for r in wingman_rows}
    mint_id = next_id_generator(existing_ids)

    if not os.path.exists(FINDER_JSON):
        print(f"[!] Finder seed file not found at '{FINDER_JSON}' — skipping.")
        finder_rows, skipped = [], 0
    else:
        with open(FINDER_JSON, "r", encoding="utf-8") as f:
            finder_raw = json.load(f)
        finder_rows, skipped = [], 0
        for opp in finder_raw:
            norm = normalize_url(opp.get("url"))
            if norm and norm in seen_urls:
                skipped += 1
                continue
            row = map_finder_row(opp, next(mint_id))
            finder_rows.append(row)
            if norm:
                seen_urls.add(norm)

    print(f"[OK] {len(wingman_rows)} wingman-seed rows, {len(finder_rows)} new opportunity-finder rows, {skipped} duplicates skipped.")
    return wingman_rows + finder_rows


def post_batch(supabase_url, service_key, batch):
    req = urllib.request.Request(
        f"{supabase_url}/rest/v1/opportunities?on_conflict=id",
        data=json.dumps(batch).encode("utf-8"),
        method="POST",
        headers={
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal,resolution=merge-duplicates",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status


def main():
    dry_run = "--dry-run" in sys.argv
    load_dotenv()
    rows = build_rows()

    if dry_run:
        print(f"[DRY RUN] Would upsert {len(rows)} rows. Sample:")
        print(json.dumps(rows[-1], indent=2, ensure_ascii=False))
        return

    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    service_key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not supabase_url or not service_key:
        print("[ERROR] SUPABASE_URL / SUPABASE_SERVICE_KEY not set in .env.")
        sys.exit(1)

    total = len(rows)
    for i in range(0, total, BATCH_SIZE):
        batch = rows[i:i + BATCH_SIZE]
        try:
            status = post_batch(supabase_url, service_key, batch)
            print(f"[OK] Upserted rows {i + 1}-{i + len(batch)} of {total} (status {status})")
        except urllib.error.HTTPError as e:
            print(f"[ERROR] Batch {i + 1}-{i + len(batch)} failed: {e.code} {e.read().decode(errors='replace')}")
            sys.exit(1)

    print(f"\n[DONE] Migrated {total} opportunities into Supabase.")


if __name__ == "__main__":
    main()

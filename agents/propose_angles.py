#!/usr/bin/env python3
"""Propose new scraper angles from catalog coverage gaps. Analysis is FREE.

Phase 4 of the scraper v2 plan. Angles are hand-written today; this finds where the catalog is
THIN — under-served types, seasons and subjects — and proposes angles to fill those gaps. Every
proposal lands as a DISABLED seed in scraper_seeds for the operator to review and enable; nothing
runs on its own, and a disabled seed costs nothing until enabled.

The coverage-gap analysis is pure and free. `--commit` writes the disabled seeds (a free DB
write, no model call). An optional siblings-of-winners model call (--siblings) is PAID and gated.

    python -m agents.propose_angles --preview          # FREE: print proposed angles
    python -m agents.propose_angles --commit            # FREE: write them as DISABLED seeds
"""
import argparse
import os
from collections import Counter

# The type vocabulary the finder understands (kept in sync with OPPORTUNITY_TYPES elsewhere).
CATALOG_TYPES = ("Program", "Internship", "Competition", "Research", "Conference",
                 "Journal", "Volunteer")

# Broad subject AREAS to check coverage against. Deliberately NOT the catalog's own free-form
# subject_tags: those are hyper-specific ("Pastry Arts", "Typography", "Adobe Creative Cloud"),
# so nearly every one appears < a few times and would mint hundreds of noise angles. A curated
# list keeps proposals bounded and meaningful — a thin AREA is a real gap; a rare leaf tag is not.
CORE_SUBJECTS = (
    "Biology", "Chemistry", "Physics", "Mathematics", "Computer Science", "Engineering",
    "Robotics", "Medicine", "Public Health", "Neuroscience", "Environmental Science",
    "Astronomy", "Economics", "Business", "Entrepreneurship", "Law", "Political Science",
    "Psychology", "Sociology", "History", "Philosophy", "Creative Writing", "Journalism",
    "Visual Arts", "Music", "Theater", "Film", "Design", "Architecture", "Debate",
    "Linguistics", "Data Science", "Artificial Intelligence", "Cybersecurity",
    "Marine Science", "Agriculture", "Aerospace", "Foreign Languages")


def _subject_coverage(active, subject):
    """How many active rows touch this broad subject area (by subject_tag, name, or summary)."""
    needle = subject.lower()
    n = 0
    for r in active:
        hay = (" ".join(r.get("subject_tags") or []) + " " + (r.get("name") or "")
               + " " + (r.get("summary") or "")).lower()
        if needle in hay:
            n += 1
    return n


def analyze_gaps(rows, mode="national", min_per_cell=4):
    """Angle proposals for thin catalog cells. Pure, free (no model call).

    Looks at ACTIVE rows only — an inactive row is not coverage a student can see. Proposes for:
    under-served TYPES, under-served SEASONS within a type, and under-served SUBJECTS. Returns a
    de-duplicated, ordered list of angle strings.
    """
    active = [r for r in rows if r.get("is_active")]
    by_type = Counter(r.get("type") for r in active if r.get("type"))
    by_type_season = Counter((r.get("type"), r.get("season"))
                             for r in active if r.get("type") and r.get("season"))

    proposals, seen = [], set()

    def add(angle):
        if angle not in seen:
            seen.add(angle)
            proposals.append(angle)

    scope = "national" if mode == "national" else "Seattle-area"
    for t in CATALOG_TYPES:
        if by_type.get(t, 0) < min_per_cell:
            add(f"{scope} {t.lower()} opportunities for high school students (grades 9-12)")
    for (t, season), n in by_type_season.items():
        if n < min_per_cell and t in CATALOG_TYPES:
            add(f"{scope} {season} {t.lower()} programs for high schoolers")
    # Broad subject AREAS the catalog barely covers — bounded to a curated list, ranked thinnest
    # first, so the operator sees the biggest gaps at the top.
    for subject in sorted(CORE_SUBJECTS, key=lambda s: _subject_coverage(active, s)):
        if _subject_coverage(active, subject) < min_per_cell:
            add(f"{scope} high school {subject} programs, competitions and research opportunities")
    return proposals


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="national", choices=["national", "seattle"])
    ap.add_argument("--min-per-cell", type=int, default=4)
    ap.add_argument("--preview", action="store_true", help="FREE: print proposals, write nothing.")
    ap.add_argument("--commit", action="store_true", help="FREE: write proposals as DISABLED seeds.")
    args = ap.parse_args()

    from wingman.supabase_common import load_dotenv, supabase_get, supabase_post
    load_dotenv()
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    service_key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_ANON_KEY")
    if not supabase_url or not service_key:
        print("[ERROR] SUPABASE_URL and a key must be set in .env.")
        raise SystemExit(1)

    rows = supabase_get(supabase_url, "opportunities",
                        {"select": "type,season,subject_tags,is_active"}, service_key) or []
    proposals = analyze_gaps(rows, mode=args.mode, min_per_cell=args.min_per_cell)
    print(f"[OK] {len(proposals)} angle proposal(s) from coverage gaps (mode={args.mode}):")
    for a in proposals:
        print(f"    {a}")

    if not args.commit or args.preview:
        print("\n[PREVIEW] Nothing written. Re-run with --commit to add these as DISABLED seeds.")
        return

    # Existing angles, so we never propose a duplicate of one already in the table.
    existing = {s.get("angle", "").strip().lower()
                for s in (supabase_get(supabase_url, "scraper_seeds",
                                       {"select": "angle", "mode": f"eq.{args.mode}"},
                                       service_key) or [])}
    written = 0
    for angle in proposals:
        if angle.strip().lower() in existing:
            continue
        try:
            supabase_post(supabase_url, "scraper_seeds",
                          [{"mode": args.mode, "category": "unused", "angle": angle,
                            "is_enabled": False}], service_key)
            written += 1
        except Exception as e:
            print(f"  [WARN] could not write '{angle[:40]}...': {str(e)[:100]}")
    print(f"[SUMMARY] wrote {written} DISABLED seed(s). Review and enable in the console.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Phase 7 eval for the eligibility reasoning (docs/plans/OPPORTUNITY_MATCHING_PLAN.md).

Grades the curation call's eligibility verdicts against a labeled set, in BOTH error
directions — because they are not symmetric and the worse one has no code guard:

  * OVER-EXCLUSION  — an open program marked ineligible (a false exclusion). The
    quote-verification guard catches most of these; this metric also surfaces the accepted
    marketing-hyperbole residual (a `summary` "exclusively designed to challenge top
    students" that verifies as a quote but is not really a gate).
  * UNDER-EXCLUSION  — a hard-scope program (girls-only, citizenship-gated) shown to an
    ineligible student. There is NO code guard for this direction (no quote to verify when
    the model FAILS to exclude), it is the worse product harm, and this eval is the only
    thing that measures it. This is the whole reason Phase 7 exists.

The labeled cases below are CRAFTED (synthetic row text + a student context + a gold
verdict), not pulled from the catalog — so the eval runs with just a model key, no DB, and
each case is a controlled test of one distinction. Add real-catalog cases (with their row
ids) as they are labeled; the scoring is the same.

The SCORING is pure and unit-tested. The RUN path (calling the live curation model per case)
needs a GEMINI_API_KEY — `--run`. `--list` is free.
"""
import argparse
import json
import os
import sys
# This script lives under eval/ but imports repo-root shared libraries by bare name from inside its functions
# (gemini_common, supabase_common), the way every root script does.
# Running it as `python eval/matching_eval.py` puts its OWN directory on sys.path, not the
# repo root, so the root has to be added explicitly.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


# Each case: a single candidate + a student context + the gold eligibility answer.
# dimension is for the per-dimension breakdown; note explains the distinction being tested.
SEED_ELIGIBILITY_CASES = [
    # --- hard demographic: same "for women" framing, opposite verdicts by student ---
    {"case_id": "gwc-male", "dimension": "demographic",
     "name": "Girls Who Code Summer Immersion", "org": "Girls Who Code",
     "summary": "A free virtual summer program teaching coding fundamentals.",
     "eligibility": "Open to female, non-binary, and gender non-conforming high school students. Beginners welcome.",
     "student": {"grade": 10, "gender": "male", "location": {"state": "WA"}},
     "gold_eligible": False,
     "note": "hard-by-scope: the named group IS who may apply; a male student is ineligible (no 'only' needed)."},
    {"case_id": "gwc-female", "dimension": "demographic",
     "name": "Girls Who Code Summer Immersion", "org": "Girls Who Code",
     "summary": "A free virtual summer program teaching coding fundamentals.",
     "eligibility": "Open to female, non-binary, and gender non-conforming high school students. Beginners welcome.",
     "student": {"grade": 10, "gender": "female", "location": {"state": "WA"}},
     "gold_eligible": True,
     "note": "same row, eligible student."},
    {"case_id": "nextgen-male", "dimension": "demographic",
     "name": "Next Gen Women in Physics", "org": "Stanford",
     "summary": "A physics enrichment weekend.",
     "eligibility": "Students of any gender are welcome; we especially encourage young women to apply.",
     "student": {"grade": 11, "gender": "male", "location": {"state": "CA"}},
     "gold_eligible": True,
     "note": "SOFT: 'any gender welcome' — encouragement, not a gate. Must NOT be excluded."},
    # --- residency vs. location-of-program ---
    {"case_id": "bps-seattle", "dimension": "residency",
     "name": "City Science Fellowship", "org": "Boston Public Schools",
     "summary": "A paid research placement.",
     "eligibility": "Open only to Boston Public Schools students.",
     "student": {"grade": 11, "location": {"state": "WA", "city": "Seattle"}},
     "gold_eligible": False,
     "note": "hard residency gate; a Seattle student is ineligible."},
    {"case_id": "hosted-boston-seattle", "dimension": "residency",
     "name": "Northeastern Summer Research", "org": "Northeastern University",
     "summary": "Hosted at Northeastern University in Boston; open to high schoolers nationwide.",
     "eligibility": "High school students who have completed grade 9.",
     "student": {"grade": 11, "location": {"state": "WA", "city": "Seattle"}},
     "gold_eligible": True,
     "note": "'Hosted in Boston' says where it RUNS, not who may apply — NOT a residency gate."},
    # --- rising grader ---
    {"case_id": "rising-10-grade9", "dimension": "grade",
     "name": "Rising Sophomore Lab", "org": "State University",
     "summary": "An intro lab experience.",
     "eligibility": "For rising 10th graders.",
     "student": {"grade": 9, "location": {"state": "TX"}},
     "gold_eligible": True,
     "note": "a rising 10th grader IS a current 9th grader — eligible; a numeric grade_min=10 would wrongly cut."},
    # --- entry window / too late (under-exclusion watch: the real Transition School miss) ---
    {"case_id": "past-entry-window-grade9", "dimension": "entry_window",
     "name": "Transition School", "org": "Robinson Center at the University of Washington",
     "summary": "A one-year college-preparatory program for advanced learners who apply during their "
                "8th grade year and participate during what would otherwise be their 9th grade year.",
     "eligibility": "Students apply during their 8th grade year and participate during their 9th grade year.",
     "student": {"grade": 9, "location": {"state": "WA"}},
     "gold_eligible": False,
     "note": "TOO LATE: the application happens in 8th grade, so a current 9th grader has passed the "
             "entry window even though the numeric participation grade (9) matches. The worst "
             "direction — no code guard; the whole reason this case exists."},
    {"case_id": "in-entry-window-sophomore", "dimension": "entry_window",
     "name": "Sophomore Research Track", "org": "State University",
     "summary": "A year-long research track for current sophomores.",
     "eligibility": "Open to current sophomores.",
     "student": {"grade": 10, "location": {"state": "WA"}},
     "gold_eligible": True,
     "note": "CONTROL: the student IS in the stated window (a current sophomore) — the too-late rule "
             "must NOT over-fire and exclude an in-window student."},
    # --- citizenship ---
    {"case_id": "citizen-noncitizen", "dimension": "citizenship",
     "name": "Federal STEM Internship", "org": "NIH",
     "summary": "A government research internship.",
     "eligibility": "Applicants must be U.S. citizens or permanent residents.",
     "student": {"grade": 12, "citizenship": "non-citizen", "location": {"state": "NY"}},
     "gold_eligible": False,
     "note": "explicit citizenship gate; a non-citizen is ineligible."},
    # --- open program: no restriction ---
    {"case_id": "open-program", "dimension": "open",
     "name": "Community Coding Club", "org": "Local Library",
     "summary": "A weekly coding club for teens.",
     "eligibility": "Open to all high school students.",
     "student": {"grade": 9, "location": {"state": "WA"}},
     "gold_eligible": True,
     "note": "no restriction — must be eligible."},
    # --- accepted marketing-hyperbole residual (over-exclusion watch) ---
    {"case_id": "marketing-exclusive", "dimension": "marketing",
     "name": "Elite Scholars Institute", "org": "Prep Co",
     "summary": "A program exclusively designed to challenge top students.",
     "eligibility": None,
     "student": {"grade": 10, "location": {"state": "WA"}},
     "gold_eligible": True,
     "note": "'exclusively designed to challenge top students' is marketing, NOT an eligibility gate. Watched residual."},
]


def score_eligibility(graded):
    """Pure scoring. `graded` is a list of {gold_eligible: bool, predicted_eligible: bool,
    dimension: str}. Returns overall + per-dimension precision/recall for BOTH directions,
    framed around exclusion (predicting ineligible):

      TP = correctly excluded (gold ineligible, predicted ineligible)
      FP = OVER-exclusion    (gold eligible,  predicted ineligible)  <- hides a real match
      FN = UNDER-exclusion   (gold ineligible, predicted eligible)   <- shows an ineligible one (worse)
      TN = correctly kept    (gold eligible,  predicted eligible)

    exclusion_precision = TP / (TP+FP)  — of what we hid, how much deserved it
    exclusion_recall    = TP / (TP+FN)  — of what should be hidden, how much we caught
    over_exclusion_count / under_exclusion_count are the raw harms.
    """
    def _counts(rows):
        tp = fp = fn = tn = 0
        for r in rows:
            gold_inelig = not r["gold_eligible"]
            pred_inelig = not r["predicted_eligible"]
            if gold_inelig and pred_inelig:
                tp += 1
            elif (not gold_inelig) and pred_inelig:
                fp += 1
            elif gold_inelig and (not pred_inelig):
                fn += 1
            else:
                tn += 1
        precision = tp / (tp + fp) if (tp + fp) else None
        recall = tp / (tp + fn) if (tp + fn) else None
        return {
            "n": len(rows), "correct": tp + tn,
            "true_exclusions": tp, "over_exclusions": fp, "under_exclusions": fn,
            "correct_keeps": tn,
            "exclusion_precision": precision, "exclusion_recall": recall,
        }

    overall = _counts(graded)
    by_dim = {}
    for dim in sorted({r["dimension"] for r in graded}):
        by_dim[dim] = _counts([r for r in graded if r["dimension"] == dim])
    return {"overall": overall, "by_dimension": by_dim}


def run_eligibility_eval(cases, verdict_fn):
    """Grade each case with `verdict_fn(case) -> predicted_eligible: bool` (injected so the
    live model call is stubbable/testable). Returns the graded list for score_eligibility."""
    graded = []
    for c in cases:
        pred = verdict_fn(c)
        graded.append({
            "case_id": c["case_id"], "dimension": c["dimension"],
            "gold_eligible": c["gold_eligible"], "predicted_eligible": bool(pred),
        })
    return graded


def _live_verdict_fn(gemini_key):
    """Real verdict: run the curation call over the single candidate + student, apply the
    guard, and report whether the candidate came back eligible. Needs a model key."""
    from gemini_common import call_gemini, extract_json
    from app.config import MESSAGES_MODEL
    from app.services.curation import (
        CURATION_SYSTEM, build_candidate_view, build_curation_user_content, finalize_curation,
    )

    def _fn(case):
        row = {"id": case["case_id"], "name": case["name"], "org": case.get("org"),
               "summary": case.get("summary"), "eligibility": case.get("eligibility"),
               "type": "Program", "subject_tags": []}
        student = {"grade": case["student"].get("grade"),
                   "location": case["student"].get("location") or {},
                   "profile_themes": [], "highlight_projects": [],
                   "funnel_answers": {k: v for k, v in case["student"].items()
                                      if k in ("gender", "citizenship")}}
        uc = build_curation_user_content(student, [build_candidate_view(row)])
        text, _usage = call_gemini(CURATION_SYSTEM, uc, gemini_key,
                                   use_web_search=False, max_tokens=1500, model=MESSAGES_MODEL)
        parsed = extract_json(text)
        if not isinstance(parsed, dict):
            return True  # unparseable -> treat as not-excluded (unknown != ineligible)
        final = finalize_curation(parsed, {case["case_id"]: row}, limit=10)
        return any(r["id"] == case["case_id"] for r in final["results"])

    return _fn


def _print_report(report):
    o = report["overall"]
    print(f"\n=== Eligibility eval: {o['correct']}/{o['n']} correct ===")
    print(f"  under-exclusions (WORSE — ineligible shown): {o['under_exclusions']}")
    print(f"  over-exclusions  (eligible hidden):          {o['over_exclusions']}")
    print(f"  exclusion precision: {o['exclusion_precision']}  recall: {o['exclusion_recall']}")
    print("  by dimension:")
    for dim, c in report["by_dimension"].items():
        print(f"    {dim:12} {c['correct']}/{c['n']}  under={c['under_exclusions']} over={c['over_exclusions']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="Print the labeled cases (free).")
    ap.add_argument("--run", action="store_true", help="Grade against the live curation model (needs GEMINI_API_KEY).")
    args = ap.parse_args()
    if args.list:
        for c in SEED_ELIGIBILITY_CASES:
            print(f"{c['case_id']:22} {c['dimension']:12} gold_eligible={c['gold_eligible']!s:5}  {c['note']}")
        print(f"\n{len(SEED_ELIGIBILITY_CASES)} cases.")
        return
    if args.run:
        from supabase_common import load_dotenv  # only for .env loading of the key
        load_dotenv()
        key = os.environ.get("GEMINI_API_KEY", "")
        if not key:
            print("[ERROR] GEMINI_API_KEY not set — cannot run the live eval.")
            sys.exit(1)
        graded = run_eligibility_eval(SEED_ELIGIBILITY_CASES, _live_verdict_fn(key))
        _print_report(score_eligibility(graded))
        return
    ap.error("pass --list or --run")


if __name__ == "__main__":
    main()

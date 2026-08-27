"""Grade a scraper decision policy against human review verdicts. Free, stdlib-only.

The 2026-08-23 batch is the first scraper output a person fully adjudicated: every one of
its 166 rows was approved, rejected, or deleted by hand, and that grading is frozen in
tests/fixtures/scraper_grading_20260823.json. This script replays a candidate-level
decision function over the batch's review snapshots and scores it against those verdicts,
so a pipeline change can be proven harmless BEFORE the next paid run:

    wins         human-rejected/deleted rows the policy would now suppress (review work
                 and catalog noise saved)
    REGRESSIONS  human-APPROVED rows the policy would suppress — real opportunities lost.
                 The bar is zero. A policy with one regression is worse than no policy,
                 because a suppressed row is invisible: nobody reviews what never arrived.

Nothing here reads or writes the database, and nothing it does affects what students see —
it grades hypothetical policies against a file on disk. The built-in deciders are
DIAGNOSTIC PROBES, not proposals: `flag-offsite` and `strong-dup` exist to show what a
naive auto-reject on those signals would have cost (each suppresses rows the human
approved, which is exactly why neither is a live rule).

Usage:
    python grade_scraper_batch.py                      # all deciders, default fixture
    python grade_scraper_batch.py --decider flag-offsite --verbose
    python grade_scraper_batch.py --fixture tests/fixtures/... --snapshot-dir .
"""

import argparse
import json
import os

import url_validate  # is_bare_domain, for the Phase-3 merge/keep discriminator (pure, no net)

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_FIXTURE = os.path.join(REPO_ROOT, "tests", "fixtures",
                               "scraper_grading_20260823.json")

# Verdicts that mean "a human did not want this row" — suppressing one is a win. A DELETED
# row is graded like a rejected one: the operator removed it by hand (duplicate or junk),
# which is the strongest possible "should never have been inserted" signal.
NEGATIVE_VERDICTS = {"rejected", "deleted"}


def load_fixture(path):
    with open(path, encoding="utf-8") as f:
        fixture = json.load(f)
    if not isinstance(fixture.get("verdicts"), dict):
        raise ValueError(f"{path}: no 'verdicts' object")
    return fixture


def load_snapshot_rows(fixture, snapshot_dir):
    """Every inserted row from the fixture's snapshots, review metadata attached.

    Rows carry the shape scrape_opportunities.py wrote: the catalog fields plus a
    'review' dict holding quality_flags and dup_candidates. Old bare-list snapshots
    (pre-2026-08-23) are tolerated for forward reuse with other fixtures.
    """
    rows = []
    for name in fixture.get("snapshots", []):
        path = os.path.join(snapshot_dir, name)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        inserted = data.get("inserted", []) if isinstance(data, dict) else data
        for row in inserted:
            if isinstance(row, dict) and row.get("id"):
                rows.append(row)
    return rows


# --------------------------------------------------------------------------- deciders
# A decider takes one snapshot row and returns (action, reason) where action is
# "insert" (today's behavior) or "suppress" (the row would not reach the review queue).
# Later workstreams add actions like "upgrade"/"merge"; the scorer treats anything that
# is not "insert" as removing the row from the queue.

def _flags(row):
    return [str(f) for f in (row.get("review", {}).get("quality_flags") or [])]


def _dup_candidates(row):
    return [c for c in (row.get("review", {}).get("dup_candidates") or [])
            if isinstance(c, dict)]


def decide_baseline(row):
    """What the pipeline does today: everything inserted reaches the queue."""
    return "insert", ""


def decide_flag_offsite(row):
    """PROBE: suppress every row flagged as sitting on an unrelated site."""
    for flag in _flags(row):
        if "unrelated site" in flag:
            return "suppress", flag
    return "insert", ""


def decide_strong_dup(row):
    """PROBE: suppress every row carrying a strong duplicate candidate."""
    for cand in _dup_candidates(row):
        if cand.get("confidence") == "strong":
            return "suppress", f"strong dup of {cand.get('id')} ({cand.get('reason')})"
    return "insert", ""


def _has_identical_url_dup(row):
    """A dup candidate that is the SAME normalized URL (match_key equality), name aside."""
    for cand in _dup_candidates(row):
        if cand.get("ratio") == 1.0 or "identical URL" in (cand.get("reason") or ""):
            return cand
    return None


def decide_url_dup(row):
    """Phase-3 rule: a candidate whose match_key equals an existing row's is never inserted as
    a NEW row. If the shared URL is a DEDICATED page it is MERGED into that row — the program
    survives in the incumbent and the best fields flow in, so this is not a lost row. If the
    shared URL is a BARE DOMAIN the two may be distinct programs that both landed on the org
    homepage (tenet 6: "both need their dedicated pages found"), so it is kept + flagged for
    review, never auto-merged. The rule is match_key EQUALITY plus a bare-domain guard, never
    name similarity — which is why it drops ZERO approved rows where the strong-dup probe,
    keyed on name similarity, drops 18. Measured discriminator on the 08-23 batch: the one
    genuinely-distinct same-URL approval (YoungArts) is the only bare-domain one."""
    cand = _has_identical_url_dup(row)
    if not cand:
        return "insert", ""
    if url_validate.is_bare_domain(row.get("url") or ""):
        return "insert", f"shares homepage URL with {cand.get('id')} — kept for review"
    return "merge", f"merge into {cand.get('id')} ({cand.get('reason')})"


DECIDERS = {
    "baseline": decide_baseline,
    "flag-offsite": decide_flag_offsite,
    "strong-dup": decide_strong_dup,
    "url-dup": decide_url_dup,
}


# ----------------------------------------------------------------------------- scoring
def evaluate(rows, verdicts, decide):
    """Score one decider. Returns a dict; see keys below.

    Rows without a verdict are counted as `ungraded` and excluded from wins/regressions —
    they are a fixture gap to report, never silently a pass or a fail.
    """
    wins, regressions, ungraded, merges = [], [], [], []
    kept_negative = 0
    for row in rows:
        entry = verdicts.get(row["id"])
        action, reason = decide(row)
        # A "merge" removes the row from the NEW-queue but preserves the program in the row it
        # merges into; a "suppress" drops it entirely. Only the latter can lose an approved row.
        removed = action in ("suppress", "merge")
        if entry is None:
            ungraded.append(row["id"])
            continue
        verdict = entry.get("verdict")
        if verdict == "approved":
            if action == "suppress":
                regressions.append({"id": row["id"], "name": row.get("name"), "reason": reason})
            elif action == "merge":
                merges.append({"id": row["id"], "name": row.get("name"), "reason": reason})
        elif verdict in NEGATIVE_VERDICTS:
            if removed:
                wins.append({"id": row["id"], "name": row.get("name"),
                             "verdict": verdict, "reason": reason})
            else:
                kept_negative += 1
    graded = len(rows) - len(ungraded)
    return {
        "graded": graded,
        "wins": wins,
        "regressions": regressions,
        "merges": merges,                 # approved rows consolidated into a survivor (not lost)
        "kept_negative": kept_negative,   # human-rejected rows the policy still inserts
        "ungraded": ungraded,
    }


def print_report(name, result, verbose=False):
    n_win, n_reg = len(result["wins"]), len(result["regressions"])
    n_merge = len(result.get("merges", []))
    verdict = "SAFE" if n_reg == 0 else f"UNSAFE — {n_reg} approved row(s) lost"
    merge_note = f", {n_merge} approved row(s) merged (preserved)" if n_merge else ""
    print(f"\n[{name}] graded {result['graded']} rows: "
          f"{n_win} win(s), {n_reg} regression(s){merge_note}, "
          f"{result['kept_negative']} rejected row(s) still inserted -> {verdict}")
    if result["ungraded"]:
        print(f"  ungraded (no verdict in fixture): {len(result['ungraded'])}")
    for m in result.get("merges", []):
        print(f"  merge (approved, preserved) {m['id']}  {m['name']}  [{m['reason']}]")
    for r in result["regressions"]:
        print(f"  REGRESSION {r['id']}  {r['name']}")
        print(f"             would suppress because: {r['reason']}")
    if verbose:
        for w in result["wins"]:
            print(f"  win ({w['verdict']}) {w['id']}  {w['name']}  [{w['reason']}]")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fixture", default=DEFAULT_FIXTURE)
    parser.add_argument("--snapshot-dir", default=REPO_ROOT,
                        help="Directory holding the snapshot files the fixture names.")
    parser.add_argument("--decider", choices=sorted(DECIDERS), default=None,
                        help="Run one decider (default: all).")
    parser.add_argument("--verbose", action="store_true",
                        help="List every win, not just regressions.")
    parser.add_argument("--json", default=None,
                        help="Also write full results to this path.")
    args = parser.parse_args()

    fixture = load_fixture(args.fixture)
    rows = load_snapshot_rows(fixture, args.snapshot_dir)
    verdicts = fixture["verdicts"]
    print(f"[OK] {len(rows)} snapshot row(s), {len(verdicts)} verdict(s) "
          f"from {os.path.basename(args.fixture)}")

    names = [args.decider] if args.decider else sorted(DECIDERS)
    results = {}
    for name in names:
        results[name] = evaluate(rows, verdicts, DECIDERS[name])
        print_report(name, results[name], verbose=args.verbose)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=1, ensure_ascii=False)
        print(f"\n[OK] wrote {args.json}")


if __name__ == "__main__":
    main()

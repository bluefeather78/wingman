#!/usr/bin/env python3
"""Pilot harness for the mailing-list finder: pick the sample, then grade what it found.

The finder's own output cannot tell you whether it is accurate — it reports what it
believes, and belief is the thing under test. This script is the measuring instrument:
it picks a deliberately adversarial 10-row sample, then turns the finder's output into a
worksheet a human fills in by opening each page, and computes the numbers from that.

Costs nothing and calls no API. `--verify` makes real HTTP requests to providers, but
those are the same requests the app already makes and no money changes hands.

THREE PASSES, in order:

  1. python -m agents.grade_mailing_lists --sample
     Picks the sample and prints the --ids string for the finder. Deterministic, so the
     same sample comes back on a re-run and two runs stay comparable.

  2. (approve, then) python -m agents.find_mailing_lists --ids <the ids>

  3. python -m agents.grade_mailing_lists --worksheet
     Writes mailing_list_pilot.json: one entry per sampled row, carrying what the finder
     concluded and three blank fields for the grader. Open each page, fill them in:

       "truth_has_list": true/false   Does this program have a mailing list a person
                                      could join from this page? Answer by LOOKING, not by
                                      reading what the finder said. This is what makes
                                      RECALL measurable — the finder cannot report the
                                      lists it failed to find.
       "truth_correct":  true/false   If the finder proposed a recipe: is that form really
                                      THIS program's list (not the university's, not
                                      another program's)? Leave null if no recipe.
       "notes":          free text

  4. python -m agents.grade_mailing_lists --score
     Reads the filled worksheet and prints precision, recall and coverage.
     Add --verify to also send a real subscribe to each proposed recipe using a test
     address, which measures EXECUTION separately from discovery.

WHAT THE NUMBERS MEAN, and what they deliberately do not:

  precision  correct recipes / recipes proposed. The number that matters most: a wrong
             recipe silently sends a student someone else's mail.
  recall     recipes proposed and correct / rows a human found a list on. Expected to be
             low — only four providers are supported, and mailto:/portal signups are out
             of scope by design. A low recall is a scope finding, not a bug.
  execution  of proposed recipes, how many a real POST is accepted by.

  There is NO confirmed-delivery number, and there cannot be one here. Every supported
  provider double opt-ins, and we subscribe an address we do not own a mailbox for, so
  nothing can observe the confirmation being clicked. Do not add a fourth metric that
  quietly assumes it.
"""
import argparse
import io
import json
import os
import sys

from wingman.supabase_common import load_dotenv, supabase_get

PILOT_FILE = "mailing_list_pilot.json"

# A stratified, deliberately awkward sample. Random 10 rows would over-represent whatever
# the catalog has most of and would probably miss the shared-portal case entirely — which
# is the single failure mode this feature is most likely to have.
SAMPLE_PLAN = [
    ("university", 3, lambda o: any(d in (o.get("url") or "") for d in (".edu", "ac.uk"))),
    ("competition", 3, lambda o: (o.get("type") or "") in ("Competition", "Research")),
    ("shared portal", 2, lambda o: any(d in (o.get("url") or "")
                                       for d in ("smapply.io", "submittable.com", "app.")) ),
    ("other", 2, lambda o: True),
]


def load_catalog():
    load_dotenv()
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        print("[ERROR] SUPABASE_URL / SUPABASE_SERVICE_KEY not set in .env.")
        sys.exit(1)
    return url, key, supabase_get(url, "opportunities", {
        "select": "id,name,org,url,type", "is_active": "eq.true", "order": "id"}, key)


def pick_sample(catalog):
    """Deterministic stratified pick. Catalog order is stable, so re-running reproduces it."""
    chosen, taken = [], set()
    for label, want, matches in SAMPLE_PLAN:
        got = 0
        for opp in catalog:
            if got >= want or opp["id"] in taken:
                continue
            if matches(opp):
                chosen.append(dict(opp, stratum=label))
                taken.add(opp["id"])
                got += 1
        if got < want:
            print(f"[WARN] Only {got}/{want} rows matched the '{label}' stratum.")
    return chosen


def cmd_sample():
    _, _, catalog = load_catalog()
    sample = pick_sample(catalog)
    print(f"[OK] {len(sample)} rows, stratified:\n")
    for opp in sample:
        print(f"  [{opp['stratum']:<13}] {opp['id']}  {opp['name'][:48]}")
        print(f"                  {opp.get('url')}")
    ids = ",".join(o["id"] for o in sample)
    print("\nRun the finder over exactly these rows (this SPENDS money — get approval first):\n")
    print(f"  python -m agents.find_mailing_lists --ids {ids}\n")
    print("Preview it for free first:\n")
    print(f"  python -m agents.find_mailing_lists --preview --ids {ids}\n")


def cmd_worksheet():
    url, key, catalog = load_catalog()
    sample = pick_sample(catalog)
    ids = [o["id"] for o in sample]
    recipes = {r["opportunity_id"]: r for r in supabase_get(
        url, "opportunity_signups", {"select": "*"}, key)
        if r["opportunity_id"] in ids}

    missing = [i for i in ids if i not in recipes]
    if missing:
        print(f"[WARN] {len(missing)} sampled row(s) have no recipe yet — has the finder run?")

    rows = []
    for opp in sample:
        r = recipes.get(opp["id"]) or {}
        rows.append({
            "id": opp["id"], "name": opp["name"], "stratum": opp["stratum"],
            "url": opp.get("url"),
            "finder_method": r.get("method"),
            "finder_source_url": r.get("source_url"),
            "finder_evidence": r.get("scope_evidence"),
            "finder_reason": r.get("reason"),
            "finder_confidence": r.get("confidence"),
            # --- fill these in by opening the page ---
            "truth_has_list": None,
            "truth_correct": None,
            "notes": "",
        })
    with io.open(PILOT_FILE, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)
    print(f"[OK] Wrote {PILOT_FILE} with {len(rows)} rows.")
    print("     Open each `url`, then fill in truth_has_list / truth_correct / notes.")
    print("     Then: python -m agents.grade_mailing_lists --score")


def cmd_score(verify=False):
    if not os.path.exists(PILOT_FILE):
        print(f"[ERROR] {PILOT_FILE} not found — run --worksheet first.")
        sys.exit(1)
    rows = json.load(io.open(PILOT_FILE, encoding="utf-8"))
    ungraded = [r for r in rows if r.get("truth_has_list") is None]
    if ungraded:
        print(f"[ERROR] {len(ungraded)} row(s) still have truth_has_list = null. Grade them "
              f"first — an ungraded row cannot be scored as either right or wrong.")
        for r in ungraded:
            print(f"        {r['id']}  {r['name'][:50]}")
        sys.exit(1)

    proposed = [r for r in rows if r.get("finder_method") not in (None, "none")]
    correct = [r for r in proposed if r.get("truth_correct") is True]
    has_list = [r for r in rows if r.get("truth_has_list") is True]

    print(f"\n  Sample                 {len(rows)} rows")
    print(f"  Recipes proposed       {len(proposed)}")
    print(f"  Rows with a real list  {len(has_list)}  (per the human grader)")
    print()
    if proposed:
        print(f"  PRECISION  {len(correct)}/{len(proposed)} = {len(correct)/len(proposed):.0%}"
              f"   correct recipes / recipes proposed")
    else:
        print("  PRECISION  n/a — nothing was proposed")
    if has_list:
        print(f"  RECALL     {len(correct)}/{len(has_list)} = {len(correct)/len(has_list):.0%}"
              f"   correct recipes / lists that exist")
    else:
        print("  RECALL     n/a — the grader found no lists in this sample")

    wrong = [r for r in proposed if r.get("truth_correct") is False]
    if wrong:
        print(f"\n  Wrong recipes ({len(wrong)}) — these are the ones that matter:")
        for r in wrong:
            print(f"    {r['id']}  {r['name'][:44]}")
            print(f"      claimed: \"{(r.get('finder_evidence') or '')[:80]}\"")
            print(f"      grader:  {r.get('notes') or '(no note)'}")

    missed = [r for r in rows if r.get("truth_has_list") is True
              and r.get("finder_method") in (None, "none")]
    if missed:
        print(f"\n  Missed ({len(missed)}) — a list exists but no recipe was proposed:")
        for r in missed:
            print(f"    {r['id']}  {r['name'][:44]}  {r.get('notes') or ''}")

    if verify:
        cmd_verify(proposed)
    print()


def cmd_verify(proposed):
    """Send a real subscribe through each proposed recipe with a test address.

    This measures EXECUTION, which is a different thing from discovery: a perfectly
    attributed recipe can still be unpostable. Use an address you control — the
    confirmation emails are real and will arrive there.
    """
    from wingman import mailing_list_common
    from wingman.supabase_common import load_dotenv as _ld
    _ld()
    test_email = os.environ.get("MAILING_LIST_TEST_EMAIL", "")
    if not test_email:
        print("\n  [SKIP] --verify needs MAILING_LIST_TEST_EMAIL in .env — an address you "
              "own and can check. Real confirmation emails will be sent to it.")
        return
    url, key, _ = load_catalog()
    stored = {r["opportunity_id"]: r for r in supabase_get(
        url, "opportunity_signups", {"select": "*"}, key)}

    print(f"\n  EXECUTION — posting to {len(proposed)} recipe(s) as {test_email}:")
    ok = 0
    for r in proposed:
        recipe = stored.get(r["id"])
        if not recipe:
            print(f"    {r['name'][:40]:<42} no stored recipe")
            continue
        state, message, _detail = mailing_list_common.execute(
            recipe, {"email": test_email, "first_name": "Wingman", "last_name": "Test"})
        ok += 1 if state in ("submitted", "already_subscribed") else 0
        print(f"    {r['name'][:40]:<42} {state}  {message[:60]}")
    print(f"\n  EXECUTION  {ok}/{len(proposed)} accepted by the provider")
    print("  (accepted != subscribed — check the test mailbox for confirmation emails)")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--sample", action="store_true", help="Pick the pilot rows and print the --ids for the finder.")
    mode.add_argument("--worksheet", action="store_true", help="Write the grading worksheet from what the finder stored.")
    mode.add_argument("--score", action="store_true", help="Score the filled-in worksheet.")
    parser.add_argument("--verify", action="store_true",
                        help="With --score: also POST each recipe for real, using "
                             "MAILING_LIST_TEST_EMAIL. Sends real confirmation emails.")
    args = parser.parse_args()
    if args.sample:
        cmd_sample()
    elif args.worksheet:
        cmd_worksheet()
    else:
        cmd_score(verify=args.verify)


if __name__ == "__main__":
    main()

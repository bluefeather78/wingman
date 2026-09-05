"""Theme-extraction eval: does the merged profile-tag prompt group at the right altitude?

This grades the `extractTagsAndBasics` prompt in
`frontend/src/lib/profileTags.ts` — the one that reduces a synthesized profile to the
MECE filter-facet themes. The golden-matching set (gen_golden_profiles.py) hands each
profile ONE assumed search_theme, so it never exercises the grouping; this does.

The case that motivated it: shamabildikar's real profile produced a single theme
"Community service and baking", welding two unrelated searchable interests. A facet row
like that filters nothing (one search cannot serve both halves) and embeds to a muddy
recall vector. The prompt was edited 2026-09-03 (ONE DIRECTION PER THEME + the standalone
one-line-interest carve-out); this eval is the measuring instrument for that fix and the
regression guard against it drifting back.

It reads the system prompt STRAIGHT FROM the .ts source, so it always grades the shipped
prompt, and calls the same model production uses (gemini-3.5-flash-lite via /api/messages).

Cost: one paid Gemini call per (case x run). ~$0.002/call at TAG_EXTRACT_MAX_TOKENS, so a
full default pass (all cases, --runs 1) is well under a cent. LLM output varies run to run
— use --runs N to measure a rate rather than a single roll.

Usage:
    python eval/theme_extraction_eval.py --list          # free: cases + assertions, no calls
    python eval/theme_extraction_eval.py                 # one run per case (paid)
    python eval/theme_extraction_eval.py --runs 5        # 5 runs per case, report pass rate
    python eval/theme_extraction_eval.py --show          # also print the themes each run produced

Exit code is nonzero if any case fails on any run (so it can gate a prompt change).
"""
import argparse, json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Kept in step with ranking.ts PROFILE_BASICS_RULE; the prompt interpolates it.
PROFILE_BASICS_RULE = ('"grade" (their school year, e.g. "11th grade"), "state" (US state or '
    'region they live in, spelled out), "gender". Set a key to null if the student did not say '
    'it — never guess, never infer from stereotypes, and never fill a value in just to avoid a null.')

MODEL = "gemini-3.5-flash-lite"   # MESSAGES_MODEL — what /api/messages uses for this feature
MAX_TOKENS = 8000                 # TAG_EXTRACT_MAX_TOKENS in profileTags.ts


def load_system_prompt():
    """The merged extraction system prompt, lifted verbatim from the TS source so this
    eval can never grade a prompt other than the one that ships."""
    path = os.path.join(ROOT, "frontend", "src", "lib", "profileTags.ts")
    ts = open(path, encoding="utf-8").read()
    m = re.search(r"const system = `(You are reading a high school student's profile.*?)`;", ts, re.S)
    if not m:
        raise SystemExit("Could not locate the merged system prompt in profileTags.ts")
    return m.group(1).replace("${PROFILE_BASICS_RULE}", PROFILE_BASICS_RULE)


# A "concept" is a set of keywords; a theme COVERS it if any keyword appears in the theme's
# tag or intent (lowercased). `must_separate` pairs must never be covered by the SAME theme;
# `expect_present` concepts must be covered by at least one theme.
CASES = [
    {
        "id": "thin_service_baking",
        "why": "The minimal reproduction: baking is a lone new hobby with no culinary sibling, "
               "so the old prompt fused it onto the nearest item (community service).",
        "profile": (
            "I'm a 10th grader in Washington. I volunteer through Kids Coming Together — library "
            "shifts and craft projects for elderly residents at a local senior living community. "
            "I recently got into baking as a new hobby and want to learn to bake properly from my "
            "mom. I'm also on my school's robotics club, where I help build our competition robot."
        ),
        "must_separate": [
            (["baking", "cooking", "bake", "culinary", "pastry"],
             ["community service", "volunteer", "service", "outreach", "senior", "elderly"]),
        ],
        "expect_present": [
            ["baking", "cooking", "bake", "culinary"],
            ["volunteer", "service", "outreach", "senior", "elderly", "community"],
            ["robot"],
        ],
    },
    {
        "id": "shama_like",
        "why": "Condensed from the real shamabildikar profile around the collision region "
               "(the rich profile still conflated once).",
        "profile": (
            "I'm a freshman at a STEM high school in Washington, passionate about computational "
            "linguistics, AI, and language science, and I read physics for fun. I serve as PR for my "
            "school's physics club and want to improve my USAPhO score. I'm involved in community "
            "service through Kids Coming Together, including craft projects for elderly residents, "
            "library volunteering, and donating baked goods to a local senior living community. I "
            "enjoy cooking and recently got into baking, and I want to learn to bake properly and "
            "recreate dishes I've enjoyed, like cacio e pepe, and make fresh pasta from scratch."
            "\n\nPassion Project: I founded and led a Linguistics Club, designing an original "
            "curriculum and running weekly lessons for 10+ students."
        ),
        "must_separate": [
            (["baking", "cooking", "bake", "culinary", "pasta"],
             ["community service", "volunteer", "service", "outreach", "senior", "elderly"]),
        ],
        "expect_present": [
            ["baking", "cooking", "bake", "culinary", "pasta"],
            ["volunteer", "service", "outreach", "senior", "elderly", "community"],
            ["linguistic", "language"],
        ],
    },
]


def theme_covers(theme, keywords):
    hay = (str(theme.get("tag", "")) + " " + str(theme.get("intent", ""))).lower()
    return any(k in hay for k in keywords)


def grade(themes):
    """Return (ok, failures[]) for one model response's theme list."""
    failures = []
    for case_pair in GRADE_CTX["must_separate"]:
        a, b = case_pair
        for t in themes:
            if theme_covers(t, a) and theme_covers(t, b):
                failures.append(f'CONFLATED in one theme "{t.get("tag")}": '
                                f'{a[0]!r} + {b[0]!r} belong in separate themes')
    for concept in GRADE_CTX["expect_present"]:
        if not any(theme_covers(t, concept) for t in themes):
            failures.append(f'MISSING: no theme covers {concept[0]!r}')
    # Belt-and-braces: a tag literally conjoining two tracked concepts with "and"/comma.
    tracked = [c for pair in GRADE_CTX["must_separate"] for c in pair]
    for t in themes:
        tag = str(t.get("tag", ""))
        if re.search(r"\band\b|,", tag.lower()):
            hits = [c[0] for c in tracked if any(k in tag.lower() for k in c)]
            if len(set(hits)) >= 2:
                failures.append(f'CROSS-AREA CONJUNCTION in tag "{tag}": {hits}')
    return (not failures, failures)


GRADE_CTX = {}  # set per case before grade() is called


def run_case(system, case, runs, show):
    from wingman import gemini_common
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise SystemExit("GEMINI_API_KEY not set (put it in .env) — cannot run.")
    user_content = (f'STUDENT PROFILE:\n\n{case["profile"]}\n\n'
                    'Return the JSON object with "basics" and "tags" only.')
    global GRADE_CTX
    GRADE_CTX = case
    passes, total_cost = 0, 0.0
    for i in range(runs):
        text, usage = gemini_common.call_gemini(
            system, user_content, api_key,
            use_web_search=False, max_tokens=MAX_TOKENS, thinking_level="low", model=MODEL)
        total_cost += gemini_common.estimate_cost(usage)
        try:
            parsed = gemini_common.extract_json(text)
            if isinstance(parsed, str):
                parsed = json.loads(parsed)
            themes = parsed.get("tags") or []
        except Exception as e:
            print(f"  run {i+1}: UNPARSEABLE ({e})")
            continue
        ok, failures = grade(themes)
        passes += 1 if ok else 0
        mark = "PASS" if ok else "FAIL"
        print(f"  run {i+1}: {mark}  ({len(themes)} themes)")
        if show:
            for t in themes:
                print(f"       - {t.get('tag')}")
        for f in failures:
            print(f"       ! {f}")
    return passes, runs, total_cost


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=1, help="model runs per case")
    ap.add_argument("--list", action="store_true", help="print cases + assertions, make no calls")
    ap.add_argument("--show", action="store_true", help="print the themes each run produced")
    args = ap.parse_args()

    if args.list:
        for c in CASES:
            print(f'{c["id"]}: {c["why"]}')
            for a, b in c["must_separate"]:
                print(f'    must separate: {a[0]!r}  vs  {b[0]!r}')
            for concept in c["expect_present"]:
                print(f'    must be present: {concept[0]!r}')
        return 0

    import app.config  # loads .env
    system = load_system_prompt()
    all_ok, grand_cost = True, 0.0
    for c in CASES:
        print(f'\n=== {c["id"]} ({args.runs} run{"s" if args.runs != 1 else ""}) ===')
        p, n, cost = run_case(system, c, args.runs, args.show)
        grand_cost += cost
        print(f'  -> {p}/{n} passed   (${cost:.4f})')
        if p < n:
            all_ok = False
    print(f'\nTOTAL cost ${grand_cost:.4f}  |  {"ALL PASSED" if all_ok else "FAILURES ABOVE"}')
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())

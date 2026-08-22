#!/usr/bin/env python3
"""One-time (then periodic, ~monthly) high-quality scrape for opportunities missing
from wingman's Supabase `opportunities` catalog — including *hyperlocal* ones (YMCA,
library systems, farmers markets, etc.) that don't explicitly market themselves as
"high school programs" but a motivated student could turn into one.

Design: for each hand-written "seed" (a category + a search angle), makes one Gemini
API call with the `googleSearch` tool enabled (see gemini_common.call_gemini — as of
the 2026-08-18 migration off Anthropic, see that module's docstring for why) and asks
the model to search broadly, judge relevance/legitimacy, and return a JSON array of
real opportunities it found evidence for. Not a single item per call — one seed can
(and should, when there's real material) surface several.

Every row this script inserts is written with is_active=false — never straight to
production. It also writes a local scrape_review_<timestamp>.json snapshot of
everything it inserted, for a manual skim before flipping anything live. See
CLAUDE.md and the plan doc for the manual activation step (spot-check, then
UPDATE ... SET is_active = true WHERE source = '...').

SETUP:
    .env (repo root) needs:
        SUPABASE_URL=https://<project-ref>.supabase.co
        SUPABASE_SERVICE_KEY=<service_role key>
        GEMINI_API_KEY=<key>
    Run the agent_runs SQL (see check_deadlines.py's docstring / the plan doc)
    before the first non-dry-run use, so run logging doesn't fail.

USAGE:
    python scrape_opportunities.py --mode seattle --dry-run   # no writes, just prints
    python scrape_opportunities.py --mode seattle              # real run
    python scrape_opportunities.py --mode national
"""
import argparse
import datetime
import difflib
import json
import os
import sys
import urllib.error

from agent_common import add_agent_args, apply_timing, emit_preview, snapshot_stamp
from gemini_common import call_gemini, extract_json, estimate_cost
from seeds_common import load_seeds, record_seed_result, select_seeds
from supabase_common import load_dotenv, supabase_get, supabase_post, supabase_insert_one, supabase_patch

VALID_SUBJECTS = ['Mixed', 'STEM', 'Medicine', 'Humanities', 'Art', 'Business', 'Engineering',
                   'Computer Science', 'Mathematics', 'Biology', 'Physics', 'Astronomy',
                   'Chemistry', 'Leadership', 'Law', 'Logic', 'Education']
VALID_TYPES = {'Program', 'Internship', 'Competition', 'Research', 'Volunteer', 'Journal', 'Conference'}
VALID_PRICE = {'Free', 'Paid'}
VALID_LOCATION = {'In-Person', 'Remote', 'In-Person and Remote'}
VALID_INTL = {'International Students', 'Domestic Students'}
VALID_SEASON = {'Summer', 'Year-Long', 'Spring', 'Fall', 'Winter'}
DEDUP_RATIO = 0.85  # matches the fuzzy-title threshold the old opportunity_agent.py used

# (category, search-angle) — category drives the fallback `type` value and shows up
# in the `category` column for provenance; each angle is appended to the shared
# system prompt as the specific thing this call should search for.
NATIONAL_SEEDS = [
    ("Program", "prestigious, selective national or regional summer academic programs and "
                 "pre-college academies for high schoolers, across STEM, humanities, business, and the arts"),
    ("Program", "free or need-based-aid summer programs and academies for high schoolers, "
                 "including programs specifically for underrepresented or low-income students"),
    ("Internship", "paid or unpaid internships and structured mentorship programs open to high "
                    "school students, in research labs, hospitals, tech companies, government agencies, or nonprofits"),
    ("Internship", "remote/virtual internship and shadowing programs for high schoolers that "
                    "don't require relocation, across a range of fields"),
    ("Research", "structured research programs, mentored research placements, or lab-access "
                  "programs specifically open to high school students"),
    ("Competition", "national academic competitions and olympiads for high schoolers in STEM, "
                      "humanities, or business (subject olympiads, case competitions, quiz-bowl-style contests)"),
    ("Competition", "national project-based or research competitions for high schoolers "
                      "(science fairs, app/hackathon-style challenges, invention competitions)"),
    ("Competition", "creative writing, visual art, or humanities competitions for high schoolers "
                      "with real prizes, publication, or recognition attached"),
    ("Volunteer", "structured, skills-based national volunteer programs or service fellowships "
                    "for high schoolers — real time commitment and structure, not generic one-off volunteering"),
    ("Journal", "student research journals or publications that accept submissions from high schoolers"),
    ("Conference", "academic or professional conferences and student summits that high schoolers "
                     "can attend or present at"),
    ("Program", "leadership development programs, youth advisory boards, or civic engagement "
                 "fellowships open to high schoolers nationally"),
    ("Program", "business/entrepreneurship-focused programs, accelerators, or pitch competitions "
                 "for high school students"),
    ("Program", "arts intensives, conservatories, or portfolio-building summer programs for high schoolers"),
    ("Internship", "healthcare and medicine shadowing or pre-med pipeline programs for high schoolers"),
    ("Program", "computer science and engineering-focused summer camps, bootcamps, or hackathons for high schoolers"),
    ("Program", "AI ethics, AI safety, and responsible AI programs for high schoolers, including coursework, "
                 "research opportunities, and fellowships focused on beneficial AI and safety considerations"),
    ("Program", "rationality, critical thinking, decision-making, metacognition, and forecasting programs "
                 "for high schoolers (including game theory, applied reasoning workshops, and thinking-skills development)"),
    ("Research", "research programs focused on direct social impact: biosecurity, pandemic preparedness, global health, "
                 "climate science, clean energy, and environmental solutions open to high school researchers"),
    ("Program", "international immersive residential programs and camps for high schoolers (10-21 days, overseas locations, "
                 "typically including tuition, room, and board; cutting-edge academic or specialized topics)"),
    ("Competition", "debate, speech, forensics, and argumentation competitions for high schoolers (parliamentary debate, "
                     "policy debate, public forum, Lincoln-Douglas, dramatic interpretation, extemporaneous speaking)"),
    ("Internship", "cybersecurity, cryptography, hacking competitions, and cybersecurity research programs for high schoolers "
                   "(CTF competitions, bug bounties, security camps, penetration testing training)"),
    ("Research", "environmental science, field research, marine biology, conservation, and ecological restoration programs "
                 "for high schoolers with hands-on outdoor or lab-based research components"),
    ("Program", "writing, creative writing, journalism, literary magazines, and publishing programs for high school students "
                "(distinct from creative writing competitions — focused on skill-building, publication opportunities, and mentorship)"),
    ("Internship", "policy analysis, government affairs, civic engagement internships, and political/advocacy organizations "
                   "open to high schoolers (distinct from generic leadership programs — focused on policy work, advocacy, government offices)"),
    ("Program", "robotics, maker spaces, fabrication labs, engineering challenges, and hardware-software integration programs "
                "specifically for high schoolers (distinct from general CS/engineering — hands-on building, robotics competitions, invention)"),
    ("Research", "psychology, neuroscience, cognitive science, and behavioral research programs and internships for high schoolers "
                 "(lab-based research, behavioral studies, brain science, perception research)"),
    ("Research", "sociology, anthropology, ethnography, and social sciences research programs for high schoolers "
                 "(field research, community studies, cultural research, social dynamics)"),
    ("Internship", "economics, finance, accounting, trading, and business analysis internships for high schoolers "
                   "(financial services, banking, investment firms, business analysis, economics research)"),
    ("Program", "graphic design, UX/UI design, digital design, web design, and product design programs for high schoolers "
                "(portfolio-building, design thinking, prototyping, design competitions)"),
    ("Program", "architecture, landscape architecture, urban design, and architectural visualization programs for high schoolers "
                "(design studios, CAD training, architecture competitions, built environment)"),
    ("Program", "fashion design, fashion business, apparel design, and fashion industry programs for high schoolers "
                "(design, merchandising, styling, fashion competitions, industry internships)"),
    ("Program", "dance, ballet, contemporary dance, hip-hop, and choreography programs for high schoolers "
                "(intensive training, performances, choreography workshops, dance companies)"),
    ("Program", "culinary arts, pastry, cooking, and hospitality management programs for high schoolers "
                "(culinary bootcamps, chef mentorships, food service programs, kitchen experience)"),
    ("Program", "sports management, kinesiology, sports science, athletic coaching, and sports medicine programs for high schoolers "
                "(sports science labs, coaching certification, athletic training, sports business)"),
    ("Program", "theology, religious studies, interfaith dialogue, and spiritual leadership programs for high schoolers "
                "(seminary prep, religious organizations, faith-based leadership, spiritual retreats)"),
    ("Program", "AI and machine learning immersive bootcamps, intensive training, and hands-on research opportunities for high schoolers "
                "(practical ML projects, neural networks, model training, AI applications — distinct from theoretical AI ethics programs)"),
    ("Program", "linguistics, language learning, language technology, computational linguistics, and translation programs for high schoolers "
                "(endangered language documentation, multilingual NLP, language science, translation studies)"),
    ("Program", "film, video production, documentary filmmaking, cinematography, and media arts programs for high schoolers "
                "(production skills, editing, storytelling, filmmaking competitions, media labs)"),
    ("Program", "music performance, composition, music production, music technology, and audio engineering programs for high schoolers "
                "(instrument training, composition workshops, recording studios, music competitions, sound design)"),
]

SEATTLE_SEEDS = [
    ("Program", "Seattle Public Library and King County Library System teen programs, teen "
                 "advisory boards, or volunteer/leadership opportunities for high schoolers"),
    ("Volunteer", "YMCA branches and community centers in the Seattle metro area (Seattle, "
                    "Bellevue, Redmond, Tacoma) offering teen leadership, volunteering, or work-experience programs"),
    ("Program", "Seattle-area farmers markets and small local businesses (Pike Place Market, "
                 "neighborhood farmers markets, local makers/shops) that could let a motivated high schooler set up "
                 "a booth, run a project, or gain real hands-on experience — even if they don't explicitly market "
                 "this to students. Explain concretely, in the summary, how a specific kind of student project "
                 "could actually use this."),
    ("Program", "Seattle Parks & Recreation youth boards, teen programs, or seasonal youth "
                 "employment/leadership opportunities"),
    ("Program", "maker spaces, hackerspaces, or fabrication labs in the Seattle area open to "
                 "teens (open workshop nights, teen memberships, project support)"),
    ("Volunteer", "Seattle-area nonprofits offering skilled-volunteer roles for high schoolers "
                    "(tutoring, tech support, design/marketing help) rather than generic one-off volunteering"),
    ("Internship", "small businesses, startups, or local professionals in the Seattle area open "
                    "to informal mentorship, shadowing, or apprenticeship-style arrangements with motivated high schoolers"),
    ("Program", "Seattle-area arts organizations, museums, or civic/government offices offering "
                 "teen programs, internships, or youth councils"),
]

SYSTEM_BASE = """You are a meticulous researcher helping build a high-quality catalog of extracurricular \
opportunities (programs, internships, research, competitions, volunteer roles, journals, conferences) for \
high school students, for the app "Wingman". Today's date is {today}.

Search thoroughly with web_search for: {angle}

Hard rules — quality over quantity matters far more than hitting any target count:
- Only include real opportunities you found actual evidence for via web_search. Never invent one, \
never pad the list to reach a certain size. If you found 2 excellent ones, return 2 — do not add weak \
or speculative filler to look more thorough.
- Every "url" must be a real URL you actually found, never guessed or constructed from a pattern.
- Confirm (as best you can from what you found) that the opportunity is currently real/active — skip \
anything that looks defunct, or where you can't tell if it's still running.
- Reason about eligibility, grade range, and cost from what you actually find on the page — never guess.

Respond with ONLY a raw JSON array, no markdown fences, no preamble, no text after the array. Each \
element must match exactly this schema (use null for any field you don't have real evidence for):
{{"name": "...", "org": "...", "url": "...", "summary": "2-4 sentences covering what it is plus any \
useful detail (this is the only descriptive text shown to students, so don't be overly terse)", \
"type": "one of: Program, Internship, Competition, Research, Volunteer, Journal, Conference", \
"price": "Free, Paid, or null", "cost_detail": "short freeform cost note, or null", \
"state": "2-letter US state code if location-specific, else null", \
"location": "In-Person, Remote, In-Person and Remote, or null", \
"intl": "International Students, Domestic Students, or null — who is eligible", \
"season": "one of Summer, Year-Long, Spring, Fall, Winter, or null", \
"grade_min": integer or null, "grade_max": integer or null, \
"eligibility": "short freeform eligibility note, or null", \
"subject_tags": ["3-5 short tags: one broad category from {subjects}, plus 2-4 more specific tags \
naming the actual field, skill, or activity"]}}"""

SEATTLE_ADDENDUM = """

This is a hyperlocal, creative-reasoning sweep: the org itself may not describe this as a "high school \
opportunity" at all. Your job is to actively reason about whether a motivated high schooler could turn it \
into one — e.g. a student building an app could use a local farmers market booth to get beta customers. \
That's just one example, not a template — do not force every result into that same framing. In the \
"summary" field, concretely explain *why and how* a high schooler could actually use this one, specific \
to what you found, not generic filler. If you can't come up with a genuinely concrete, specific reason, leave \
the opportunity out."""


def next_id_generator(existing_ids):
    nums = [int(i[2:]) for i in existing_ids if i.startswith("ec") and i[2:].isdigit()]
    n = (max(nums) if nums else 18220) + 1
    while True:
        yield f"ec{n}"
        n += 1


def normalize_url(url):
    return (url or "").strip().rstrip("/").lower()


def is_duplicate(name, url, seen_urls, seen_names):
    norm = normalize_url(url)
    if norm and norm in seen_urls:
        return True
    for existing_name in seen_names:
        if difflib.SequenceMatcher(None, name.lower(), existing_name.lower()).ratio() >= DEDUP_RATIO:
            return True
    return False


def clean_value(value, valid_set):
    return value if value in valid_set else None


def build_row(candidate, seed_category, mint_id, source):
    name = (candidate.get("name") or "").strip()
    url = (candidate.get("url") or "").strip()
    if not name or not url:
        return None
    opp_type = candidate.get("type") if candidate.get("type") in VALID_TYPES else seed_category
    tags = candidate.get("subject_tags") or []
    if not isinstance(tags, list):
        tags = [tags] if tags else []
    return {
        "id": mint_id,
        "name": name,
        "org": (candidate.get("org") or "").strip() or None,
        "summary": candidate.get("summary"),
        "url": url,
        "type": opp_type,
        "price": clean_value(candidate.get("price"), VALID_PRICE),
        "state": (candidate.get("state") or None),
        "location": clean_value(candidate.get("location"), VALID_LOCATION),
        "intl": clean_value(candidate.get("intl"), VALID_INTL),
        "season": clean_value(candidate.get("season"), VALID_SEASON),
        "category": seed_category,
        "eligibility": candidate.get("eligibility"),
        "grade_min": candidate.get("grade_min") if isinstance(candidate.get("grade_min"), int) else None,
        "grade_max": candidate.get("grade_max") if isinstance(candidate.get("grade_max"), int) else None,
        "cost": candidate.get("cost_detail"),
        "subject_tags": tags or None,
        "is_active": False,
        "source": source,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["national", "seattle"], required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--seed-ids", type=str, default=None,
                         help="Comma-separated scraper_seeds ids to run (e.g. '12,15,31') instead "
                              "of every enabled seed. Ids are stable across edits, so this is the "
                              "safe way to retry just the seeds that failed in a prior run without "
                              "re-paying for the ones that succeeded. This is what the admin "
                              "console sends.")
    parser.add_argument("--seed-indices", type=str, default=None,
                         help="DEPRECATED — use --seed-ids. Comma-separated 0-based POSITIONS in "
                              "the seed list (e.g. '0,1,4,9'). Positions shift whenever a seed is "
                              "added, deleted or reordered, so a saved index list can silently "
                              "select different angles than it did when you wrote it down.")
    parser.add_argument("--max-searches", type=int, default=10,
                         help="SOFT budget (folded into the prompt) on how many searches Gemini "
                              "runs per seed (default 10) — unlike Anthropic's web_search "
                              "`max_uses`, Gemini's googleSearch tool has no server-enforced cap, "
                              "so this is a request the model can still exceed, not a guarantee. "
                              "Still worth setting: it bounds cost ($0.014/search + the tokens "
                              "each result adds to context) and wall time in the common case.")
    # --timeout comes from add_agent_args at 280s: broad national seeds with multiple
    # search rounds routinely take longer than the 120s the other agents use.
    add_agent_args(parser, default_timeout=280)
    args = parser.parse_args()
    apply_timing(args, gemini=True)

    load_dotenv()
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    service_key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    if not supabase_url or not service_key or not gemini_key:
        print("[ERROR] SUPABASE_URL / SUPABASE_SERVICE_KEY / GEMINI_API_KEY not set in .env.")
        sys.exit(1)

    # Seeds live in the scraper_seeds Supabase table so they can be edited from the admin
    # console; the module-level lists below are the fallback if that table is empty or
    # unreachable. See seeds_common.load_seeds().
    fallback = NATIONAL_SEEDS if args.mode == "national" else SEATTLE_SEEDS
    all_seeds = load_seeds(supabase_url, service_key, args.mode, fallback=fallback)
    seeds = select_seeds(all_seeds, seed_ids=args.seed_ids, seed_indices=args.seed_indices)

    # Preview: scope resolved, report and stop before the first (paid) Gemini call.
    if args.preview:
        emit_preview(len(seeds), "seeds",
                     [f"[{s.get('id')}] ({s['category']}) {s['angle'][:90]}" for s in seeds],
                     mode=args.mode,
                     seed_ids=[s.get("id") for s in seeds])
        return

    if not seeds:
        print("[OK] No seeds selected — nothing to do.")
        return

    addendum = "" if args.mode == "national" else SEATTLE_ADDENDUM
    today = datetime.date.today().isoformat()
    run_date = datetime.date.today().strftime("%Y%m%d")
    source = f"scraper-{args.mode}-{run_date}"

    print(f"[OK] Fetching existing catalog from Supabase for dedup...")
    existing = supabase_get(supabase_url, "opportunities", {"select": "id,name,url"}, service_key)
    seen_urls = {normalize_url(r["url"]) for r in existing if r.get("url")}
    seen_names = [r["name"] for r in existing if r.get("name")]
    mint_id = next_id_generator({r["id"] for r in existing})
    print(f"[OK] {len(existing)} existing rows loaded.")

    # Dry runs are logged too: they skip DATABASE writes, not the paid Gemini calls, so a
    # dry scrape costs the same as a real one. The "-dryrun" mode suffix marks them.
    run_mode = args.mode + ("-dryrun" if args.dry_run else "")
    run_row = supabase_insert_one(supabase_url, "agent_runs", {
        "agent": "scraper",
        "mode": run_mode,
        "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }, service_key)
    run_id = run_row["id"] if run_row else None

    inserted_rows = []
    total_cost = 0.0
    raw_found = 0
    duplicates_skipped = 0
    invalid_skipped = 0
    errors = 0
    total_searches = 0
    silent_search_count = 0

    for seed in seeds:
        category, angle = seed["category"], seed["angle"]
        seed_label = f"id={seed['id']}" if seed.get("id") is not None else "fallback"
        print(f"\n[SEED {seed_label}] ({category}) {angle[:80]}...")
        # Per-seed tallies, so each angle's own yield can be recorded against it. The
        # run-level counters below still aggregate across all seeds as before.
        seed_found = seed_added = seed_dupes = 0
        seed_cost = 0.0
        system = SYSTEM_BASE.format(today=today, angle=angle, subjects=", ".join(VALID_SUBJECTS)) + addendum
        user_content = f"Search now and return the JSON array per the schema for: {angle}"
        try:
            text, usage = call_gemini(system, user_content, gemini_key, use_web_search=True,
                                       max_tokens=6000, timeout=args.timeout,
                                       max_searches=args.max_searches)
            cost = estimate_cost(usage)
            total_cost += cost
            seed_cost = cost
            searches = (usage.get("server_tool_use") or {}).get("web_search_requests", 0)
            total_searches += searches
            # Silent skip-search: use_web_search=True but Gemini answered from training data
            # instead of actually invoking googleSearch (see gemini_common.py's docstring —
            # this is a real, observed, non-deterministic Gemini behavior with no error/
            # warning of its own; a 0-search seed's candidates were never live-verified).
            if searches == 0:
                silent_search_count += 1
            candidates = extract_json(text)
            if not isinstance(candidates, list):
                candidates = [candidates]
            raw_found += len(candidates)
            seed_found = len(candidates)
            # Report searches against the cap: if a seed consistently pins at the cap, it
            # was cut off mid-research and the cap (not the seed) is limiting result quality.
            capped = " (CAPPED)" if searches >= args.max_searches else ""
            silent = " [SILENT: no search invoked]" if searches == 0 else ""
            print(f"  -> {len(candidates)} candidate(s), {searches} search(es){capped}{silent}, ${cost:.4f}")
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    invalid_skipped += 1
                    continue
                name = (candidate.get("name") or "").strip()
                url = (candidate.get("url") or "").strip()
                if not name or not url:
                    invalid_skipped += 1
                    continue
                if is_duplicate(name, url, seen_urls, seen_names):
                    duplicates_skipped += 1
                    seed_dupes += 1
                    continue
                row = build_row(candidate, category, next(mint_id), source)
                if not row:
                    invalid_skipped += 1
                    continue
                seen_urls.add(normalize_url(url))
                seen_names.append(name)
                inserted_rows.append(row)
                seed_added += 1
        except urllib.error.HTTPError as e:
            errors += 1
            print(f"  [ERROR] HTTP {e.code}: {e.read().decode(errors='replace')[:200]}")
        except Exception as e:
            errors += 1
            print(f"  [ERROR] {e}")

        # Record this angle's yield against the seed itself, so the console can rank
        # angles by rows-added-per-dollar and retire the ones that only return dupes.
        # Skipped on a dry run — nothing was really added, so nothing should be credited.
        if not args.dry_run:
            record_seed_result(supabase_url, service_key, seed,
                               found=seed_found, added=seed_added,
                               dupes=seed_dupes, cost=seed_cost)
        # No explicit sleep: gemini_common enforces --min-delay (default 5s) between calls
        # and stamps its timestamp at call start, so a shorter sleep here did nothing.

    print(f"\n[SUMMARY] seeds run: {len(seeds)}, raw candidates found: {raw_found}, "
          f"duplicates skipped: {duplicates_skipped}, invalid skipped: {invalid_skipped}, "
          f"errors: {errors}, new rows: {len(inserted_rows)}, "
          f"searches: {total_searches} (cap {args.max_searches}/seed), "
          f"silent (no-search) seeds: {silent_search_count}/{len(seeds)}, cost: ${total_cost:.4f}")

    # Stamped to the second, unlike `source` above which stays a date so a whole day's
    # scrape groups under one source value. Two scrapes of the same mode on one day used to
    # land on the same filename, and the scraper writes this snapshot on LIVE runs too — so
    # the overwritten one was the record of rows that had actually been inserted.
    review_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                f"scrape_review_{args.mode}_{snapshot_stamp()}.json")
    with open(review_path, "w", encoding="utf-8") as f:
        json.dump(inserted_rows, f, indent=2, ensure_ascii=False)
    print(f"[OK] Wrote review snapshot: {review_path}")

    if args.dry_run:
        print(f"[DRY RUN] No opportunities were written. The run itself is still logged to "
              f"agent_runs (mode='{run_mode}') because it cost real money.")
    elif inserted_rows:
        supabase_post(supabase_url, "opportunities", inserted_rows, service_key)
        print(f"[OK] Inserted {len(inserted_rows)} row(s) into opportunities (is_active=false).")

    if run_id is not None:
        supabase_patch(supabase_url, "agent_runs", {"id": f"eq.{run_id}"}, {
            "finished_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "items_processed": len(seeds),
            # On a dry run nothing was actually inserted; report 0 so the row's own
            # numbers stay truthful, with the would-have-been count in notes.
            "items_added": 0 if args.dry_run else len(inserted_rows),
            "errors": errors,
            "cost_usd": round(total_cost, 4),
            "total_web_searches": total_searches,
            "silent_search_count": silent_search_count,
            "notes": (f"raw_found={raw_found}, duplicates_skipped={duplicates_skipped}, "
                      f"invalid_skipped={invalid_skipped}, "
                      f"max_searches={args.max_searches}, timeout={args.timeout}s"
                      + (f", would_have_added={len(inserted_rows)}" if args.dry_run else "")),
        }, service_key)
        print(f"[OK] Logged agent_runs id={run_id}.")

    print(f"\n[DONE] Review {review_path} before flipping is_active=true for source='{source}'.")


if __name__ == "__main__":
    main()

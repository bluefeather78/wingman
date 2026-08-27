#!/usr/bin/env python3
"""Periodic (~monthly) high-quality scrape for opportunities missing from wingman's Supabase
`opportunities` catalog — including *hyperlocal* ones (YMCA, library systems, farmers markets,
etc.) that don't explicitly market themselves as "high school programs" but a motivated
student could turn into one.

THE CONTRACT (rewritten 2026-08-23 — see SCRAPER_PLAN.md):

    seed = (mode, angle)
      -> Gemini turns the angle into its own search terms
      -> returns opportunities, each typed from the enum
      -> we resolve real URLs from grounding, validate them, and flag anything imperfect

The seed says *where to look*. The model says *what it found*. We verify and explain.

WHY IT IS A TWO-PHASE CALL. Gemini does not reliably put a retrieved URL in its answer:
when it answers without searching it writes URLs from memory, and they come out with the
right host and a path off by one segment. Measured: 30 of 116 URLs in the 2026-08-20 batch
were hard 404s (26%), every one a constructed deep path, never a bare domain. The ONLY place
the real URL exists is `groundingMetadata.groundingChunks`, which a JSON-only answer does not
carry back to us. So:

    Phase 1 (research)  prose out, googleSearch on -> keeps groundingChunks/groundingSupports
    Phase 2 (extract)   phase 1's notes + the RESOLVED urls in -> strict JSON out, no search

Phase 2 needs no search, so a strict output format costs nothing there. Do not collapse
these back into one call: asking for JSON in the searching call is what threw the grounding
away in the first place.

SILENT SEARCH. Gemini decides per call whether to search at all, non-deterministically —
seed 51 was run twice with an identical command and returned 0 searches once and 6 the next
time (see gemini_common.py's FIFTH finding). It cannot be forced. So phase 1 RETRIES once on
a zero-search response (cheap: a silent call skips the $0.014/search fees), and anything
still silent is flagged for review rather than trusted.

DISCARD ALMOST NOTHING, EXPLAIN EVERYTHING. Every row lands is_active=false with
moderation_status='pending_review' and short, actionable `quality_flags` saying what to check.
Only two things never reach the table: an exact duplicate (same normalized URL *and* matching
name), and a candidate with no URL at all — the URL is the row's identity. Both are written to
the review snapshot with their raw JSON so nothing vanishes silently. Never add a discard rule
here without reading url_dedupe.py's measurements first.

SETUP:
    .env (repo root) needs:
        SUPABASE_URL=https://<project-ref>.supabase.co
        SUPABASE_SERVICE_KEY=<service_role key>
        GEMINI_API_KEY=<key>
    Run the agent_runs SQL and user_submissions_schema.sql before the first non-dry-run use.

USAGE:
    python scrape_opportunities.py --mode seattle --preview    # free, resolves scope only
    python scrape_opportunities.py --mode seattle --dry-run    # PAID, no DB writes
    python scrape_opportunities.py --mode national             # real run
"""
import argparse
import datetime
import json
import os
import re
import sys
import urllib.error

import seed_ledger
import url_dedupe
import url_repair
import url_validate
from agent_common import add_agent_args, apply_timing, clean_email, emit_preview, snapshot_stamp
from contact_email_common import resolve_contact_email
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

# Dropped when matching a candidate's name against a grounding span: they carry no
# identifying signal and appear in almost every opportunity name, so requiring them would
# only make the match brittle.
_NAME_STOPWORDS = {"the", "a", "an", "of", "for", "and", "at", "in", "on", "to", "program",
                   "programs", "summer", "high", "school", "students", "student"}

# Review flags. The console renders each as a pill and truncates the visible text, so these
# must stay SHORT and must say what the reviewer should go and check — a flag that only
# names a symptom makes someone re-derive the diagnosis row by row.
FLAG_DEAD_LINK = "dead link (404) — program may be real; find the correct URL"
FLAG_BLOCKED_LINK = "link unverifiable ({code}) — site blocks checks; open it manually"
FLAG_UNREACHABLE = "link timed out — could not reach the site; open it manually"
FLAG_BARE_DOMAIN = "URL is a site homepage, not a program page"
FLAG_LOW_VALUE = "URL is a sub-page (faq/about/apply), not the main page"
FLAG_NOT_SEARCHED = "not search-verified — model answered from memory; check every field"
FLAG_URL_UNSOURCED = "URL not among the pages actually searched — may be from memory"
FLAG_URL_REPLACED = "URL replaced with the page the search returned — confirm it matches"
# Live, not a homepage, not a sub-page — and still the wrong page. 10 of the 166 rows on
# 2026-08-23 stored an SEO round-up ("19 Selective Internships for High School Students")
# that only mentions the program. Every other URL check passes those, so this is the one
# flag that catches them. See url_validate.domain_matches_org().
FLAG_OFFSITE = "URL is on an unrelated site — may be an article about it, not its own page"
FLAG_NO_TYPE = "no valid type returned — set one before activating"
# Phase-2 URL truth. A rescued URL was moved off a listicle/mill onto the program's own page
# (verified on-domain + title-proven); a reviewer still confirms it matches. An unproven title
# is never a rejection — false negatives like "Algebra II" vs "Algebra 2" are the accepted cost.
FLAG_URL_RESCUED = "URL rescued to the program's own page (was on {domain}) — confirm it matches"
FLAG_TITLE_UNPROVEN = "page title does not clearly name this program — confirm it's the right page"
# Phase-3 uniqueness. A row sharing a program's HOMEPAGE with another may be a genuinely
# distinct program that just has no page of its own (tenet 6), so it is kept + flagged rather
# than auto-merged — a person confirms it's distinct or a duplicate.
FLAG_SHARES_HOMEPAGE = "shares a homepage URL with {id} — confirm distinct program vs duplicate"

# Scraper-owned fields a merge may FILL on the surviving row when they are empty there. Name
# is handled separately (title evidence decides it). Deliberately excludes everything owned by
# other agents (important_dates/status/was_estimated/dates_last_checked_at, review_*, link_*)
# and all provenance (id/source/created_at/seed_id) and visibility (is_active/moderation_*).
MERGE_FILL_FIELDS = ("org", "summary", "eligibility", "grade_min", "grade_max",
                     "subject_tags", "contact_email")


def classify_same_url(url, exact, dup_candidates):
    """The Phase-3 disposition of a candidate that may share its URL with an existing row.

    Returns (action, target): action is 'merge' | 'reject' | 'flag' | 'insert', target is the
    existing row/candidate dict (or None). This is the SINGLE source of truth for the same-URL
    rule, shared by the live scrape loop AND grade_scraper_batch's decider — so a change to the
    rule is graded against every accumulated fixture automatically (Phase 5), never drifting
    between a reimplementation and the real thing.

      - same URL on a DEDICATED page  -> merge into the incumbent (same program)
      - same URL, name-similar, BARE   -> reject (a homepage + matching name is almost surely a dup)
      - same URL, name-different, BARE -> flag (may be distinct programs sharing a homepage, tenet 6)
      - no same-URL match              -> insert normally
    """
    same_url = exact or next(
        (c for c in (dup_candidates or []) if "identical URL" in (c.get("reason") or "")), None)
    if same_url and not url_validate.is_bare_domain(url):
        return "merge", same_url
    if exact:
        return "reject", exact
    if same_url:
        return "flag", same_url
    return "insert", None


def merge_row(candidate, existing, page_title):
    """Best-copy-wins merge of a re-found candidate INTO an existing row. Returns (patch, notes).

    Only ever IMPROVES the survivor: the name changes only when the shared page's title proves
    the candidate's name and NOT the incumbent's (evidence, never length — Round 2 measured the
    incumbent winning 27/28, so the default is to keep it); every other scraper-owned field
    fills only where the incumbent is empty. `notes` are the old values, for the survivor's
    quality_flags, so the merge is auditable and hand-reversible. `patch` is {} when nothing
    would change (a merge that improves nothing still suppresses the duplicate, it just writes
    no columns).
    """
    patch, notes = {}, []
    cname = (candidate.get("name") or "").strip()
    ename = (existing.get("name") or "").strip()
    org = existing.get("org") or candidate.get("org") or ""
    # Replace the name ONLY when the incumbent's is genuinely junk — fewer than two identifying
    # words of its own, e.g. "Columbia" / "Pre-College" — AND the candidate's name is proven by
    # the page. A more-specific incumbent name is never traded for a shorter one just because
    # strict title-proof spells a word differently ("Mathematics" vs "Math", "Algebra II" vs
    # "Algebra 2"); that false negative must not decide the name.
    if cname and cname != ename and len(url_repair.identity_words(ename, org)) < 2:
        if url_repair.title_proves(page_title, cname, org)[0]:
            patch["name"] = cname
            notes.append(f"name was '{ename}'")
    for field in MERGE_FILL_FIELDS:
        ev, cv = existing.get(field), candidate.get(field)
        if ev in (None, "", [], {}) and cv not in (None, "", [], {}):
            patch[field] = cv
            notes.append(f"filled {field}")
    return patch, notes


_MERGE_SELECT = ("id,name,org,summary,eligibility,grade_min,grade_max,"
                 "subject_tags,contact_email,url,quality_flags")


def apply_merge(supabase_url, service_key, candidate, target_id, url, timeout):
    """Fetch the surviving row, merge the candidate's better fields in, and PATCH it. Auto-applies
    (operator decision), active rows included; old values are appended to the survivor's
    quality_flags so the edit is auditable and hand-reversible. Returns a dict for the snapshot
    `merged` list, or None if the survivor can't be read. Never touches another agent's columns."""
    rows = supabase_get(supabase_url, "opportunities",
                        {"select": _MERGE_SELECT, "id": f"eq.{target_id}", "limit": "1"},
                        service_key)
    existing = (rows or [None])[0]
    if not existing:
        return None
    page, _final = url_repair._fetch(url, timeout)
    patch, notes = merge_row(candidate, existing, url_repair.page_title(page or ""))
    result = {"id": target_id, "from_name": candidate.get("name"),
              "into_name": existing.get("name"), "notes": notes, "changed": bool(patch)}
    if not patch:
        return result
    day = datetime.date.today().strftime("%Y-%m-%d")
    patch["quality_flags"] = list(existing.get("quality_flags") or []) + \
        [f"merged {day}: {n}" for n in notes]
    patch["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        supabase_patch(supabase_url, "opportunities", {"id": f"eq.{target_id}"}, patch, service_key)
    except Exception as e:
        print(f"  [WARN] merge PATCH of {target_id} failed: {e}")
        result["changed"] = False
        result["error"] = str(e)[:160]
    return result

# Angles only. The seed's `category` was dropped 2026-08-23: it was never sent to the model
# (it is not interpolated into either prompt), nothing in the student-facing app reads the
# `category` column, and its one live use was a silent `type` fallback. Measured across 238
# scraper rows, the model's type disagreed with the seed's category 27% of the time overall
# and 65% for Research seeds — so the fallback fired a guess that was usually wrong, at
# exactly the moment (a malformed response) when guessing is least defensible. An invalid
# type is now FLAG_NO_TYPE instead.
NATIONAL_SEEDS = [
    "prestigious, selective national or regional summer academic programs and "
    "pre-college academies for high schoolers, across STEM, humanities, business, and the arts",
    "free or need-based-aid summer programs and academies for high schoolers, "
    "including programs specifically for underrepresented or low-income students",
    "paid or unpaid internships and structured mentorship programs open to high "
    "school students, in research labs, hospitals, tech companies, government agencies, or nonprofits",
    "remote/virtual internship and shadowing programs for high schoolers that "
    "don't require relocation, across a range of fields",
    "structured research programs, mentored research placements, or lab-access "
    "programs specifically open to high school students",
    "national academic competitions and olympiads for high schoolers in STEM, "
    "humanities, or business (subject olympiads, case competitions, quiz-bowl-style contests)",
    "national project-based or research competitions for high schoolers "
    "(science fairs, app/hackathon-style challenges, invention competitions)",
    "creative writing, visual art, or humanities competitions for high schoolers "
    "with real prizes, publication, or recognition attached",
    "structured, skills-based national volunteer programs or service fellowships "
    "for high schoolers — real time commitment and structure, not generic one-off volunteering",
    "student research journals or publications that accept submissions from high schoolers",
    "academic or professional conferences and student summits that high schoolers "
    "can attend or present at",
    "leadership development programs, youth advisory boards, or civic engagement "
    "fellowships open to high schoolers nationally",
    "business/entrepreneurship-focused programs, accelerators, or pitch competitions "
    "for high school students",
    "arts intensives, conservatories, or portfolio-building summer programs for high schoolers",
    "healthcare and medicine shadowing or pre-med pipeline programs for high schoolers",
    "computer science and engineering-focused summer camps, bootcamps, or hackathons for high schoolers",
    "AI ethics, AI safety, and responsible AI programs for high schoolers, including coursework, "
    "research opportunities, and fellowships focused on beneficial AI and safety considerations",
    "rationality, critical thinking, decision-making, metacognition, and forecasting programs "
    "for high schoolers (including game theory, applied reasoning workshops, and thinking-skills development)",
    "research programs focused on direct social impact: biosecurity, pandemic preparedness, global health, "
    "climate science, clean energy, and environmental solutions open to high school researchers",
    "international immersive residential programs and camps for high schoolers (10-21 days, overseas locations, "
    "typically including tuition, room, and board; cutting-edge academic or specialized topics)",
    "debate, speech, forensics, and argumentation competitions for high schoolers (parliamentary debate, "
    "policy debate, public forum, Lincoln-Douglas, dramatic interpretation, extemporaneous speaking)",
    "cybersecurity, cryptography, hacking competitions, and cybersecurity research programs for high schoolers "
    "(CTF competitions, bug bounties, security camps, penetration testing training)",
    "environmental science, field research, marine biology, conservation, and ecological restoration programs "
    "for high schoolers with hands-on outdoor or lab-based research components",
    "writing, creative writing, journalism, literary magazines, and publishing programs for high school students "
    "(distinct from creative writing competitions — focused on skill-building, publication opportunities, and mentorship)",
    "policy analysis, government affairs, civic engagement internships, and political/advocacy organizations "
    "open to high schoolers (distinct from generic leadership programs — focused on policy work, advocacy, government offices)",
    "robotics, maker spaces, fabrication labs, engineering challenges, and hardware-software integration programs "
    "specifically for high schoolers (distinct from general CS/engineering — hands-on building, robotics competitions, invention)",
    "psychology, neuroscience, cognitive science, and behavioral research programs and internships for high schoolers "
    "(lab-based research, behavioral studies, brain science, perception research)",
    "sociology, anthropology, ethnography, and social sciences research programs for high schoolers "
    "(field research, community studies, cultural research, social dynamics)",
    "economics, finance, accounting, trading, and business analysis internships for high schoolers "
    "(financial services, banking, investment firms, business analysis, economics research)",
    "graphic design, UX/UI design, digital design, web design, and product design programs for high schoolers "
    "(portfolio-building, design thinking, prototyping, design competitions)",
    "architecture, landscape architecture, urban design, and architectural visualization programs for high schoolers "
    "(design studios, CAD training, architecture competitions, built environment)",
    "fashion design, fashion business, apparel design, and fashion industry programs for high schoolers "
    "(design, merchandising, styling, fashion competitions, industry internships)",
    "dance, ballet, contemporary dance, hip-hop, and choreography programs for high schoolers "
    "(intensive training, performances, choreography workshops, dance companies)",
    "culinary arts, pastry, cooking, and hospitality management programs for high schoolers "
    "(culinary bootcamps, chef mentorships, food service programs, kitchen experience)",
    "sports management, kinesiology, sports science, athletic coaching, and sports medicine programs for high schoolers "
    "(sports science labs, coaching certification, athletic training, sports business)",
    "theology, religious studies, interfaith dialogue, and spiritual leadership programs for high schoolers "
    "(seminary prep, religious organizations, faith-based leadership, spiritual retreats)",
    "AI and machine learning immersive bootcamps, intensive training, and hands-on research opportunities for high schoolers "
    "(practical ML projects, neural networks, model training, AI applications — distinct from theoretical AI ethics programs)",
    "linguistics, language learning, language technology, computational linguistics, and translation programs for high schoolers "
    "(endangered language documentation, multilingual NLP, language science, translation studies)",
    "film, video production, documentary filmmaking, cinematography, and media arts programs for high schoolers "
    "(production skills, editing, storytelling, filmmaking competitions, media labs)",
    "music performance, composition, music production, music technology, and audio engineering programs for high schoolers "
    "(instrument training, composition workshops, recording studios, music competitions, sound design)",
]

SEATTLE_SEEDS = [
    "Seattle Public Library and King County Library System teen programs, teen "
    "advisory boards, or volunteer/leadership opportunities for high schoolers",
    "YMCA branches and community centers in the Seattle metro area (Seattle, "
    "Bellevue, Redmond, Tacoma) offering teen leadership, volunteering, or work-experience programs",
    "Seattle-area farmers markets and small local businesses (Pike Place Market, "
    "neighborhood farmers markets, local makers/shops) that could let a motivated high schooler set up "
    "a booth, run a project, or gain real hands-on experience — even if they don't explicitly market "
    "this to students. Explain concretely, in the summary, how a specific kind of student project "
    "could actually use this.",
    "Seattle Parks & Recreation youth boards, teen programs, or seasonal youth "
    "employment/leadership opportunities",
    "maker spaces, hackerspaces, or fabrication labs in the Seattle area open to "
    "teens (open workshop nights, teen memberships, project support)",
    "Seattle-area nonprofits offering skilled-volunteer roles for high schoolers "
    "(tutoring, tech support, design/marketing help) rather than generic one-off volunteering",
    "small businesses, startups, or local professionals in the Seattle area open "
    "to informal mentorship, shadowing, or apprenticeship-style arrangements with motivated high schoolers",
    "Seattle-area arts organizations, museums, or civic/government offices offering "
    "teen programs, internships, or youth councils",
]

# PHASE 1 — research. Deliberately asks for PROSE. This call's job is to search and to leave
# behind grounding metadata; structuring the answer is phase 2's job. Do not add an output
# format here.
RESEARCH_SYSTEM = """You are a meticulous researcher helping build a high-quality catalog of extracurricular \
opportunities (programs, internships, research, competitions, volunteer roles, journals, conferences) for \
high school students, for the app "Wingman". Today's date is {today}.

Search thoroughly with web_search for: {angle}

Hard rules — quality over quantity matters far more than hitting any target count:
- Only write up real opportunities you found actual evidence for via web_search. Never invent one, \
never pad the list to reach a certain size. If you found 2 excellent ones, write up 2 — do not add weak \
or speculative filler to look more thorough.
- Confirm (as best you can from what you found) that the opportunity is currently real/active — skip \
anything that looks defunct, or where you can't tell if it's still running.
- Reason about eligibility, grade range, and cost from what you actually find on the page — never guess.

Write your findings as notes, one opportunity at a time, each starting with its name in bold. For each, \
cover: the running organization, what it actually is (2-4 sentences of real detail), who is eligible \
(grades, international vs domestic), cost, whether it is in-person or remote, what time of year it runs, \
the US state if it is location-specific, the subject areas it covers, and a contact email if you found a \
real one on the site. Say plainly when you could not establish something rather than filling it in."""

# PHASE 2 — extraction. Strict JSON, NO search tool. The resolved source URLs are supplied
# rather than requested, because asking a model for a URL is what produced the 26% dead-link
# batch: given a URL it can only copy, it copies; asked to recall one, it reconstructs.
EXTRACT_SYSTEM = """You convert research notes about high-school extracurricular opportunities into strict JSON. \
You do not search and you do not add opportunities that are not in the notes.

Respond with ONLY a raw JSON array, no markdown fences, no preamble, no text after the array. Each \
element must match exactly this schema (use null for any field the notes don't establish):
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
naming the actual field, skill, or activity"], \
"contact_email": "a real contact email address for the program if the notes contain one, else null — \
never guess or construct one"}}

Rules for "url", which matter more than anything else here:
- A list of SOURCE PAGES is supplied below. These are the pages that were actually retrieved. \
For each opportunity, copy the matching URL from that list VERBATIM, character for character.
- If no source page corresponds to an opportunity, use whatever URL the notes give, or null. \
NEVER construct, complete, guess or pattern-match a URL — a plausible-looking wrong URL is worse \
than no URL at all, because it looks correct to a reviewer.
- "type" must be one of the seven listed values. Pick the best fit from the notes. Do not invent \
a different value and do not leave it null."""

SEATTLE_ADDENDUM = """

This is a hyperlocal, creative-reasoning sweep: the org itself may not describe this as a "high school \
opportunity" at all. Your job is to actively reason about whether a motivated high schooler could turn it \
into one — e.g. a student building an app could use a local farmers market booth to get beta customers. \
That's just one example, not a template — do not force every result into that same framing. In your notes, \
concretely explain *why and how* a high schooler could actually use this one, specific to what you found, \
not generic filler. If you can't come up with a genuinely concrete, specific reason, leave it out."""


def next_id_generator(existing_ids):
    nums = [int(i[2:]) for i in existing_ids if i.startswith("ec") and i[2:].isdigit()]
    n = (max(nums) if nums else 18220) + 1
    while True:
        yield f"ec{n}"
        n += 1


def clean_value(value, valid_set):
    return value if value in valid_set else None


def _name_key(text):
    """Lowercase alphanumeric words, with any parenthetical/after-colon tail dropped."""
    head = re.split(r"[(:—–]", text or "", maxsplit=1)[0]
    return [w for w in re.findall(r"[a-z0-9]+", head.lower()) if w not in _NAME_STOPWORDS]


def spans_for_name(name, spans):
    """The grounding spans that describe `name`, most specific first.

    Deliberately not an exact substring test. A candidate is routinely named
    "NASA Internship Programs (Summer 2027)" where its span reads "NASA Internship
    Programs:" — an exact match misses, and the row then keeps the model's remembered URL
    even though the retrieved one was sitting right there. Requires all of the name's
    significant words to appear in the span, so it stays strict about *which* opportunity
    while tolerating suffixes and punctuation.
    """
    words = _name_key(name)
    if not words:
        return []
    hits = []
    for span in spans:
        span_words = set(re.findall(r"[a-z0-9]+", (span.get("text") or "").lower()))
        if all(w in span_words for w in words):
            hits.append(span)
    # A span mentioning fewer other things is more likely to be about this one opportunity.
    hits.sort(key=lambda s: len(s.get("text") or ""))
    return [u for span in hits for u in span["urls"]]


def reconcile_url(model_url, resolved_urls, span_urls):
    """Decide the URL to store, and say why.

    Returns (url, flags). `resolved_urls` are every URL the search actually retrieved for
    this seed; `span_urls` are the ones grounding attributed to this opportunity's own span,
    which is why groundingSupports is worth parsing — with a list of eight opportunities and
    seven source pages, "these pages were consulted" cannot say which URL belongs to which.

    Preference order: a source URL attributed to this opportunity > the model's own URL if
    it is itself a retrieved page > a retrieved page on the same host > the model's URL,
    flagged as unsourced. The second rung applies whether or not grounding attributed a
    span: a URL the search actually returned is verified regardless of which opportunity's
    sentence cited it, and preferring an unrelated span URL over it is how third-party
    articles reached the catalog.
    """
    model_url = (model_url or "").strip()
    if span_urls:
        # Grounding tied a real page to this specific opportunity. Trust it over the
        # model's own text unless the model already agrees.
        if model_url and model_url in span_urls:
            return model_url, []
        if model_url and model_url in resolved_urls:
            # The model's URL is ITSELF one of the pages the search retrieved, so it is
            # not a remembered guess and needs no repair. A span URL from elsewhere is
            # often a third-party round-up that merely mentions the program: replaying
            # the 2026-08-23 batch, this branch had discarded the program's own verified
            # page for an SEO article 5 times (aimi.stanford.edu -> ladderinternships.com,
            # stemgateway.nasa.gov -> indigoresearch.org, nhsjs.com -> futureforward.app),
            # and swapped a real program page for a worse one on the same host in ~20
            # more. Replayed over all 166 rows it changes 33 and worsens none.
            return model_url, []
        if model_url and any(url_validate.same_host(model_url, u) for u in span_urls):
            return span_urls[0], [FLAG_URL_REPLACED]
        if not model_url:
            return span_urls[0], []
        return span_urls[0], [FLAG_URL_REPLACED]
    if not model_url:
        return "", []
    if model_url in resolved_urls:
        return model_url, []
    same = [u for u in resolved_urls if url_validate.same_host(model_url, u)]
    if same:
        # Right site, but not a page the search returned — the off-by-one-segment
        # signature of a remembered URL. Prefer the retrieved page.
        return same[0], [FLAG_URL_REPLACED]
    return model_url, [FLAG_URL_UNSOURCED]


def url_flags(check):
    """Review flags for one url_validate.check_url() result."""
    if not check:
        return []
    if check["status"] == url_validate.DEAD:
        return [FLAG_DEAD_LINK]
    if check["status"] == url_validate.UNVERIFIED:
        code = check.get("code")
        if isinstance(code, int):
            return [FLAG_BLOCKED_LINK.format(code=code)]
        return [FLAG_UNREACHABLE]
    return []


def _netloc(url):
    try:
        return urllib.parse.urlsplit(url or "").netloc
    except ValueError:
        return ""


# At most this many grounding siblings are fetched to find a proven alternative, so a candidate
# with many same-domain source pages can't blow the per-row fetch budget.
_MAX_SIBLING_FETCH = 3


def _first_proven_sibling(resolved_urls, url, name, org, timeout, require_landing=False):
    """A grounding source URL, on the org's domain, that title-proves — or None.

    require_landing skips low-value sub-pages (faq/apply/rules/PDF), used when we already have
    a working URL and only want to trade UP to the canonical landing page.
    """
    fetched = 0
    for sib in resolved_urls:
        if fetched >= _MAX_SIBLING_FETCH:
            break
        if not sib or sib == url or url_validate.is_content_mill(sib):
            continue
        if not url_validate.domain_matches_org(sib, org, name):
            continue
        if require_landing and url_dedupe.is_low_value_path(sib):
            continue
        fetched += 1
        verdict, _title = url_repair.title_proof_url(sib, name, org, timeout)
        if verdict is True:
            return sib
    return None


def _rescue_offsite(url, name, org, resolved_urls, timeout):
    """The program's own page for a candidate whose URL is a content mill or off-site article.

    Ladder: (a) another grounding URL on the org domain that title-proves, then (b) the primary
    program link ON the off-site page itself (listicles link out to the real page). None if
    neither yields — the caller keeps the URL and lets FLAG_OFFSITE stand.
    """
    sib = _first_proven_sibling(resolved_urls, url, name, org, timeout)
    if sib:
        return sib
    page, final = url_repair._fetch(url, timeout)
    if page:
        link, _title = url_repair.extract_primary_link(
            page, name, org, base_url=final or url, timeout=timeout)
        if link:
            return link
    return None


def resolve_url_truth(candidate, url, flags, resolved_urls, timeout):
    """Phase-2 URL truth (free HTTP). Improve the URL a row will be stored under:

      1. A content mill or off-site page-about-the-program is rescued to the program's OWN page
         (a proven grounding sibling, else the page's primary program link). FLAG_URL_RESCUED.
      2. A low-value sub-page (apply/faq/rules/PDF) yields to a proven canonical landing page
         when the grounding offers one.
      3. The final URL is title-proven; a proof FAILURE trades to a proven sibling, else lands
         FLAG_TITLE_UNPROVEN (never a rejection — a blocked fetch or "Algebra II" vs "Algebra 2"
         is the accepted false-negative cost). PDFs/non-HTML fail the proof.

    Returns (url, flags). Offsite/mill detection itself stays in the caller's free flag phase,
    so this only ever ADDS the rescued/unproven markers and swaps the URL — it never flags
    offsite (which would double up with the flag phase).
    """
    name = (candidate.get("name") or "").strip()
    org = (candidate.get("org") or "").strip()
    flags = list(flags)

    # 1. A content mill / off-site page-about-the-program is rescued to the program's own page.
    if url_validate.is_content_mill(url) or not url_validate.domain_matches_org(url, org, name):
        rescued = _rescue_offsite(url, name, org, resolved_urls, timeout)
        if rescued:
            was = url_dedupe.registrable_domain(_netloc(url)) or _netloc(url) or "another site"
            flags.append(FLAG_URL_RESCUED.format(domain=was))
            return rescued, flags
        # Couldn't rescue: keep the URL; the free flag phase adds FLAG_OFFSITE. No point
        # title-proving a page we already know is off the program's site.
        return url, flags

    # 2. On the org's own domain: trade a low-value sub-page (apply/faq/rules/PDF) for a proven
    #    canonical landing page when the grounding offers one.
    if url_dedupe.is_low_value_path(url):
        better = _first_proven_sibling(resolved_urls, url, name, org, timeout, require_landing=True)
        if better:
            return better, flags

    # 3. Title-proof the stored URL; a failure trades to a proven sibling, else flags (never a
    #    rejection — a blocked fetch or "Algebra II" vs "Algebra 2" is the accepted cost).
    verdict, _title = url_repair.title_proof_url(url, name, org, timeout)
    if verdict is False:
        better = _first_proven_sibling(resolved_urls, url, name, org, timeout)
        if better:
            return better, flags
        flags.append(FLAG_TITLE_UNPROVEN)
    return url, flags


def build_row(candidate, mint_id, source, url, flags):
    """One catalog row, or None if it has no name/url to be identified by.

    No seed category any more: `type` is the model's answer or nothing. `category` is not
    written at all — it is nullable and already NULL on 1139 of 1440 catalog rows, and
    nothing in the student-facing app reads it.
    """
    name = (candidate.get("name") or "").strip()
    if not name or not url:
        return None
    tags = candidate.get("subject_tags") or []
    if not isinstance(tags, list):
        tags = [tags] if tags else []
    # `type` is non-null on every one of the 1440 catalog rows, so an invalid one cannot
    # simply be left empty. Park it on the most common value to get the row inserted; the
    # CALLER attaches FLAG_NO_TYPE so a reviewer sets it properly before activating. Do not
    # re-derive a type from the seed here — that fallback is exactly what was removed.
    opp_type = candidate.get("type") if candidate.get("type") in VALID_TYPES else "Program"
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
        "eligibility": candidate.get("eligibility"),
        "grade_min": candidate.get("grade_min") if isinstance(candidate.get("grade_min"), int) else None,
        "grade_max": candidate.get("grade_max") if isinstance(candidate.get("grade_max"), int) else None,
        "cost": candidate.get("cost_detail"),
        "subject_tags": tags or None,
        "contact_email": clean_email(candidate.get("contact_email")),
        "is_active": False,
        "source": source,
    }


def research_seed(angle, addendum, today, gemini_key, args):
    """Phase 1. Returns (notes, usage, grounding, cost, attempts).

    Retries once on a zero-search response. Retrying rather than re-prompting is the only
    mitigation that works: the search decision is non-deterministic (seed 51 returned 0
    searches then 6 on an identical command), and a silent call is cheap because it pays no
    per-search fee.
    """
    system = RESEARCH_SYSTEM.format(today=today, angle=angle) + addendum
    user_content = f"Search now and write up what you find for: {angle}"
    cost = 0.0
    notes, usage, extra = "", {}, {}
    for attempt in (1, 2):
        notes, usage, extra = call_gemini(system, user_content, gemini_key, use_web_search=True,
                                          max_tokens=6000, timeout=args.timeout,
                                          max_searches=args.max_searches, return_grounding=True)
        cost += estimate_cost(usage)
        searches = (usage.get("server_tool_use") or {}).get("web_search_requests", 0)
        queries = (usage.get("server_tool_use") or {}).get("web_search_queries", [])
        for q in queries:
            print(f"     search: {q}")
        if searches:
            return notes, usage, extra.get("grounding") or {}, cost, attempt
        if attempt == 1:
            print("  [SILENT] no search invoked — retrying once (a silent call pays no "
                  "search fee, and the decision is non-deterministic)")
    return notes, usage, extra.get("grounding") or {}, cost, 2


def extract_candidates(notes, resolved_urls, gemini_key, args):
    """Phase 2. Notes + real URLs in, strict JSON out. No search, so no grounding to keep."""
    system = EXTRACT_SYSTEM.format(subjects=", ".join(VALID_SUBJECTS))
    source_block = "\n".join(f"- {u}" for u in resolved_urls) or "(none retrieved)"
    user_content = (f"SOURCE PAGES actually retrieved (copy these verbatim where they match):\n"
                    f"{source_block}\n\nRESEARCH NOTES:\n{notes}\n\n"
                    f"Return the JSON array now.")
    text, usage = call_gemini(system, user_content, gemini_key, use_web_search=False,
                              max_tokens=6000, timeout=args.timeout)
    candidates = extract_json(text)
    if not isinstance(candidates, list):
        candidates = [candidates] if candidates else []
    return candidates, estimate_cost(usage)


ATTRIBUTION_KEYS = ("seed_id", "found_via")


def _without(rows, keys):
    drop = set(keys)
    return [{k: v for k, v in r.items() if k not in drop} for r in rows]


def insert_rows(supabase_url, service_key, rows, review_by_id):
    """Insert with the review + attribution columns, degrading if either migration is pending.

    PostgREST rejects an entire insert on one unknown key, so a single missing column would
    mean the whole scrape wrote NOTHING — reading as "the agent found nothing" rather than
    "every insert 400'd". Two INDEPENDENT migrations can be missing here: the review columns
    (moderation_status/dup_candidates/quality_flags — user_submissions_schema.sql) and the
    attribution columns (seed_id/found_via — scraper_attribution_schema.sql). Either can be
    present without the other, so the ladder tries all four combinations widest-first and
    keeps the maximal set the DB actually supports — a live DB with both applied always takes
    the full path. Returns the tier that succeeded. `rows` carry seed_id/found_via on the base
    dict, so the attribution-dropping tiers strip them explicitly.
    """
    full = [{**row, **review_by_id.get(row["id"], {})} for row in rows]
    attempts = [
        ("full", full),                                 # both migrations present
        ("no-attribution", _without(full, ATTRIBUTION_KEYS)),  # review present, attribution not
        ("no-review", rows),                            # attribution present, review not
        ("minimal", _without(rows, ATTRIBUTION_KEYS)),  # neither present
    ]
    for i, (tier, payload) in enumerate(attempts):
        try:
            supabase_post(supabase_url, "opportunities", payload, service_key)
            return tier
        except Exception as e:
            if i == len(attempts) - 1:
                raise  # the minimal write is base columns only — a failure here is real
            print(f"[WARN] Insert tier '{tier}' failed ({e}); trying a narrower column set. "
                  f"Run user_submissions_schema.sql and scraper_attribution_schema.sql to "
                  f"keep review flags and seed attribution.")


def auto_disable_mined_seeds(supabase_url, service_key, ran_seeds):
    """After a run, switch off angles the ledger diagnoses mined-out or thin.

    The diagnosis is over an angle's LIFETIME verdicts (seed_ledger.diagnose), not just this
    run — an angle that added nothing new because every candidate was a dupe is exactly the
    thing to retire. Sample-guarded (>=10 found across >=2 runs) and reversible with one
    click in the console, which is what makes auto-disabling safe (operator decision
    2026-08-26). Only seeds that just ran are considered, and they were enabled by definition,
    so a manually-disabled angle is never touched. A mis-aimed or pipeline-limited angle is
    never disabled — its fix is a better angle or the URL/refind pipeline, not silence.

    Returns the list of (seed_id, reason) disabled. Every failure is logged and swallowed:
    retiring an angle must never abort a scrape that already spent money on real results.
    """
    ids = [s["id"] for s in ran_seeds if s.get("id") is not None]
    if not ids:
        return []
    id_list = ",".join(str(i) for i in ids)
    try:
        seed_rows = supabase_get(supabase_url, "scraper_seeds", {
            "select": "id,is_enabled,total_runs,total_found,total_added,total_dupes,total_cost",
            "id": f"in.({id_list})"}, service_key) or []
        opp_rows = supabase_get(supabase_url, "opportunities", {
            "select": "seed_id,moderation_status,moderation_reason,is_active",
            "seed_id": f"in.({id_list})"}, service_key) or []
    except Exception as e:
        print(f"[WARN] Skipping auto-disable — could not read the ledger ({e}). "
              f"Have you run scraper_attribution_schema.sql?")
        return []

    funnels = seed_ledger.build_seed_funnels(opp_rows, seed_rows)
    enabled_by_id = {s.get("id"): s.get("is_enabled") for s in seed_rows}
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    disabled = []
    for sid, funnel in funnels.items():
        if not funnel.get("auto_disable") or not enabled_by_id.get(sid):
            continue
        reason = seed_ledger.disable_reason(funnel)
        try:
            supabase_patch(supabase_url, "scraper_seeds", {"id": f"eq.{sid}"},
                           {"is_enabled": False, "disabled_reason": reason,
                            "disabled_at": now}, service_key)
        except Exception as e:
            # disabled_reason/disabled_at migration may be pending — still retire the angle,
            # just without recording why (the console degrades to a bare disable).
            print(f"  [WARN] Could not record the auto-disable reason for seed {sid} ({e}); "
                  f"disabling anyway. Run scraper_seeds_schema.sql to keep reasons.")
            try:
                supabase_patch(supabase_url, "scraper_seeds", {"id": f"eq.{sid}"},
                               {"is_enabled": False}, service_key)
            except Exception as e2:
                print(f"  [WARN] Could not disable seed {sid}: {e2}")
                continue
        disabled.append((sid, reason))
        print(f"  [AUTO-DISABLE] seed {sid}: {reason}")
    return disabled


def _url_rank(url, flags):
    """Phase-2 URL quality, higher is better: title-proven > not-low-value > shallower path.
    Uses the flags already computed for the row (FLAG_TITLE_UNPROVEN as the proof proxy) so it
    needs no extra fetch."""
    proven = 0 if FLAG_TITLE_UNPROVEN in (flags or []) else 1
    not_low = 0 if url_dedupe.is_low_value_path(url) else 1
    try:
        depth = len([s for s in urllib.parse.urlsplit(url or "").path.split("/") if s])
    except ValueError:
        depth = 99
    return (proven, not_low, -depth)


def collapse_intra_run_twins(rows, flags_by_id):
    """Collapse rows minted THIS run that are same-registrable-domain AND name_similarity >= 0.9
    down to the copy whose URL wins the Phase-2 ranking. Returns (kept_rows, collapsed), where
    collapsed is [{"loser","winner","name"}]. This is a LOOSER rule than the catalog dedupe and
    is applied to in-run rows ONLY — it never touches the real catalog, so a false collapse
    costs at most one freshly-scraped row, not a curated one."""
    kept, collapsed = [], []
    for row in rows:
        dom = url_dedupe.registrable_domain(_netloc(row.get("url")))
        twin = None
        for k in kept:
            if (dom and url_dedupe.registrable_domain(_netloc(k.get("url"))) == dom
                    and url_dedupe.name_similarity(row.get("name") or "", k.get("name") or "") >= 0.9):
                twin = k
                break
        if not twin:
            kept.append(row)
            continue
        if _url_rank(row.get("url"), flags_by_id.get(row["id"])) > \
                _url_rank(twin.get("url"), flags_by_id.get(twin["id"])):
            kept[kept.index(twin)] = row          # new row's URL wins; it replaces the twin
            collapsed.append({"loser": twin["id"], "winner": row["id"], "name": twin.get("name")})
        else:
            collapsed.append({"loser": row["id"], "winner": twin["id"], "name": row.get("name")})
    return kept, collapsed


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
                              "Observed runs stop well short of it (6 against caps of both 10 and "
                              "25), so raising it is not the lever it looks like.")
    parser.add_argument("--no-verify-urls", action="store_true",
                         help="Skip the free HTTP liveness check on every candidate URL. Only for "
                              "offline debugging — without it, dead links reach the review queue "
                              "unflagged, which is the failure this rewrite exists to fix.")
    # --timeout comes from add_agent_args at 280s: broad national seeds with multiple
    # search rounds routinely take longer than the 120s the other agents use.
    add_agent_args(parser, default_timeout=280)
    args = parser.parse_args()
    apply_timing(args, gemini=True)

    load_dotenv()
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    service_key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    # Optional: contact-email resolution is regex-first and works without it; a key only lets
    # the rare multi-address page be disambiguated by one cheap Haiku call.
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if not supabase_url or not service_key or not gemini_key:
        print("[ERROR] SUPABASE_URL / SUPABASE_SERVICE_KEY / GEMINI_API_KEY not set in .env.")
        sys.exit(1)

    # Seeds live in the scraper_seeds Supabase table so they can be edited from the admin
    # console; the module-level lists above are the fallback if that table is empty or
    # unreachable. See seeds_common.load_seeds().
    fallback = NATIONAL_SEEDS if args.mode == "national" else SEATTLE_SEEDS
    all_seeds = load_seeds(supabase_url, service_key, args.mode, fallback=fallback)
    seeds = select_seeds(all_seeds, seed_ids=args.seed_ids, seed_indices=args.seed_indices)

    # Preview: scope resolved, report and stop before the first (paid) Gemini call.
    if args.preview:
        emit_preview(len(seeds), "seeds",
                     [f"[{s.get('id')}] {s['angle'][:100]}" for s in seeds],
                     mode=args.mode,
                     seed_ids=[s.get("id") for s in seeds])
        return

    if not seeds:
        print("[OK] No seeds selected — nothing to do.")
        return

    addendum = "" if args.mode == "national" else SEATTLE_ADDENDUM
    today = datetime.date.today().isoformat()
    run_stamp = snapshot_stamp()
    # `source` stays DATE-only: a whole day's scrape groups under one source value and
    # existing rows carry that shape. The snapshot filename carries the seconds.
    source = f"scraper-{args.mode}-{datetime.date.today().strftime('%Y%m%d')}"

    print(f"[OK] Fetching existing catalog from Supabase for dedup...")
    # The whole table, rejected rows included — a rejected row is the permanent record
    # that a person said no, and its URL blocking re-submission is the point. (Operator
    # deletions from 2026-08 were backfilled as source='tombstone-backfill' rejected
    # rows, so the table is the ONLY dedupe memory. Never SQL-DELETE a row; reject it.)
    existing = supabase_get(supabase_url, "opportunities", {"select": "id,name,url"}, service_key)
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

    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_logs")
    os.makedirs(log_dir, exist_ok=True)

    inserted_rows = []
    review_by_id = {}
    rejected = []          # everything NOT inserted, with the reason and the raw candidate
    merged = []            # same-URL candidates merged into an existing row instead of inserted
    total_cost = 0.0
    raw_found = duplicates_skipped = invalid_skipped = errors = 0
    total_searches = silent_search_count = flagged_rows = dead_links = 0

    for seed in seeds:
        angle = seed["angle"]
        seed_label = f"id={seed['id']}" if seed.get("id") is not None else "fallback"
        print(f"\n[SEED {seed_label}] {angle[:90]}...")
        seed_found = seed_added = seed_dupes = 0
        seed_cost = 0.0
        try:
            notes, usage, grounding, phase1_cost, attempts = research_seed(
                angle, addendum, today, gemini_key, args)
            # Banked immediately: phase 2 raising must not erase money phase 1 already spent.
            total_cost += phase1_cost
            seed_cost = phase1_cost
            searches = (usage.get("server_tool_use") or {}).get("web_search_requests", 0)
            total_searches += searches
            silent = searches == 0
            if silent:
                silent_search_count += 1

            # Resolve the redirect URIs to real pages. Free HTTP, and the only place the
            # actual URL of a retrieved page exists — see gemini_common's SIXTH finding.
            resolved = url_validate.resolve_grounding_chunks(grounding)
            resolved_urls = [r["url"] for r in resolved if r.get("url")]
            spans = url_validate.support_urls_by_span(grounding, resolved)
            print(f"  -> phase 1: {searches} search(es) in {attempts} attempt(s), "
                  f"{len(resolved_urls)} source page(s) resolved, ${phase1_cost:.4f}"
                  + (" [SILENT: answered from memory]" if silent else ""))

            candidates, phase2_cost = extract_candidates(notes, resolved_urls, gemini_key, args)
            seed_cost = phase1_cost + phase2_cost
            total_cost += phase2_cost
            raw_found += len(candidates)
            seed_found = len(candidates)
            print(f"  -> phase 2: {len(candidates)} candidate(s), ${phase2_cost:.4f} "
                  f"(seed total ${seed_cost:.4f})")

            # Save the raw material. No run before 2026-08-23 kept any, which is why past
            # failures ("5 candidates, all invalid, $0.09") were undiagnosable afterwards.
            with open(os.path.join(log_dir, f"scraper_{run_stamp}_seed{seed.get('id')}.json"),
                      "w", encoding="utf-8") as f:
                json.dump({"angle": angle, "searches": searches, "attempts": attempts,
                           "queries": (usage.get("server_tool_use") or {}).get("web_search_queries", []),
                           "resolved_urls": resolved_urls, "notes": notes,
                           "candidates": candidates}, f, indent=2, ensure_ascii=False)

            # Resolve each candidate's URL first, so liveness is checked on the URL we will
            # actually store rather than the one the model typed.
            staged = []
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    invalid_skipped += 1
                    rejected.append({"reason": "not a JSON object", "raw": candidate})
                    continue
                name = (candidate.get("name") or "").strip()
                span_urls = spans_for_name(name, spans)
                url, flags = reconcile_url(candidate.get("url"), resolved_urls, span_urls)
                if not name or not url:
                    invalid_skipped += 1
                    rejected.append({"reason": "no name or no URL — a row cannot be identified "
                                               "without both", "raw": candidate})
                    continue
                # Phase-2 URL truth: rescue a content-mill/off-site URL to the program's own
                # page and title-prove what we store. Free HTTP, so it rides the same
                # --no-verify-urls switch as the liveness check.
                if not args.no_verify_urls:
                    url, flags = resolve_url_truth(
                        candidate, url, flags, resolved_urls, url_validate.DEFAULT_TIMEOUT)
                staged.append((candidate, url, list(flags)))

            checks = {}
            if staged and not args.no_verify_urls:
                checks = url_validate.check_urls([u for _, u, _ in staged])

            for candidate, url, flags in staged:
                flags += url_flags(checks.get(url))
                if url_validate.is_bare_domain(url):
                    flags.append(FLAG_BARE_DOMAIN)
                if url_dedupe.is_low_value_path(url):
                    flags.append(FLAG_LOW_VALUE)
                if (url_validate.is_content_mill(url)
                        or not url_validate.domain_matches_org(
                            url, candidate.get("org"), candidate.get("name"))):
                    flags.append(FLAG_OFFSITE)
                if silent:
                    flags.append(FLAG_NOT_SEARCHED)

                # Everything weaker than an exact/same-URL match becomes a hint on the row:
                # url_dedupe.py's measurements show URL-alone rejects shared application portals
                # and name-alone rejects 257 genuinely distinct catalog pairs.
                exact, dup_candidates = url_dedupe.find_duplicates(url, candidate.get("name"), existing)
                # Phase 3, via the one shared rule (classify_same_url) the harness also grades:
                # a same-URL candidate on a DEDICATED page is the same program -> merge into the
                # incumbent (Round 2: incumbent won 27/28); on a BARE DOMAIN it is rejected if the
                # name matches, else flagged as a possible distinct program on a shared homepage.
                action, target = classify_same_url(url, exact, dup_candidates)
                if action == "merge":
                    duplicates_skipped += 1
                    seed_dupes += 1
                    if args.dry_run or args.no_verify_urls:
                        merged.append({"id": target["id"], "from_name": candidate.get("name"),
                                       "into_name": target.get("name"), "changed": False,
                                       "dry": True})
                    else:
                        m = apply_merge(supabase_url, service_key, candidate, target["id"],
                                        url, url_validate.DEFAULT_TIMEOUT)
                        if m:
                            merged.append(m)
                    continue
                if action == "reject":
                    duplicates_skipped += 1
                    seed_dupes += 1
                    rejected.append({"reason": f"exact duplicate of {target.get('id')} "
                                               f"({target.get('name')})", "raw": candidate})
                    continue
                if action == "flag":
                    flags.append(FLAG_SHARES_HOMEPAGE.format(id=target["id"]))

                row = build_row(candidate, next(mint_id), source, url, flags)
                if not row:
                    invalid_skipped += 1
                    rejected.append({"reason": "row could not be built", "raw": candidate})
                    continue
                # Actively resolve the program's OWN contact email from its page instead of
                # only keeping one that happened to appear in the search notes. Reuses the
                # shared regex-first helper (a model call only on a page with several
                # addresses, so almost always free), rides the same free-HTTP switch as URL
                # truth, and is best-effort: a fetch failure keeps the notes value build_row
                # already set. This is why the standalone find_contact_emails.py is only a
                # backfill for OLD rows — new rows now arrive email-complete.
                if not args.no_verify_urls:
                    try:
                        email, email_cost, _m, _f = resolve_contact_email(
                            {"url": url, "name": row["name"], "org": row.get("org")}, anthropic_key)
                        seed_cost += email_cost
                        total_cost += email_cost
                        if email:
                            row["contact_email"] = clean_email(email)
                    except Exception:
                        pass
                # Attribute the row to the angle that found it, so the reviewer's verdict on
                # it becomes live feedback on this seed (seed_ledger.py). None for fallback
                # angles, which have no stable id — the correct "unknown" value.
                row["seed_id"] = seed.get("id")
                # build_row substitutes a placeholder type rather than failing; flagging it
                # is this loop's job, so the reviewer is told to set a real one.
                row_flags = list(flags)
                if candidate.get("type") not in VALID_TYPES:
                    row_flags.append(FLAG_NO_TYPE)
                if row_flags:
                    flagged_rows += 1
                if FLAG_DEAD_LINK in row_flags:
                    dead_links += 1
                review_by_id[row["id"]] = {
                    "moderation_status": "pending_review",
                    "dup_candidates": (dup_candidates or None),
                    "quality_flags": (row_flags or None),
                }
                # Join the dedupe pool immediately so a later seed in the same run doesn't
                # re-add what this one just found.
                existing.append({"id": row["id"], "name": row["name"], "url": row["url"]})
                inserted_rows.append(row)
                seed_added += 1
        except urllib.error.HTTPError as e:
            errors += 1
            print(f"  [ERROR] HTTP {e.code}: {e.read().decode(errors='replace')[:200]}")
        except Exception as e:
            errors += 1
            print(f"  [ERROR] {e}")

        # Record this angle's yield against the seed itself, so the console can rank angles
        # by rows-added-per-dollar and retire the ones that only return dupes. Dry runs
        # credit found/dupes/cost but never `added` — nothing was really added, and leaving
        # them out entirely is why every seed still read zero after a month of dry runs.
        record_seed_result(supabase_url, service_key, seed,
                           found=seed_found, added=0 if args.dry_run else seed_added,
                           dupes=seed_dupes, cost=seed_cost)
        # No explicit sleep: gemini_common enforces --min-delay (default 5s) between calls
        # and stamps its timestamp at call start, so a shorter sleep here did nothing.

    # Collapse in-run twins (two seeds finding the same program at different URLs on one site).
    flags_by_id = {rid: (rv.get("quality_flags") or []) for rid, rv in review_by_id.items()}
    inserted_rows, collapsed_twins = collapse_intra_run_twins(inserted_rows, flags_by_id)
    for c in collapsed_twins:
        rejected.append({"reason": f"intra-run duplicate of {c['winner']}",
                         "raw": {"id": c["loser"], "name": c.get("name")}})
    if collapsed_twins:
        print(f"[OK] Collapsed {len(collapsed_twins)} in-run twin(s) to their best-URL copy.")

    print(f"\n[SUMMARY] seeds run: {len(seeds)}, raw candidates: {raw_found}, "
          f"duplicates skipped: {duplicates_skipped}, invalid skipped: {invalid_skipped}, "
          f"errors: {errors}, new rows: {len(inserted_rows)} "
          f"({flagged_rows} flagged for review, {dead_links} with dead links), "
          f"searches: {total_searches}, silent seeds: {silent_search_count}/{len(seeds)}, "
          f"cost: ${total_cost:.4f}")

    review_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                f"scrape_review_{args.mode}_{run_stamp}.json")
    with open(review_path, "w", encoding="utf-8") as f:
        # `merged` is additive — dryrun_common reads only `inserted`; do not reshape those two.
        json.dump({"inserted": [{**r, "review": review_by_id.get(r["id"], {})} for r in inserted_rows],
                   "rejected": rejected, "merged": merged}, f, indent=2, ensure_ascii=False)
    applied_merges = sum(1 for m in merged if m.get("changed"))
    print(f"[OK] Wrote review snapshot: {review_path} "
          f"({len(inserted_rows)} inserted, {len(rejected)} rejected with reasons, "
          f"{len(merged)} merged into existing rows — {applied_merges} changed a field)")

    if args.dry_run:
        print(f"[DRY RUN] No opportunities were written. The run itself is still logged to "
              f"agent_runs (mode='{run_mode}') because it cost real money.")
    elif inserted_rows:
        insert_rows(supabase_url, service_key, inserted_rows, review_by_id)
        print(f"[OK] Inserted {len(inserted_rows)} row(s) into opportunities (is_active=false).")

    # Retire angles the ledger now diagnoses mined-out or thin. Live runs only: a dry run
    # never touches the catalog, and disabling an angle is a catalog config change. The
    # decision reads the reviewer's accumulated verdicts, so it is meaningful even on a run
    # that inserted nothing new.
    if not args.dry_run:
        auto_disable_mined_seeds(supabase_url, service_key, seeds)

    if run_id is not None:
        supabase_patch(supabase_url, "agent_runs", {"id": f"eq.{run_id}"}, {
            "finished_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "items_processed": len(seeds),
            "items_added": 0 if args.dry_run else len(inserted_rows),
            "errors": errors,
            "cost_usd": round(total_cost, 4),
            "total_web_searches": total_searches,
            "silent_search_count": silent_search_count,
            "notes": (f"raw_found={raw_found}, duplicates_skipped={duplicates_skipped}, "
                      f"invalid_skipped={invalid_skipped}, flagged={flagged_rows}, "
                      f"dead_links={dead_links}, "
                      f"max_searches={args.max_searches}, timeout={args.timeout}s"
                      + (f", would_have_added={len(inserted_rows)}" if args.dry_run else "")),
        }, service_key)
        print(f"[OK] Logged agent_runs id={run_id}.")

    print(f"\n[DONE] Review {review_path} before activating anything from source='{source}'.")


if __name__ == "__main__":
    main()

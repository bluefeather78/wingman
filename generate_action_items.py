#!/usr/bin/env python3
"""Action-item generator: for each active opportunity, work out the administrative steps a
student must actually complete to apply, and store them on the catalog row so every student
tracking that program gets the same, already-verified list instantly.

WHY THIS AGENT EXISTS
---------------------
Task lists used to be generated in the browser, per student, per add, by a single Gemini
call whose prompt said "infer these ... from what's typical for this type of opportunity."
It did exactly that. A student tracking NYU's User Experience Design summer program was
handed "Review prerequisite requirements (Algebra 2)" — a prerequisite on neither the
program's page nor its catalog row. A fabricated eligibility bar is the worst thing this
app can print: the student reads it, concludes they do not qualify, and never applies.

Three properties of the old design made that inevitable, and this agent inverts all three:

  1. It could not read the page. /api/messages attaches exactly one tool, googleSearch —
     no web_fetch, no urlContext — while the prompt instructed it to "Fetch this URL". The
     two tools it was told to use did not exist in the call. Here the page is fetched by the
     shared CAPTURE substrate (source_capture.fetch_and_capture, Claude web_fetch — reads
     PDFs and SPAs our urllib cannot; see DEADLINE_AND_TASK_PLAN.md §5a) and its content is
     handed to the extract call as text AND kept for the code-side verification below. The
     extract call itself still has no tools.
  2. Nothing could check the answer. The proxy returns only the response text; grounding
     and search counts are discarded, so no caller could tell a researched answer from a
     recalled one. Here every task is checked in code against the fetched page, and one
     that cannot be supported is demoted or dropped. See page_text.py for the two tests.
  3. It was invisible. Generated per student, seen by nobody else, so a fabrication
     surfaced only when someone complained. A catalog pass can be sampled, graded and
     counted before anyone sees it.

WHY THIS IS A SINGLE CALL AND NOT TWO PHASES
--------------------------------------------
check_reviews.py and check_deadlines.py are two-phase because demanding JSON collapses the
SEARCH rate (measured A/B: prose 4/4 searched, JSON 0/4). That reasoning does not transfer
here, because this agent never searches — it is handed the page. With no search to
suppress, the second phase would buy nothing and cost a second call per row. What made the
two-phase split necessary there is replaced here by something stronger: the grounding is
enforced in code rather than coaxed out of the model. If a graded sample ever shows the
model producing sloppy near-quotes that fail verification in bulk, add a prose phase then —
the verifier will make that visible as a demotion rate, which is exactly the signal to
watch. Do not add it speculatively.

COST SHAPE (revised 2026-08-26, substrate)
------------------------------------------
Two Claude calls per readable row now: a CAPTURE call (web_search to locate + web_fetch to
retrieve, ~$0.01/search) and the EXTRACT call (no tools) over the captured text. A row whose
capture fetches nothing still costs the capture call, then falls to a free local generic
checklist (no extract call) — the same honest, asserts-nothing fallback as before, just no
longer free, because reading now happens server-side. This is the accepted trade for reading
PDFs/SPAs the urllib fetcher rejected outright (which produced generic lists for free but with
no real coverage). One shared capture is meant to eventually feed BOTH the task and deadline
extracts (T6); until then tasks pay for their own capture.

THIS AGENT COSTS MONEY. Like the other five paid agents, never run it without fresh
explicit approval in chat. `--preview` is free and resolves the scope and price first.
"""
import argparse
import datetime
import json
import os
import random
import sys
import urllib.error

from agent_common import add_agent_args, apply_timing, emit_preview, snapshot_stamp
from claude_common import call_claude, extract_json, estimate_cost
import aggregators_common
# The shared program-source finder (T6) lives in check_deadlines — tasks and deadlines read the
# same pages the same way through it. Importing it here is one-directional (check_deadlines does
# not import this module), so there is no cycle.
import check_deadlines
import page_text
from supabase_common import load_dotenv, supabase_get, supabase_insert_one, supabase_patch

DB_AGENT = "action_item_generator"

# Requirements move with a program's application cycle, i.e. roughly annually — far slower
# than deadlines (weeks) and slower than reputation (months, STALE_AFTER_DAYS=30 in
# check_reviews). This only decides which rows a plain run re-pays for; there is no
# scheduler, every run is started by hand.
STALE_AFTER_DAYS = 90

# Model pin. Claude Haiku 4.5, promoted off gemini-3.5-flash-lite (2026-08-25) so tasks and
# deadlines — the product's two core surfaces — run on the same reasoning tier. The actual
# call model is claude_common.MODEL (call_claude takes no model arg); this constant is what
# the cost-attribution layer records as user_costs.model, so provider_for_model() resolves
# it to Anthropic. It must stay in step with claude_common.MODEL. This agent never searches
# and its quality is enforced by page_text's verification, not by model tier.
MODEL = "claude-haiku-4-5-20251001"

# A whole page in, five short tasks out. The ceiling is for the OUTPUT; page text is input.
MAX_TOKENS = 1400

MAX_ITEMS = 5

# Floor on a written checklist, topped up from the generic list. The prompt asks for 3-5 and
# the first graded sample (20 rows, 2026-08-24) came back with 37 tasks total — under two a
# row, several rows with exactly one. Told that an unsupported word destroys a step, the
# model plays safe and returns only what it can literally copy, so the honest list ended up
# useless: "Complete the following Google form." as a student's entire checklist is worse
# than the invented one it replaced, because at least that one looked like a plan.
#
# Asking the prompt more firmly is not enough on its own — a floor has to be guaranteed
# somewhere it cannot be talked out of. Top-up items are plain generic steps, which assert
# nothing and so cannot reintroduce the original problem.
MIN_ITEMS = 3

# ---------- write outcomes (mirrors check_deadlines.SOURCE_*) ----------
# Which of these a row carries is stored in action_items_source, so a reader can always
# tell how much the list is worth without re-deriving it.
SOURCE_VERIFIED = "page-verified"      # page read, model ran, at least one task survived
SOURCE_PAGE_EMPTY = "page-empty"       # page read, nothing specific survived verification
SOURCE_GENERIC = "generic-fallback"    # page unreadable — no model call was made
SOURCE_UNPARSED = "unparsed"           # page read, model output unreadable

# Only a real read of the page stamps action_items_checked_at. The other outcomes leave the
# row DUE, so the next run retries it — which is free when the page simply could not be
# fetched, and is the same reasoning check_deadlines uses for not stamping an
# unverified-fallback. Stamping a transient 403 would hide the row for 90 days.
STAMPING_SOURCES = {SOURCE_VERIFIED, SOURCE_PAGE_EMPTY}


# ---------- generic checklists ----------
# Used when the page cannot be read, and to top up a thin verified list. Every line here
# asserts NOTHING about any particular program — that is the whole contract of a generic
# task, and it is what makes writing these without reading anything defensible. Adding a
# line that names a course, a test, a document specific to one kind of program, or a fee
# amount would break it.
GENERIC_BY_TYPE = {
    "Competition": [
        "Read the official rules and eligibility page",
        "Register before the entry deadline",
        "Check the rules for team and entry requirements",
        "Check what has to be submitted, and in what format",
    ],
    "Conference": [
        "Read the submission or attendance guidelines",
        "Register before the deadline",
        "Check travel, dates and any attendance cost",
        "Ask a teacher or mentor to review your submission",
    ],
    "Journal": [
        "Read the author or submission guidelines",
        "Format your manuscript to their requirements",
        "Create a submission account if needed",
        "Ask a mentor to review before you submit",
    ],
    "Internship": [
        "Read the eligibility and application page",
        "Update your resume",
        "Ask a teacher or mentor for a recommendation",
        "Draft your statement of interest",
        "Submit the application before the deadline",
    ],
    "Research": [
        "Read the eligibility and application page",
        "Ask a teacher or mentor for a recommendation",
        "Draft your statement of interest",
        "Gather your transcript and any required records",
    ],
    "Volunteer": [
        "Read the sign-up and eligibility page",
        "Check whether a parent or guardian form is needed",
        "Complete the sign-up form",
    ],
}
# ORDER MATTERS: top_up() takes from the front, so the most universally-true steps go first
# and the presumptuous ones last. The second graded sample put "Draft your personal
# statement" on an IEEE conference row, because its catalog `type` is not "Conference" and it
# fell through to this list — a conference wants a paper, not a statement. Anything that
# assumes a particular KIND of application belongs below the fold here, where it is only
# reached when a row genuinely has nothing else.
GENERIC_DEFAULT = [
    "Read the eligibility and application page",
    "Note the application deadline",
    "Check what must be submitted, and in what format",
    "Ask a teacher or mentor for a recommendation",
    "Gather your transcript and any required records",
]


def generic_items(opp):
    """A per-type checklist, built locally and free. Every item is marked generic, which is
    what the Quest Log renders under 'Typical steps — confirm on the site'."""
    base = GENERIC_BY_TYPE.get((opp.get("type") or "").strip(), GENERIC_DEFAULT)
    url = opp.get("url") or None
    return [{"text": t, "url": url, "basis": "generic", "evidence": None} for t in base]


# ---------- the model call ----------

SYSTEM = """You read one extracurricular program's own web page and list the administrative \
steps a high-school student must complete in order to apply. The page text is given to you \
in full — you have no web access and must not rely on anything else.

Return 3-5 steps, each under 10 words, phrased as an instruction to the student.

A step is something the STUDENT DOES, written in your own words as an instruction: "Request \
a recommendation letter", "Submit your transcript", "Pay the application fee". It is NOT a \
copy of the sentence you are quoting, and it is NOT a link label, page heading, menu entry \
or button caption lifted off the page. "Read frequently asked questions", "Add course to \
shopping cart" and "Apply now" are page furniture, not application steps — do not return \
them. The quote and the step are different things: the quote is your EVIDENCE, the step is \
what the student must go and do about it.

Aim for 3-5 steps every time. If the page only supports one or two specific steps, make up \
the number with generic ones — that is what "generic" is for, and a short list of real \
steps plus obvious ones is more use to a student than a single fragment.

EVERY step is one of exactly two kinds and you must label which:

- "basis":"page" — the step states something SPECIFIC to THIS program that the page text \
actually says. Set "evidence" to the exact sentence or phrase from the page text that says \
it, copied VERBATIM, character for character. Do not paraphrase, shorten, tidy, correct or \
translate it. Quotes are checked against the real page text by exact match and a step whose \
quote is not found is thrown away, so copying carelessly loses the step.
- "basis":"generic" — ordinary application logistics true of almost any program of this \
kind. Set "evidence" to null. A generic step must assert NOTHING specific about this \
program: "Draft your personal statement" is generic; "Draft the 500-word statement on your \
research goals" is not.

NEVER state a prerequisite, required course, test, score, GPA, age or grade limit, required \
document, fee amount, or eligibility condition that is not written in the page text in \
front of you. Not from memory, not from what programs like this usually require, not \
inferred from the program's name or subject. If the page does not say it, you do not know \
it. Every word of a step is checked against the page, so an invented detail does not merely \
go unproven — it destroys the step it appears in.

Inventing a requirement is the worst thing you can do here. A student who reads a \
prerequisite they do not meet will not apply, and they may have been perfectly eligible. A \
short, dull, obvious list is a much better outcome than a confident wrong one. When in \
doubt, drop the specific detail and keep the plain instruction: "Review the eligibility \
requirements" is always safe.

Keep every step tactical and administrative — the logistics of applying. Never advise on \
the student's own project or its substance; you do not know what they are working on.

For "url", give the most specific page the student would go to for that step (application \
portal, registration form, payment page) ONLY if that URL appears in the page text. \
Otherwise null. Never construct or guess a URL path.

Respond with ONLY a raw JSON object, no markdown fences, no preamble, no text after it:
{"action_items":[{"text":"step, under 10 words","url":"url from the page text, or null",\
"basis":"page or generic","evidence":"verbatim quote from the page text, or null"}]}"""


def build_user_content(opp, text):
    eligibility = opp.get("eligibility")
    known = [f"Program: {opp.get('name', '')}", f"Organization: {opp.get('org') or ''}",
             f"Type: {opp.get('type') or ''}"]
    if eligibility:
        # Context, not proof. Said explicitly because the model will otherwise happily quote
        # our own note back as "evidence" from the page — which would make a curated field
        # launder itself into a page-verified claim. The verifier would catch it (the quote
        # is not in the page text) but the instruction saves the round trip.
        known.append(f"Our catalog's eligibility note (OUR note, NOT from the page — never "
                     f"quote this as evidence): {eligibility}")
    return ("\n".join(known) + "\n\nPAGE TEXT BEGINS\n" + text + "\nPAGE TEXT ENDS\n\n"
            "List the administrative steps per the schema. Quote only from the page text "
            "above.")


# ---------- verification (free, and the actual guarantee) ----------

def _new_stats():
    return {"proposed": 0, "dropped": 0, "demoted": 0, "page_backed": 0, "generic": 0,
            "dropped_eligibility": 0}


def verify_items(raw_items, opp, sources):
    """(kept, stats). The single place a model's proposal becomes a stored task, now over the
    CAPTURED sources (list of source_capture.CapturedSource) rather than one urllib page.

    Three code-side gates, all applied to EVERY item regardless of what the model labelled it:
      * claim_is_supported  — words not present in ANY captured page → task DROPPED.
      * quote_is_on_page    — a "page" task whose quote is not on any captured page → DEMOTED
        to generic (its words are all on some page, it just did not prove the sentence). The
        SOURCE that carries the quote sets the task's tier (official / trusted / pending), so
        "which page said it" and "how far to trust that page" are decided together.
      * eligibility gate (T3) — a page-backed task that STATES an eligibility condition
        (is_eligibility_claim) is kept ONLY at official tier; at trusted/pending it is DROPPED,
        not demoted (a false prerequisite with a citation is the original harm). A blocked
        source backs nothing and its claims are dropped outright.
    """
    kept = []
    stats = _new_stats()
    name, org = opp.get("name") or "", opp.get("org") or ""
    combined = "\n\n".join(s.text for s in sources if s.text)
    for it in raw_items:
        if not isinstance(it, dict):
            continue
        task = (it.get("text") or "").strip()
        if not task:
            continue
        stats["proposed"] += 1
        supported, _missing = page_text.claim_is_supported(task, combined, name, org)
        if not supported:
            stats["dropped"] += 1
            continue
        evidence = it.get("evidence")
        evidence = evidence.strip() if isinstance(evidence, str) and evidence.strip() else None
        basis, tier, src = "generic", None, None
        if it.get("basis") == "page" and evidence:
            # The SOURCE whose text actually carries the quote — that is what decides the tier.
            src = next((s for s in sources
                        if s.text and page_text.quote_is_on_page(evidence, s.text)), None)
            if src is not None:
                basis, tier = "page", src.tier
            else:
                stats["demoted"] += 1
                evidence = None
        elif it.get("basis") == "page":
            stats["demoted"] += 1
            evidence = None
        else:
            evidence = None

        if basis == "page":
            # A blocked source never backs a stored task; an eligibility claim survives only
            # at official tier. Both are DROPS, not demotions — the claim itself is what is
            # untrustworthy off-domain, not merely its citation.
            if tier == "blocked":
                stats["dropped"] += 1
                continue
            if tier != "official" and page_text.is_eligibility_claim(task):
                stats["dropped_eligibility"] += 1
                continue

        stats["page_backed" if basis == "page" else "generic"] += 1
        url = it.get("url")
        item = {
            "text": task,
            "url": url if isinstance(url, str) and url.startswith("http") else None,
            "basis": basis,
            "evidence": evidence,
        }
        if basis == "page" and src is not None:
            # Provenance the client renders as the trust gradient (P7) and the serve path
            # filters on (pending/blocked withheld). Absent on generic items by design.
            item["source_tier"] = tier
            item["source_url"] = src.url
            item["source_domain"] = src.domain
        kept.append(item)
        if len(kept) >= MAX_ITEMS:
            break
    return kept, stats


def top_up(kept, opp):
    """Pad a thin verified list to MIN_ITEMS with generic steps, skipping anything the
    verified list already covers. Verified items always come first — the point of the split
    is that a student reads the proven steps before the boilerplate."""
    if len(kept) >= MIN_ITEMS:
        return kept[:MAX_ITEMS]
    have = {page_text.normalize_for_match(i["text"]) for i in kept}
    # Also treat a generic line as covered when the verified list already uses its main verb
    # and object — "Submit the online application" should not sit under "Complete the online
    # application". Cheap approximation: overlap on the distinctive-plus-verb token set.
    have_tokens = [set(page_text._tokens(i["text"])) for i in kept]
    out = list(kept)
    for cand in generic_items(opp):
        if len(out) >= MIN_ITEMS:
            break
        norm = page_text.normalize_for_match(cand["text"])
        if norm in have:
            continue
        toks = set(page_text._tokens(cand["text"]))
        if any(len(toks & t) >= max(2, min(len(toks), len(t)) - 1) for t in have_tokens):
            continue
        out.append(cand)
    return out[:MAX_ITEMS]


# D3 (G-task-1b) — the furniture / navigation-CTA vocabulary. A page-backed task whose only
# distinctive content is newsletter / social / donate vocabulary is a nav label, not an
# application step: "Sign up for emails from us" (ec18244) is the canonical case — a real quoted
# phrase that passes both verifier gates yet is furniture. Singularized to match
# distinctive_tokens' output.
FURNITURE_TOKENS = {page_text._singular(t) for t in (
    "email", "emails", "newsletter", "subscribe", "subscription", "sign", "signup", "follow",
    "social", "facebook", "twitter", "instagram", "linkedin", "youtube", "tiktok", "donate",
    "donation", "share", "learn", "updates", "notified", "notify", "mailing", "list",
    # neutral function words that carry no claim — they must not, on their own, keep an
    # otherwise-furniture CTA from reading as furniture ("Subscribe to our newsletter").
    "our", "your", "their", "its", "this", "that", "here", "now", "more")}


def is_furniture_task(text, name="", org=""):
    """True when a page-backed task is a navigation label / CTA rather than an application step:
    it asserts nothing beyond the program's name (no distinctive tokens) OR its distinctive
    content is entirely furniture vocabulary. A single substantive token makes it a real step."""
    dt = page_text.distinctive_tokens(text, name, org)
    if not dt:
        return True
    return all(t in FURNITURE_TOKENS for t in dt)


class WriteDecision:
    def __init__(self, items, source, write, stamp):
        self.items = items
        self.source = source
        self.write = write
        self.stamp = stamp


def action_items_write_decision(kept, opp, page_ok, model_ok, existing):
    """The ONE place that decides what a check may write, shared by this agent's batch loop
    and the on-demand endpoint (app/services/action_items.py) so the two cannot drift —
    exactly the role deadline_write_decision() plays for dates.

    Four outcomes. Only a genuine read of the page stamps the row; every other outcome
    leaves it due so a later run retries, which costs nothing when the failure was a fetch
    that could not get through.
    """
    # Page read and understood. Whatever survived verification is the answer, even if that
    # is nothing specific — a page that states no requirements is a real finding, not a
    # failure, and it should not re-bill on every run.
    if page_ok and model_ok:
        if kept:
            # D3 (G-task-1b): a "page-verified" result whose only page-backed tasks are
            # navigation furniture/CTAs is a SHALLOW read — the pipeline landed on a marketing
            # homepage and never reached the real steps page (ec18244). Write the list (better
            # than nothing) but do NOT stamp, so a later, better-discovered pass retries instead
            # of being frozen behind the TTL. A single substantive task stamps normally.
            name, org = opp.get("name") or "", opp.get("org") or ""
            shallow = all(is_furniture_task(i.get("text", ""), name, org) for i in kept)
            return WriteDecision(top_up(kept, opp), SOURCE_VERIFIED, True, not shallow)
        return WriteDecision(generic_items(opp), SOURCE_PAGE_EMPTY, True, True)

    # Page read, model output unreadable. We know nothing new. Keep whatever the row already
    # has rather than replacing a verified list with a fallback, and do not stamp.
    if page_ok and not model_ok:
        if existing:
            return WriteDecision(existing, SOURCE_UNPARSED, False, False)
        return WriteDecision(generic_items(opp), SOURCE_UNPARSED, True, False)

    # Page could not be fetched. That is a fact about our HTTP client, never about the
    # program — check_links measured ~9% of this catalog 403ing a non-browser agent on pages
    # a student's browser loads fine. A generic checklist is the honest answer, and it must
    # not overwrite a previously verified one.
    if existing and any(i.get("basis") == "page" for i in existing):
        return WriteDecision(existing, SOURCE_GENERIC, False, False)
    return WriteDecision(generic_items(opp), SOURCE_GENERIC, True, False)


def _load_policy():
    """The operator's trusted-domain policy for tier-tagging captured sources; empty (all
    third-party → pending) when Supabase/env is not configured. Cached in aggregators_common."""
    return aggregators_common.get_policy(
        os.environ.get("SUPABASE_URL", "").rstrip("/"),
        os.environ.get("SUPABASE_SERVICE_KEY", ""))


def process_one(opp, api_key, timeout=None, full_capture=False):
    """Full pipeline for one row: CAPTURE (Claude web_fetch) → EXTRACT (no tools) → verify →
    decide. Returns (decision, cost, stats, fetch_reason). The capture is what reads a PDF/SPA
    the old urllib fetch could not; the extract and verification are unchanged in spirit, now
    running over the captured content and tier-tagging each task by the source that backed it.

    Both halves go through the SHARED finder (T6, check_deadlines.find_program_sources) so task
    discovery is the same thorough sub-page hunt deadlines do — the how-to-apply / FAQ / key-dates
    pages application steps hide on. `full_capture` (interactive path) ALSO runs the date ladder,
    so the finder's per-opportunity cache lets the deadline endpoint firing alongside read the
    program ONCE; the batch leaves it False (requirement pages only, cheaper).
    """
    policy = _load_policy()
    _notes, cost, _searches, _urls, _attempts, _reached, sources = \
        check_deadlines.find_program_sources(
            opp, api_key, want_dates=full_capture, want_requirements=True, policy=policy)
    reason = "ok" if any(s.text.strip() for s in sources) else "no-fetch"
    combined = "\n\n".join(s.text for s in sources if s.text)
    text_ok = bool(combined.strip())
    raw, model_ok, stats = [], False, _new_stats()
    if text_ok:
        # The call and the PARSE are in separate try blocks on purpose. Wrapping both in one
        # `except Exception: model_ok = False` hid a plain TypeError here (estimate_cost takes
        # the usage dict, not three numbers) and reported it as the model producing unreadable
        # output — a programming error wearing a model failure's clothes, while also throwing
        # away the cost of a call that had already been billed.
        out, usage = call_claude(SYSTEM, build_user_content(opp, combined), api_key,
                                 use_web_search=False, max_tokens=MAX_TOKENS,
                                 timeout=timeout)
        # Banked immediately, on top of the capture cost, before anything that can raise.
        cost += estimate_cost(usage)
        try:
            parsed = extract_json(out)
            if isinstance(parsed, dict) and isinstance(parsed.get("action_items"), list):
                raw = parsed["action_items"]
                model_ok = True
        except Exception as e:
            print(f"  [unparsed] {type(e).__name__}: {e}")
            model_ok = False
    kept = []
    if text_ok and model_ok:
        kept, stats = verify_items(raw, opp, sources)
    existing = opp.get("action_items") if isinstance(opp.get("action_items"), list) else []
    decision = action_items_write_decision(kept, opp, text_ok, model_ok, existing)
    return decision, cost, stats, reason


# ---------- batch ----------

SELECT = ("id,name,org,url,summary,type,eligibility,action_items,action_items_source,"
          "action_items_checked_at")


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--sample", type=int, help="Process a random N-row sample.")
    group.add_argument("--all", action="store_true",
                       help="Process every stale/unchecked active row (the default).")
    group.add_argument("--ids", nargs="+", metavar="ID",
                       help="Process these specific ids, ignoring staleness.")
    group.add_argument("--missing", action="store_true",
                       help="Only rows that have no action items at all yet.")
    parser.add_argument("--force", action="store_true",
                        help="Ignore the staleness filter and re-do every active row.")
    parser.add_argument("--dry-run", action="store_true",
                        help="No database writes — dumps a snapshot instead. Still calls "
                             "the paid API at full cost.")
    add_agent_args(parser, default_timeout=120)
    args = parser.parse_args()
    apply_timing(args, claude=True)

    load_dotenv()
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    service_key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not supabase_url or not service_key or not anthropic_key:
        print("[ERROR] SUPABASE_URL / SUPABASE_SERVICE_KEY / ANTHROPIC_API_KEY not set in .env.")
        sys.exit(1)

    params = {"select": SELECT, "is_active": "eq.true", "order": "id"}
    if args.ids:
        params["id"] = f"in.({','.join(args.ids)})"
    elif args.missing:
        params["action_items"] = "is.null"
    elif not args.force:
        cutoff = (datetime.datetime.now(datetime.timezone.utc)
                  - datetime.timedelta(days=STALE_AFTER_DAYS)).isoformat()
        params["or"] = (f"(action_items_checked_at.is.null,"
                        f"action_items_checked_at.lt.{cutoff})")

    print("[OK] Fetching catalog rows due for action-item generation...")
    try:
        candidates = supabase_get(supabase_url, "opportunities", params, service_key)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        if "action_items" in body:
            print("[ERROR] The action_items columns are missing. Run "
                  "action_items_schema.sql in the Supabase SQL editor first.")
            sys.exit(1)
        raise
    print(f"[OK] {len(candidates)} row(s) selected.")

    mode = "ids" if args.ids else ("missing" if args.missing else
                                   ("sample" if args.sample else "all"))
    items = candidates
    if args.sample:
        items = random.sample(candidates, min(args.sample, len(candidates)))

    if args.preview:
        # Free. Note the quoted cost is a ceiling, not an estimate of what will be spent:
        # a row whose page cannot be fetched makes no API call at all, and roughly one in
        # ten cannot. estimate_agent_cost() in ops/core.py averages real history instead.
        emit_preview(len(items), "rows", [o.get("name", "?") for o in items],
                     mode=mode + ("-force" if args.force else ""))
        return

    if not items:
        print("[OK] Nothing due.")
        return

    run_mode = mode + ("-force" if args.force else "") + ("-dryrun" if args.dry_run else "")
    run_row = supabase_insert_one(supabase_url, "agent_runs", {
        "agent": DB_AGENT,
        "mode": run_mode,
        "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }, service_key)
    run_id = run_row["id"] if run_row else None

    total_cost = 0.0
    updated = errors = 0
    totals = _new_stats()
    by_source = {}
    unreadable = {}
    snapshot = []

    for i, opp in enumerate(items):
        print(f"[{i + 1}/{len(items)}] {(opp.get('name') or '?')[:55]}...", end=" ")
        try:
            decision, cost, stats, reason = process_one(opp, anthropic_key, timeout=args.timeout)
            total_cost += cost
            for k in totals:
                totals[k] += stats.get(k, 0)
            by_source[decision.source] = by_source.get(decision.source, 0) + 1
            if reason != "ok":
                unreadable[reason] = unreadable.get(reason, 0) + 1

            if not decision.write:
                print(f"{decision.source}: nothing written, row stays due, ${cost:.4f}")
                continue

            if args.dry_run:
                snapshot.append({
                    "id": opp["id"], "name": opp.get("name"), "url": opp.get("url"),
                    "action_items": decision.items,
                    "action_items_source": decision.source,
                    "would_stamp": decision.stamp,
                    "fetch": reason, "stats": stats, "cost_usd": round(cost, 4),
                })
            else:
                now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
                patch = {
                    "action_items": decision.items,
                    "action_items_source": decision.source,
                }
                if decision.stamp:
                    patch["action_items_checked_at"] = now_iso
                supabase_patch(supabase_url, "opportunities",
                               {"id": f"eq.{opp['id']}"}, patch, service_key)
            updated += 1
            print(f"{decision.source}: {stats['page_backed']} page-backed, "
                  f"{stats['generic']} generic, {stats['dropped']} dropped, "
                  f"{stats['demoted']} demoted, ${cost:.4f}")
        except urllib.error.HTTPError as e:
            errors += 1
            print(f"[ERROR] HTTP {e.code}")
        except Exception as e:
            errors += 1
            print(f"[ERROR] {e}")

    print(f"\n[SUMMARY] rows: {len(items)}, written: {updated}, errors: {errors}, "
          f"cost: ${total_cost:.4f}")
    print(f"[SUMMARY] tasks proposed: {totals['proposed']}, kept page-backed: "
          f"{totals['page_backed']}, kept generic: {totals['generic']}, "
          f"DROPPED as unsupported: {totals['dropped']}, demoted (quote not on page): "
          f"{totals['demoted']}, dropped as off-domain eligibility: "
          f"{totals['dropped_eligibility']}")
    print(f"[SUMMARY] outcomes: {by_source}")
    if unreadable:
        # Named rather than silently folded into "generic", because this is the number that
        # says how much of the catalog this agent structurally cannot verify.
        print(f"[SUMMARY] pages we could not read: {sum(unreadable.values())} {unreadable}")
    if args.sample and items:
        per = total_cost / len(items)
        print(f"[PROJECTED] ~${per:.4f}/row -> all {len(candidates)} due rows "
              f"~${per * len(candidates):.2f} for a full pass.")

    if args.dry_run:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            f"action_items_dry_run_{snapshot_stamp()}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2, ensure_ascii=False)
        print(f"[OK] Wrote dry-run snapshot: {path}")
        print(f"[DRY RUN] No catalog rows were written. The run is still logged to "
              f"agent_runs (mode='{run_mode}') because it cost real money.")

    if run_id is not None:
        supabase_patch(supabase_url, "agent_runs", {"id": f"eq.{run_id}"}, {
            "finished_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "items_processed": len(items),
            "items_updated": updated,
            "errors": errors,
            "cost_usd": round(total_cost, 4),
            "total_web_searches": 0,
        }, service_key)
        print(f"[OK] Logged agent_runs id={run_id}.")


if __name__ == "__main__":
    main()

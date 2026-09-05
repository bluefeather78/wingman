#!/usr/bin/env python3
"""Page classifier — read a fetched page's FULL text and say what KIND of page it is.

The review queue fills faster than the operator can clear it. This is the stage that pre-sorts
and pre-justifies it: one no-search model call per candidate reads the chrome-stripped page and
returns one of four classes with an evidence quote, so a reviewer's decision becomes a five-second
spot-check instead of a cold read.

    program           -> one opportunity's own page              -> a catalog ROW (labelled)
    first_party_hub    -> an institution listing its OWN programs -> a same-domain hub lead
    third_party_hub    -> a blog/listicle naming OTHERS' programs -> an off-domain / names lead
    none               -> a non-opportunity page (or unreadable)  -> flagged (NOT dropped in v1)

**This deliberately revisits an idea the plan once rejected.** "An LLM classifier at the
search-results step" was rejected because at that point we hold the URL string and phase-1 prose,
NOT the pages. This classifier FETCHES the full page first and judges from its text — the missing
premise — and it is a SEPARATE no-search call, never folded into phase-2 extract (whose 6000-token
budget and silent truncation-repair are the other half of that rejection). See
docs/archive/SCRAPER_IMPROVEMENT_PLAN.md, "Session 2026-08-30".

Two design choices worth stating up front, both matching rules already load-bearing elsewhere:

- **The staleness drop is DETERMINISTIC CODE, not a prompt instruction** (`is_stale_page`). Dates
  are exactly where models fabricate (the whole deadline-checker history), so "the newest date on
  this page is >= 3 years old" is decided by a regex over the text, never asked of the model. A
  page with no date at all is KEPT — it cannot be proven stale (operator decision 2026-08-30).

- **Evidence must be on the page.** The model is told to quote a verbatim substring; a positive
  class whose quote is NOT found in the fetched text is marked `evidence_verified=False` and capped
  at low confidence. It is the same "quote or it didn't happen" bar `page_text.quote_is_on_page`
  already enforces for action items — here it keeps a confident-sounding hallucinated class from
  reading as proven.

**Conservative v1** (operator choice): the model's judgment never DROPS a would-be program row —
`none` is flagged and stays queued so its precision can be measured before it earns drop authority.
The only drop here is the deterministic date rule. Nothing auto-activates.

FREE to import and unit-test. The single model call is PAID (M9) and only fires when a run is
triggered; the prompt is MARQUEE (M8). Both are gated per docs/archive/SCRAPER_IMPROVEMENT_PLAN.md.
"""
import dataclasses
import datetime
import json
import re

import gemini_common
import page_text

# --- the four classes -----------------------------------------------------------------
CLASS_PROGRAM = "program"
CLASS_FIRST_PARTY_HUB = "first_party_hub"
CLASS_THIRD_PARTY_HUB = "third_party_hub"
CLASS_NONE = "none"
VALID_CLASSES = {CLASS_PROGRAM, CLASS_FIRST_PARTY_HUB, CLASS_THIRD_PARTY_HUB, CLASS_NONE}
# The three that assert something about the page and therefore owe an on-page evidence quote.
_POSITIVE_CLASSES = {CLASS_PROGRAM, CLASS_FIRST_PARTY_HUB, CLASS_THIRD_PARTY_HUB}

CONF_HIGH = "high"
CONF_MEDIUM = "medium"
CONF_LOW = "low"
VALID_CONFIDENCE = {CONF_HIGH, CONF_MEDIUM, CONF_LOW}
_CONF_RANK = {CONF_LOW: 0, CONF_MEDIUM: 1, CONF_HIGH: 2}

# What the caller (the scraper wiring, a later step) does with each verdict. Kept here, pure, so
# the routing is one testable decision rather than re-derived at every call site.
ROUTE_ROW = "row"                          # program, fresh -> build a catalog row
ROUTE_DROP_STALE = "drop_stale"            # program, but the newest date on it is >= 3y old
ROUTE_SAME_DOMAIN_LEAD = "same_domain_lead"  # first_party_hub -> mine its own site
ROUTE_OFF_DOMAIN_LEAD = "off_domain_lead"    # third_party_hub -> mine off-domain / harvest names
ROUTE_FLAG_NONE = "flag_none"              # none -> stays queued, flagged (v1 does NOT drop it)
ROUTE_UNREADABLE = "unreadable"            # could not read the page -> keep today's behaviour

# The deterministic staleness rule. "Drop a program page whose newest date is no later than three
# years ago" — so on 2026 a page whose latest year is <= 2023 is stale. Rolling, year-granular
# (the operator specified it in years, and to-the-day arithmetic buys nothing here).
STALE_MAX_AGE_YEARS = 3
# Years are read only inside a sane window. Below this a "2007" in an address or a citation is
# noise; above it a stray "2099" would keep a dead page alive forever. A real program lists at
# most a couple of cycles ahead, so anything past today+FUTURE_SLACK is ignored, not trusted.
_YEAR_MIN = 1990
_FUTURE_SLACK_YEARS = 2
_YEAR_RE = re.compile(r"(?<!\d)(19|20)\d{2}(?!\d)")

# Page text handed to the model. The hub miner truncates at 14000 chars for the same reason: the
# class of a page is decided by its top, and the tail is footer.
_MAX_PAGE_CHARS = 14_000
# A verbatim evidence quote shorter than this cannot be checked against the page with any
# confidence (every short phrase is a substring of something), so it counts as unverified.
_MIN_EVIDENCE_CHARS = 12

# MARQUEE M8 (MARQUEE_DECISIONS.md): this prompt is sent to a model. Any change to its wording is a
# marquee change — get Shama's approval first and make it its own dedicated commit. Written in the
# house style (define each class with YES/NO examples and counter-examples, never adjectives alone).
CLASSIFY_SYSTEM = """\
You are classifying ONE web page for a catalog of extracurricular opportunities for high school \
students (summer programs, internships, research programs, competitions, conferences, journals). \
You are given the page's URL and its readable text. Decide what KIND of page it is. Judge ONLY \
from the text provided — do not use outside knowledge about the program or the organization.

Return STRICT JSON and nothing else:
{"class": "...", "confidence": "high|medium|low", "evidence": "<verbatim quote from the page, \
under 160 characters>", "why": "<one short sentence>"}

The four classes:

- "program" — a page dedicated to ONE specific opportunity a high schooler can apply to or join. \
It describes that one thing: who it is for, when it runs, how to apply. A program page almost \
always carries an ACTION for the student — an "Apply", "Register", or "Enroll" button or link, an \
application DEADLINE, or "applications open/close" dates. A single clear apply-or-deadline call to \
action describing ONE opportunity is a strong signal for "program". A DEADLINE or apply action \
signals "program" only when the page presents ONE opportunity with ONE apply action; when a page \
shows the dates or deadlines of SEVERAL distinct offerings side by side, each linking to its own \
page, that is an INDEX — a hub — and the multiple separate detail links outweigh the inline dates.
  YES: a "Stanford AI4ALL Summer Program" page with dates and an "Apply Now" button.
  YES: a "Regeneron Science Talent Search" page with an entry deadline and rules.
  YES: a page that describes ONE opportunity in depth — its dates, who it is for, how to apply — \
even if it also names or links a related or sibling program. A mention of another program does \
not make it a hub.
  NO (this is a hub): a page titled "Summer Programs at Stanford" listing ten programs, each with \
its own separate "Learn more" or "Apply" link.
  NO (this is none): a tuition page, a professor's bio, a generic "Admissions" page.

- "first_party_hub" — a page whose PURPOSE is to LIST or INDEX MANY of an institution's OWN \
programs. Its body IS the list; it does not itself fully describe any single program — each one's \
real details live on a separate page it links to. The listed programs belong to whoever runs the \
page, and it has no single apply action of its own.
  YES: "CMU Pre-College — Academic Programs" listing 15 Carnegie Mellon summer programs.
  YES: a university's "Summer Opportunities for High School Students" index.
  YES: a page titled "Summer Writing Workshops" that lists an institution's OWN winter-online, \
summer-online, and residential sessions — each linking to its own detail/registration page — EVEN \
THOUGH it shows each one's dates and application deadline inline. A page indexing 2 or more distinct \
offerings, each with its own page, is a hub no matter how few, and no matter that their dates are \
listed.
  NO: a page about ONE program (that is "program").
  NO: a page that fully describes ONE program — its dates, eligibility, how to apply — and merely \
names or links a sibling program (that is "program", not a hub).
  NO: a page whose listed programs are mostly on OTHER organizations' sites (that is \
"third_party_hub").

- "third_party_hub" — a blog post, listicle, directory, magazine, or aggregator that lists or \
NAMES many programs run by OTHER organizations. The author is not the one running the programs; \
it is an article or directory ABOUT programs.
  YES: "20 Best Summer Research Programs for High Schoolers" on a college-admissions blog.
  YES: a directory naming 70 competitions, each run by a different organization.
  NO: an institution listing its own programs (that is "first_party_hub").

- "none" — anything else: a single NON-opportunity page (costs, FAQ, staff bio, a news article \
ABOUT a program rather than the program's own page, a login or portal shell), a page that is \
clearly not about programs a high schooler applies to, or a page you cannot actually read \
(mostly navigation, empty, or error text).
  YES: "Tuition & Fees", "Contact Us", a press release, a 404 page.

Rules:
- The ONLY difference between first_party_hub and third_party_hub is WHOSE programs are listed: \
the site's own programs (first) versus other organizations' programs (third). Decide it from the \
page text — who runs the listed programs.
- A hub's PURPOSE is to LIST or INDEX many programs — its body is the list, and each program's \
real details are on a page it links to. A page that DESCRIBES ONE opportunity in depth (its own \
dates, eligibility, how to apply) is a "program" EVEN IF it names or links a related or sibling \
program — a mention or a link is not a listing. Ask what the page is FOR: presenting one \
opportunity, or indexing many?
- "confidence" is "high" only when the page text plainly settles the class. If the text is thin, \
ambiguous, or you are guessing, say "low".
- "evidence" MUST be a verbatim substring of the page text. If you cannot quote the page, the \
class is "none" with low confidence.
"""


@dataclasses.dataclass
class Classification:
    """One page's verdict. `klass` is None only when the page could not be read at all."""
    klass: str = None
    confidence: str = CONF_LOW
    evidence: str = ""
    why: str = ""
    readable: bool = True
    evidence_verified: bool = False
    stale: bool = False
    latest_year: int = None
    cost: float = 0.0
    error: str = ""

    def route(self):
        return route_for(self)

    def flag(self):
        """A short, human-readable summary for the row's quality_flags / the run snapshot."""
        if not self.readable:
            return f"classify: unreadable ({self.error or 'no text'})"
        if self.klass is None:
            return f"classify: no verdict ({self.error or 'unparsed'})"
        stale = f"; STALE latest year {self.latest_year}" if self.stale else ""
        ev = "" if self.evidence_verified else "; evidence unverified"
        return f"classify: {self.klass} ({self.confidence}){ev}{stale}"


# --- the deterministic staleness gate (no model) --------------------------------------

def latest_page_year(text, today_year=None):
    """The newest plausible year mentioned on the page, or None if it names none. Pure.

    Only years in [1990, today+2] count: an older stray number is noise (an address, a citation)
    and a far-future one ("2099") would keep a dead page alive forever. A copyright footer's
    current year legitimately lifts this — which is the SAFE direction for a drop rule, because it
    keeps a live program rather than dropping it.
    """
    today_year = today_year or datetime.date.today().year
    ceiling = today_year + _FUTURE_SLACK_YEARS
    years = [int(m.group()) for m in _YEAR_RE.finditer(text or "")]
    years = [y for y in years if _YEAR_MIN <= y <= ceiling]
    return max(years) if years else None


def is_stale_page(text, today_year=None, max_age=STALE_MAX_AGE_YEARS):
    """(stale, latest_year). Stale when the newest date is <= today-max_age. Pure.

    A page with NO date is NOT stale — it cannot be proven old, and many evergreen program pages
    print no year (operator decision 2026-08-30: keep undated pages).
    """
    today_year = today_year or datetime.date.today().year
    latest = latest_page_year(text, today_year)
    if latest is None:
        return False, None
    return latest <= today_year - max_age, latest


# --- parsing the model's answer (pure) ------------------------------------------------

def _evidence_on_page(evidence, text):
    """True when the evidence quote really is a substring of the page, normalized. Pure."""
    e = page_text.normalize_for_match(evidence or "")
    if len(e) < _MIN_EVIDENCE_CHARS:
        return False
    return e in page_text.normalize_for_match(text or "")


def parse_classification(raw_text, page_text_str, today_year=None):
    """Turn the model's JSON string into a validated Classification. Pure — no network, no cost.

    Enforces the two things the prompt cannot guarantee on its own: a class outside the four is
    rejected, and a positive class whose evidence is not on the page is marked unverified and
    capped at low confidence (a confident-sounding but unquotable class must not read as proven).
    The staleness flag is computed here from the fetched text, never from anything the model said.
    """
    try:
        data = gemini_common.extract_json(raw_text)
    except (ValueError, json.JSONDecodeError):
        return Classification(klass=None, error="model output had no JSON")
    if not isinstance(data, dict):
        return Classification(klass=None, error="model output was not a JSON object")

    klass = (data.get("class") or "").strip().lower()
    if klass not in VALID_CLASSES:
        return Classification(klass=None, error=f"invalid class {klass!r}")

    confidence = (data.get("confidence") or "").strip().lower()
    if confidence not in VALID_CONFIDENCE:
        confidence = CONF_LOW
    evidence = (data.get("evidence") or "").strip()
    why = (data.get("why") or "").strip()

    verified = klass not in _POSITIVE_CLASSES or _evidence_on_page(evidence, page_text_str)
    if klass in _POSITIVE_CLASSES and not verified:
        # Keep the class (v1 measures its precision) but never let it claim high confidence on a
        # quote we cannot find. The reviewer sees `evidence unverified`.
        confidence = CONF_LOW

    stale, latest_year = (False, None)
    if klass == CLASS_PROGRAM:
        stale, latest_year = is_stale_page(page_text_str, today_year)

    return Classification(klass=klass, confidence=confidence, evidence=evidence, why=why,
                          evidence_verified=verified, stale=stale, latest_year=latest_year)


def route_for(c):
    """The single routing decision, given a verdict. Pure. Conservative v1 (see module docstring)."""
    if c is None or not c.readable:
        return ROUTE_UNREADABLE
    if c.klass == CLASS_PROGRAM:
        return ROUTE_DROP_STALE if c.stale else ROUTE_ROW
    if c.klass == CLASS_FIRST_PARTY_HUB:
        return ROUTE_SAME_DOMAIN_LEAD
    if c.klass == CLASS_THIRD_PARTY_HUB:
        return ROUTE_OFF_DOMAIN_LEAD
    # none, or an unparsed/invalid verdict on a page we COULD read: flagged, still queued.
    return ROUTE_FLAG_NONE


# --- building the request (pure) ------------------------------------------------------

def build_user_content(url, page_text_str, name_hint="", org_hint=""):
    """The user turn for one page. The name/org are our TENTATIVE guess and are labelled as such —
    they can be wrong, and the class must come from the page, not from them."""
    head = [f"URL: {url}"]
    if name_hint or org_hint:
        head.append(f"(Our tentative guess, unverified — judge from the page, not this: "
                    f"name={name_hint!r} org={org_hint!r})")
    head.append("")
    head.append("PAGE TEXT:")
    head.append((page_text_str or "")[:_MAX_PAGE_CHARS])
    return "\n".join(head)


# --- classification (the model call lives here; injectable for tests) -----------------

def classify_from_text(url, page_text_str, call, name_hint="", org_hint="", today_year=None):
    """Classify one page whose text is already in hand. `call(system, user) -> (text, usage)` is
    injected so this is testable with no network; production passes a call_gemini closure.

    Cost is banked from the usage the call returns BEFORE the parse, so a malformed answer never
    discards money already spent — the same discipline every paid agent here follows.
    """
    text, usage = call(CLASSIFY_SYSTEM, build_user_content(url, page_text_str, name_hint, org_hint))
    cost = gemini_common.estimate_cost(usage or {})
    try:
        c = parse_classification(text, page_text_str, today_year)
    except Exception as e:  # a parse must never lose the class silently, nor the cost above
        c = Classification(klass=None, error=f"parse raised: {type(e).__name__}")
    c.cost = cost
    return c


def classify_page(url, api_key, name_hint="", org_hint="", call=None, timeout=None,
                  today_year=None, allow_browser=False, max_tokens=800):
    """Fetch `url` and classify it. Returns a Classification. PAID (one no-search model call).

    A page we cannot read gets NO verdict (`readable=False`) and costs nothing — no model call is
    made. A blocked/JS/PDF fetch is a fact about our HTTP client, never about the page, so the
    caller keeps its existing behaviour for those (queue with the blocked flag). `allow_browser`
    opts into the headless-Chromium fallback (the M1 path) for JS shells; off by default.
    """
    text, reason, _final = page_text.fetch_page_text_resolved(
        url, timeout=timeout or page_text.DEFAULT_TIMEOUT, allow_browser=allow_browser)
    if not text:
        return Classification(klass=None, readable=False, error=reason or "no text")
    call = call or (lambda system, user: gemini_common.call_gemini(
        system, user, api_key, use_web_search=False, max_tokens=max_tokens, timeout=timeout))
    return classify_from_text(url, text, call, name_hint, org_hint, today_year)

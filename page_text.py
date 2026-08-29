#!/usr/bin/env python3
"""Fetch a program page as plain text, and decide whether a model's claim is actually
supported by it. Entirely free — plain HTTP, no API keys, no model. Shared by
generate_action_items.py (batch) and app/services/action_items.py (on-demand), so the
batch pass and a live student request cannot disagree about what counts as proven.

WHY THIS EXISTS
---------------
The Quest Log's task list was written by a model that was told, in the prompt, to fill
gaps with "what's typical for this type of opportunity". It did: a student tracking NYU's
User Experience Design summer program was handed "Review prerequisite requirements
(Algebra 2)". No such prerequisite appears on the program's page or in its catalog row —
"Algebra 2" is just what a STEM summer program usually asks for.

Rewriting the prompt is necessary and is not sufficient. The repo has learned this twice
already, in url_repair.py ("accept on proof, not similarity") and in the scraper's
grounding work ("a model-typed URL is not trustworthy anywhere in this repo"). A prompt is
guidance; only code is a guarantee. So every task a model proposes is checked here against
text we fetched ourselves, and one that cannot be supported is demoted or dropped.

THE TWO TESTS, and why both are needed
--------------------------------------
1. `claim_is_supported()` — every DISTINCTIVE word in the task text must appear somewhere
   on the page. This is the test that actually catches Algebra 2: strip the generic
   application vocabulary ("review", "requirements") and the program's own name, and
   "algebra" is left. It is not on nyu.edu's page, so the task cannot be kept.

   This runs on EVERY task regardless of what the model labelled it. Running it only on
   tasks the model called page-backed would leave the loophole wide open — the model can
   dodge verification simply by labelling an invented prerequisite "generic", which is the
   likeliest way this fails once the prompt starts asking for labels.

2. `quote_is_on_page()` — a task claiming to be page-backed must supply a verbatim quote
   that really is on the page. Test 1 says the words exist somewhere; this says the model
   read a specific sentence rather than assembling a plausible one from scattered words.

Failing test 1 DROPS a task (it asserts something we cannot support). Failing test 2 only
DEMOTES it to generic (the words check out, the proof does not) — the task is usually
still a reasonable thing to do, it just isn't a fact about this program, and the card
labels it accordingly. Demote where demoting is honest; drop only what is unsupportable.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
No fuzzy matching, no similarity ratio, no stemming beyond a plural 's'. url_repair.py
measured what a similarity threshold does to exactly this kind of judgement: at >= 0.72 it
accepted "Summer Research Immersion" as proof of "First-year Research Immersion". The
shared words are always the category and the differing word is the identity, which is
backwards for a ratio. The cost of being strict is false negatives — the page says
"Algebra II" and the model wrote "Algebra 2", so a real task gets demoted. That is the
right direction to be wrong in: a demoted task costs a student a line of italic text, an
accepted false one can stop them applying to a program they qualify for.
"""
import atexit
import html
import re
import threading
import unicodedata
import urllib.error
import urllib.request

import url_validate as uv

DEFAULT_TIMEOUT = 20

# Enough of a page to carry an eligibility/requirements section, which on university sites
# routinely sits well below the fold. url_repair reads 200KB for a <title>; we need more.
PAGE_BYTES = 600_000

# Cap on what reaches a model prompt. Tokens are the cost driver once search is out of the
# picture, and the requirements section is essentially never in the last quarter of a long
# page — that tail is navigation, related-programs lists and footers.
MAX_TEXT_CHARS = 24_000

_SCRIPT_RE = re.compile(r"<(script|style|noscript|svg|head)\b.*?</\1>", re.I | re.S)
# Site furniture. Measured on stsci.edu 2026-08-24: with these left in, 87% of the lines
# handed to the model were under 40 characters — search widgets, mega-menus, breadcrumbs,
# footers. That is not merely wasted input tokens, it actively produced BAD TASKS: told to
# quote the page verbatim, the model reached for the most quotable strings available, which
# were link labels, and wrote "Read frequently asked questions" and "Add course offering to
# shopping cart" as if they were application steps. Cleaning the input is what makes the
# verbatim-quote rule yield useful tasks rather than a transcription of the navbar.
_CHROME_RE = re.compile(r"<(nav|header|footer|aside|form|select|button|dialog)\b.*?</\1>",
                        re.I | re.S)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_BREAK_RE = re.compile(r"</(p|div|li|tr|h[1-6]|section|article|br)\s*>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")

# Prefer the page's own main-content region when it declares one. Nearly every modern site
# does, and it removes far more chrome than any tag blacklist can — the blacklist stays as
# the fallback for sites that mark nothing up.
_MAIN_RES = [
    re.compile(r"<main\b[^>]*>(.*?)</main>", re.I | re.S),
    re.compile(r"<article\b[^>]*>(.*?)</article>", re.I | re.S),
    re.compile(r"""<([a-z]+)\b[^>]*\brole\s*=\s*["']main["'][^>]*>(.*?)</\1>""", re.I | re.S),
]

# A line shorter than this, carrying no digit and no colon, is almost always a link label or
# a menu entry rather than something a program is telling an applicant. The digit/colon
# escape hatch is what keeps "Deadline: March 1" and "Fee: $1,850" — the shortest lines that
# genuinely do carry a requirement.
MIN_CONTENT_WORDS = 5


def fetch_page_text(url, timeout=DEFAULT_TIMEOUT, allow_browser=False):
    """(text, reason) -- the two-value form every existing caller uses.

    `fetch_page_text_resolved` adds the URL the fetch actually LANDED on. This wrapper keeps the
    old arity so adding that could not touch a single call site, the same way
    `call_gemini(return_grounding=True)` was added. `allow_browser` is the headless-browser
    fallback (see fetch_page_text_resolved) — default OFF, so no existing caller changes.
    """
    text, reason, _final = fetch_page_text_resolved(url, timeout, allow_browser=allow_browser)
    return text, reason


def fetch_page_text_resolved(url, timeout=DEFAULT_TIMEOUT, allow_browser=False):
    """(text, reason, final_url). `final_url` is where the request ENDED UP after redirects.

    A caller that decides what to spend money on needs this, because the address we asked for
    and the page we got are routinely different things. Measured on CMU: nine
    `/student-affairs/pre-college/academic-programs/<program>.html` links all redirect to the
    pre-college INDEX -- so deduping on the requested URL leaves nine candidates that are one
    page, and paying to extract each would buy nine rows describing the same thing.

    text is None when the page could not be read; reason names why so a run report can say
    'blocked' rather than leaving a silent hole.

    A failure here is NOT evidence about the program — it is evidence about our HTTP
    client. check_links.py measured ~9% of this catalog 403ing a non-browser agent plus 41
    rows failing TLS, all of them pages a student's browser loads fine. So the caller's
    correct response to None is "generate generic tasks only", never "this program has no
    requirements".

    `allow_browser` (default OFF): when the free plain-HTTP GET fails, retry through a headless
    Chromium (Playwright) that runs JS and presents a real fingerprint. Measured 2026-08-28 on
    the 329 catalog rows plain HTTP could not read: it recovers **156 (47%)** — the bot walls
    (403/202/429) and JS-rendered SPAs urllib cannot touch — lifting catalog fetchability from
    78% to ~88%. It is a strict ENHANCEMENT of "read the live page" (still the real page, never
    memory — MARQUEE M1 stays intact), and it is OPT-IN so the shipped server path never pays
    for or depends on Chromium. Playwright is an OPTIONAL install: if it is not present the
    fallback degrades silently to the plain-HTTP result, so the offline agents stay runnable
    stdlib-only. Kept OFF for the on-demand server path; turned ON only by the offline batch
    agents (refresh_opportunities.py).
    """
    text, reason, final = _fetch_urllib(url, timeout)
    if text is not None or not allow_browser:
        return text, reason, final
    # Plain HTTP failed and the caller allows the browser fallback. A bad/missing URL or a
    # non-HTML body (a PDF) is not something a browser can rescue, so don't spend a page load
    # on it; everything else (bot-wall http-*, empty-or-js SPA, TLS/connection error) is
    # exactly what the browser recovers.
    if reason in _NO_BROWSER_REASONS:
        return text, reason, final
    btext, breason, bfinal = _fetch_with_browser(url, timeout)
    if btext is not None:
        return btext, breason, bfinal
    # Browser also failed (or is unavailable): keep the ORIGINAL plain-HTTP reason, which is
    # the more informative one for a run report ("http-403" beats "no-browser").
    return text, reason, final


_NO_BROWSER_REASONS = {"no-url", "not-html"}


def _fetch_urllib(url, timeout):
    """The plain-HTTP GET — unchanged behaviour, extracted so the browser fallback can wrap it
    and so a caller passing allow_browser=False gets byte-identical results to before."""
    if not url or not isinstance(url, str) or not url.lower().startswith(("http://", "https://")):
        return None, "no-url", url
    final = url
    try:
        req = urllib.request.Request(url, headers={"User-Agent": uv.USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            final = getattr(r, "url", None) or url
            # Anything but a plain 200 is not a page we can quote from. nyu.edu answers a
            # bot wall with 202 and an EMPTY body, which would otherwise be reported as
            # "empty-or-js" and read as a JavaScript app rather than as a refusal.
            if r.status != 200:
                return None, f"http-{r.status}", final
            ctype = (r.headers.get("Content-Type") or "").lower()
            if "html" not in ctype and "xml" not in ctype and "text/plain" not in ctype:
                return None, "not-html", final
            raw = r.read(PAGE_BYTES).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return None, f"http-{e.code}", final
    except Exception as e:
        return None, f"error-{type(e).__name__}", final
    text = html_to_text(raw)
    if len(text) < 200:
        # A near-empty body is almost always a JavaScript-rendered page. Treating it as a
        # real read would let a model "quote" from nothing and have the quote pass, because
        # an empty haystack fails every check EXCEPT the ones that short-circuit on it.
        return None, "empty-or-js", final
    return text[:MAX_TEXT_CHARS], "ok", final


# ---------- headless-browser fallback (optional, offline agents only) ----------
# A single Chromium instance + context, created lazily on first use and reused across the
# agent's serial loop (a browser launch is ~1s; paying it per row would dominate). Playwright's
# sync API is single-thread-bound, which is fine: the ONLY caller that turns allow_browser on
# is refresh_opportunities.py, a single-threaded batch loop. The on-demand server path never
# sets allow_browser=True, so this code is never reached from a request thread.
_BROWSER_TIMEOUT_MS = 25_000
_browser_lock = threading.Lock()
_browser_ctx = None            # the reused BrowserContext, or None
_browser_unavailable = False   # latched True once, if Playwright can't import/launch


def _get_browser_context():
    global _browser_ctx, _browser_unavailable
    if _browser_unavailable:
        return None
    if _browser_ctx is not None:
        return _browser_ctx
    with _browser_lock:
        if _browser_ctx is not None:
            return _browser_ctx
        if _browser_unavailable:
            return None
        try:
            from playwright.sync_api import sync_playwright
            pw = sync_playwright().start()
            browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
            ctx = browser.new_context(user_agent=uv.USER_AGENT, locale="en-US",
                                      viewport={"width": 1280, "height": 900})
            # Skip images/media/fonts: we only ever read text, and they dominate load time.
            ctx.route("**/*", lambda route: route.abort()
                      if route.request.resource_type in ("image", "media", "font")
                      else route.continue_())
            atexit.register(_close_browser, pw, browser)
            _browser_ctx = ctx
            return _browser_ctx
        except Exception as e:
            # No Playwright installed, or Chromium not provisioned: degrade to plain HTTP for
            # the rest of the process. Offline agents stay runnable stdlib-only by this.
            _browser_unavailable = True
            print(f"[page_text] headless-browser fallback unavailable ({type(e).__name__}: {e}); "
                  f"plain HTTP only. `pip install playwright && playwright install chromium` to enable.")
            return None


def _close_browser(pw, browser):
    for close in (browser.close, pw.stop):
        try:
            close()
        except Exception:
            pass


def _fetch_with_browser(url, timeout):
    """(text, reason, final_url) via headless Chromium, or (None, reason, url) on failure.
    Returns exactly the same shape and thresholds as _fetch_urllib, so the caller cannot tell
    which path produced an 'ok' text."""
    ctx = _get_browser_context()
    if ctx is None:
        return None, "no-browser", url
    page = None
    try:
        page = ctx.new_page()
        nav_ms = int((timeout or DEFAULT_TIMEOUT) * 1000)
        resp = page.goto(url, timeout=min(nav_ms, _BROWSER_TIMEOUT_MS),
                         wait_until="domcontentloaded")
        # Give a client-rendered app a moment to populate; a timeout here is not fatal — many
        # pages never go fully idle (polling, analytics) yet have already rendered their body.
        try:
            page.wait_for_load_state("networkidle", timeout=6000)
        except Exception:
            pass
        status = resp.status if resp else 0
        final = page.url or url
        content = page.content()
    except Exception as e:
        return None, f"browser-error-{type(e).__name__}", url
    finally:
        if page is not None:
            try:
                page.close()
            except Exception:
                pass
    if status and status != 200:
        return None, f"http-{status}", final
    text = html_to_text(content[:PAGE_BYTES])
    if len(text) < 200:
        return None, "empty-or-js", final
    return text[:MAX_TEXT_CHARS], "ok", final


def html_to_text(raw):
    """Readable page CONTENT. Block-level tags become newlines so a bulleted requirements
    list does not run into one unreadable line — both the model and the quote match read
    better for it — and site furniture is removed, because what reaches the model here is
    the entire universe of things it is permitted to say."""
    s = _COMMENT_RE.sub(" ", raw or "")
    s = _SCRIPT_RE.sub(" ", s)
    s = _CHROME_RE.sub(" ", s)
    s = _BREAK_RE.sub("\n", _main_region(s))
    s = _TAG_RE.sub(" ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    lines, seen = [], set()
    for line in s.split("\n"):
        line = line.strip()
        if not line:
            continue
        # Deduplicate. Chrome repeats — a menu rendered once for desktop and again for
        # mobile — while real prose does not, and a repeated line inflates the input for no
        # added meaning. It also stops one stray nav label being "quotable" twice over.
        key = line.casefold()
        if key in seen:
            continue
        seen.add(key)
        if len(line.split()) < MIN_CONTENT_WORDS and not re.search(r"[\d:]", line):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _main_region(s):
    """The largest <main>/<article>/[role=main] block, or the whole document. Largest rather
    than first: some sites open a small decorative <article> ahead of the real one."""
    for rx in _MAIN_RES:
        blocks = [m[-1] if isinstance(m, tuple) else m for m in rx.findall(s)]
        if blocks:
            best = max(blocks, key=len)
            # Guard against a wrapper that matched almost nothing — a page whose <main>
            # holds only a heading would otherwise lose everything the fallback would keep.
            if len(best) > 500:
                return best
    return s


# ---------- normalization ----------

# Typographic pairs that differ between a page's HTML and a model's reproduction of it for
# no meaningful reason. Normalizing these is not fuzzy matching — the characters carry no
# information here, and leaving them in would fail honest quotes constantly (a curly
# apostrophe in "Bachelor's" is the single commonest cause).
_PUNCT_MAP = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
    "―": "-", "−": "-",
    " ": " ", " ": " ", " ": " ", " ": " ", " ": " ",
    "…": "...",
}


def normalize_for_match(s):
    """Case-folded, whitespace-collapsed, typographically normalized. Applied to BOTH sides
    of every comparison, so a page and a quote are always judged in the same alphabet."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", str(s))
    for a, b in _PUNCT_MAP.items():
        s = s.replace(a, b)
    s = re.sub(r"\s+", " ", s)
    return s.strip().casefold()


# ---------- test 2: is the quote really on the page ----------

# Below this a "quote" proves nothing: "apply", "grade 11" or "fee" appear on almost any
# program page, so a short fragment would let a fabricated claim borrow real words as
# proof. Measured against nothing in particular — it is a floor chosen so a quote has to be
# a clause, not a word.
MIN_QUOTE_CHARS = 24


def quote_is_on_page(quote, page_text):
    """True only if this exact clause (normalized) appears in the page we fetched."""
    q = normalize_for_match(quote)
    if len(q) < MIN_QUOTE_CHARS:
        return False
    return q in normalize_for_match(page_text)


# ---------- test 1: does the task assert anything the page doesn't say ----------

# Vocabulary that carries no program-specific claim. A task built only from these words is
# generic by construction and has nothing to verify: "draft your personal statement" says
# the same thing about every program in the catalog.
#
# Keep this list GENEROUS but never let a word in that could be the substance of a claim.
# "algebra", "calculus", "sat", "gpa", "citizen", "18" must all stay OUT — those are
# exactly the tokens that need proving. When unsure, leave a word out: the cost is a task
# demanding proof it could have skipped, which is the safe direction.
GENERIC_TOKENS = {
    # the act of applying
    "apply", "application", "applications", "applying", "applicant", "applicants",
    "submit", "submission", "submissions", "submitting", "register", "registration",
    "registering", "enroll", "enrollment", "sign", "signup", "log", "login", "create",
    "complete", "completing", "fill", "filling", "start", "begin", "finish", "send",
    "upload", "uploading", "download", "attach", "confirm", "check", "review", "read",
    "reviewing", "checking", "verify", "prepare", "preparing", "draft", "drafting",
    "write", "writing", "gather", "gathering", "collect", "request", "requesting",
    "ask", "contact", "reach", "out", "set", "setup", "add", "note", "track", "plan",
    "schedule", "book", "save", "watch", "look", "find", "visit", "go", "get", "make",
    "put", "keep", "have", "need", "needed", "take", "taking", "budget", "ensure",
    "sure", "double", "obtain", "secure", "provide", "include", "bring", "print",
    "scan", "choose", "select", "pick", "identify", "arrange", "organize",
    # the paperwork
    "form", "forms", "portal", "page", "site", "website", "link", "account", "profile",
    "essay", "essays", "statement", "personal", "letter", "letters", "recommendation",
    "recommendations", "recommender", "recommenders", "reference", "references",
    "transcript", "transcripts", "resume", "cv", "portfolio", "sample", "samples",
    "document", "documents", "documentation", "materials", "material", "paperwork",
    "copy", "copies", "photo", "id", "signature", "consent", "permission", "guardian",
    "parent", "parents", "waiver", "record", "records", "info", "information", "details",
    "question", "questions", "answer", "answers", "response", "responses",
    # money and time, in the abstract
    "fee", "fees", "cost", "costs", "payment", "pay", "paying", "price", "tuition",
    "financial", "aid", "scholarship", "deadline", "deadlines", "date", "dates", "due",
    "early", "regular", "final", "late", "time", "timeline", "day", "days", "week",
    "weeks", "month", "months", "year", "cycle", "session", "round",
    # the thing itself
    "program", "programs", "opportunity", "opportunities", "course", "camp", "internship",
    "competition", "contest", "conference", "journal", "workshop", "summer", "school",
    "student", "students", "your", "you", "yours", "the", "and", "for", "with", "from",
    "this", "that", "any", "all", "its", "their", "requirement", "requirements",
    "required", "require", "requires", "eligibility", "eligible", "criteria", "guidelines",
    "instructions", "rules", "process", "steps", "step", "next", "before", "after",
    "online", "official", "team", "group", "project", "work", "topic", "abstract",
    "paper", "manuscript", "entry", "entries", "attend", "attending", "interview",
    "orientation", "acceptance", "decision", "offer", "list", "mailing", "email",
    "updates", "notification", "notifications", "reminder", "update", "updated",
    "attendance", "attending", "travel", "author", "authors", "format", "formatting",
    "interest", "teacher", "teachers", "mentor", "mentors", "counselor", "advisor",
    "submitted", "written", "written", "sealed", "copy",
    # Plain grammar. These are here so an instruction phrased as ordinary English is not
    # forced to prove its function words against the page — "Check what must be submitted"
    # asserts nothing, and every word in it that is not already above is scaffolding.
    # Nothing that could be the SUBSTANCE of a requirement belongs in this group.
    "are", "was", "were", "has", "had", "will", "can", "may", "must", "should", "would",
    "could", "when", "where", "how", "who", "whom", "which", "what", "whether", "each",
    "every", "other", "another", "same", "some", "more", "most", "than", "then", "also",
    "just", "only", "own", "into", "about", "over", "under", "off", "yet", "still",
    "both", "either", "neither", "per", "via", "upon", "while", "during", "once",
    "again", "anything", "everything", "something", "there", "here", "such", "many",
    "much", "few", "least", "well", "already", "not", "but", "nor",
}

_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokens(text):
    return _WORD_RE.findall(normalize_for_match(text))


def _singular(tok):
    """A one-rule plural fold. Deliberately not a stemmer: "algebra"/"algebras" should
    match, "calculus"/"calculate" should not."""
    if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
        return tok[:-1]
    return tok


def distinctive_tokens(task_text, name="", org=""):
    """Words in the task that carry a program-specific CLAIM.

    The program's own name and organization are subtracted for the reason url_repair.py
    subtracts them from its title proof: a task saying "Register for the NYU UX program"
    is not asserting anything about NYU that needs verifying — it is naming the row. Without
    this subtraction every task would demand that the page repeat its own name, which is
    usually true and occasionally, pointlessly, not.
    """
    own = {_singular(t) for t in _tokens(f"{name} {org}")}
    out = []
    for tok in _tokens(task_text):
        s = _singular(tok)
        if s in GENERIC_TOKENS or tok in GENERIC_TOKENS:
            continue
        if s in own:
            continue
        if len(tok) < 3 and not tok.isdigit():
            continue
        out.append(s)
    return out


def claim_is_supported(task_text, page_text, name="", org=""):
    """(ok, unsupported_tokens). True when every distinctive word in the task appears on the
    page — i.e. the task is not asserting something we have no basis for.

    A task with NO distinctive tokens is generic by construction and passes trivially; that
    is the intended reading, not a hole. "Draft your personal statement" makes no claim
    about this program, so there is nothing for the page to support.
    """
    toks = distinctive_tokens(task_text, name, org)
    if not toks:
        return True, []
    hay = {_singular(t) for t in _tokens(page_text)}
    missing = [t for t in toks if t not in hay]
    return (not missing), missing


# ---------- eligibility-claim detector (T3, 2026-08-26) ----------
# Flags a task that STATES a specific eligibility CONDITION — a prerequisite, required course,
# test, score, GPA, age, grade level, or citizenship/residency — as opposed to the safe generic
# advice "review the eligibility requirements", which asserts no condition. The task pipeline
# DROPS such a claim unless it was read on the program's OWN page (official tier): an aggregator
# being wrong about a prerequisite is the original "Algebra 2" harm with a citation, and
# verification proves only that the source SAID it, not that it is true (DEADLINE_AND_TASK_PLAN
# decision 5). BIASED TOWARD FLAGGING per T3 — a false positive only drops one aggregator-tier
# logistics task, while a false negative lets a fabricated eligibility bar through, which is the
# exact failure this whole subsystem exists to prevent. It runs only on page-backed non-official
# tasks, so over-flagging never touches an official-tier or generic task.
_ELIGIBILITY_TOKENS = {
    "prerequisite", "prerequisites", "prereq", "prereqs",
    "gpa", "sat", "act", "psat", "toefl", "ielts", "gre",
    "citizen", "citizens", "citizenship", "resident", "residency", "residence",
    "visa", "national", "nationality", "permanent",
    "freshman", "sophomore", "junior", "senior", "undergraduate", "graduate",
    "algebra", "calculus", "geometry", "trigonometry", "precalculus", "trig",
}
_GRADE_RE = re.compile(
    r"\b(?:grade|grades|9th|10th|11th|12th|ninth|tenth|eleventh|twelfth)\b", re.I)
_AGE_RE = re.compile(
    r"\b(?:age|ages|years?\s+old|at\s+least\s+\d|under\s+\d|over\s+\d|"
    r"older\s+than|younger\s+than)\b", re.I)
_GPA_RE = re.compile(r"\b[0-4]\.\d\b")                       # a GPA-like decimal (3.5)
# Deliberately NO "must be/have" pattern: it is too ambiguous to separate an APPLICANT
# condition ("must be 16", "must have a 3.5 GPA") from plain logistics ("materials must be
# submitted", "must be completed online"), and it false-flagged generic checklist lines like
# "Check what must be submitted". The concrete signals below (courses, tests, scores, GPA,
# age, grade, citizenship) catch the real eligibility harms without that noise.


def is_eligibility_claim(task_text):
    """True when the task asserts a concrete eligibility condition (see the section note)."""
    t = task_text or ""
    if set(_tokens(t)) & _ELIGIBILITY_TOKENS:
        return True
    low = t.lower()
    if "advanced placement" in low:
        return True
    return bool(_GRADE_RE.search(low) or _AGE_RE.search(low) or _GPA_RE.search(low))


# ---------- date-on-page verification (P6c / T7, 2026-08-26) ----------
# The deadline analogue of quote_is_on_page: does a date the model reported actually appear on
# a page we fetched? Dates are written many ways, so this is date-aware rather than a string
# match. It is used ONLY to MARK a non-estimated date verified/unverified — NEVER to delete a
# date (an estimated/projected date is absent from every page BY DESIGN, and a matcher false
# negative must not lose a real deadline), so the bias is: require day+month together (a bare
# "15" proves nothing) but treat the year as optional, since program pages routinely write a
# date without its year in an already-dated context.
_MONTH_NAMES = ["january", "february", "march", "april", "may", "june", "july", "august",
                "september", "october", "november", "december"]
_MONTH_ABBR = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")


def date_is_on_page(date_iso, page_text):
    """True when the ISO date (YYYY-MM-DD) appears on the page in any common written form —
    'January 15, 2027', 'Jan 15', '1/15/2027', '15/01/27', or the ISO form itself."""
    m = _ISO_DATE_RE.match(str(date_iso or ""))
    if not m:
        return False
    year, mon, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= mon <= 12 and 1 <= day <= 31):
        return False
    hay = normalize_for_match(page_text)
    if not hay:
        return False
    name = f"(?:{_MONTH_NAMES[mon - 1]}|{_MONTH_ABBR[mon - 1]})"
    d, mm, y4 = str(day), str(mon), str(year)
    y2 = y4[2:]
    dd = rf"0?{d}(?:st|nd|rd|th)?"           # 15 / 15th, optional leading zero
    yr = rf"(?:,?\s+(?:{y4}|{y2}))?"          # optional trailing year, 4- or 2-digit
    sep = r"[/\-.]"
    year_alt = rf"(?:{y4}|{y2})"
    patterns = [
        rf"\b{name}\.?\s+{dd}(?!\d){yr}",                     # january 15, 2027
        rf"\b{dd}(?!\d)\s+{name}\.?{yr}",                     # 15 january 2027
        rf"\b0?{mm}{sep}0?{d}{sep}{year_alt}\b",             # 1/15/2027 (m/d/y, year required)
        rf"\b0?{d}{sep}0?{mm}{sep}{year_alt}\b",             # 15/1/2027 (d/m/y, year required)
        rf"\b{y4}{sep}0?{mm}{sep}0?{d}\b",                   # 2027-01-15 (iso-ish)
    ]
    return any(re.search(p, hay) for p in patterns)

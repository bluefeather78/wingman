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
import html
import re
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


def fetch_page_text(url, timeout=DEFAULT_TIMEOUT):
    """(text, reason). text is None when the page could not be read; reason names why so a
    run report can say 'blocked' rather than leaving a silent hole.

    A failure here is NOT evidence about the program — it is evidence about our HTTP
    client. check_links.py measured ~9% of this catalog 403ing a non-browser agent plus 41
    rows failing TLS, all of them pages a student's browser loads fine. So the caller's
    correct response to None is "generate generic tasks only", never "this program has no
    requirements".
    """
    if not url or not isinstance(url, str) or not url.lower().startswith(("http://", "https://")):
        return None, "no-url"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": uv.USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            # Anything but a plain 200 is not a page we can quote from. nyu.edu answers a
            # bot wall with 202 and an EMPTY body, which would otherwise be reported as
            # "empty-or-js" and read as a JavaScript app rather than as a refusal.
            if r.status != 200:
                return None, f"http-{r.status}"
            ctype = (r.headers.get("Content-Type") or "").lower()
            if "html" not in ctype and "xml" not in ctype and "text/plain" not in ctype:
                return None, "not-html"
            raw = r.read(PAGE_BYTES).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return None, f"http-{e.code}"
    except Exception as e:
        return None, f"error-{type(e).__name__}"
    text = html_to_text(raw)
    if len(text) < 200:
        # A near-empty body is almost always a JavaScript-rendered page. Treating it as a
        # real read would let a model "quote" from nothing and have the quote pass, because
        # an empty haystack fails every check EXCEPT the ones that short-circuit on it.
        return None, "empty-or-js"
    return text[:MAX_TEXT_CHARS], "ok"


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

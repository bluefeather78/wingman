#!/usr/bin/env python3
"""Contact-email discovery: find a program's own contact email, almost entirely by regex.

Shared by find_contact_emails.py (the standalone pass over the whole catalog) and
refresh_opportunities.py (which calls resolve_contact_email() per row so a plain metadata
refresh also fills this in — see that script's docstring for why its own Gemini call,
running with no web search, essentially never knows a specific program's contact address
from training data alone).

WHY THIS IS ALMOST ALL REGEX, NOT AI (same argument mailing_list_common.py makes for
signup forms, applied to a much simpler question):

  1. Fetch the row's URL, then a few /contact-page guesses, with urllib.      free
  2. Regex out mailto: links; fall back to emails in the page's visible text
     (script/style content stripped first, so an analytics snippet's tracking
     id — e.g. a Sentry DSN — can't be mistaken for an address).             free
  3. Exactly one plausible candidate -> use it directly. NO MODEL CALL.      free
  4. Zero candidates -> leave contact_email untouched. NO MODEL CALL.        free
  5. More than one candidate -> ONE gemini-3.5-flash-lite call to say which,
     if any, is this program's own address vs a shared institutional one.   ~$0.001

Step 5 exists for the same reason find_mailing_lists.py needs a model at all: a page can
carry several plausible addresses (a specific program contact plus a general admissions or
webmaster address), and regex has no way to tell them apart. Unlike that feature, a wrong
answer here costs a reader a bounced or misdirected draft email, not an unattended automated
subscribe — so there is deliberately no pending_review/verified lifecycle; this writes
straight to opportunities.contact_email.

PROVIDER (MARQUEE M9, changed 2026-08-29 with Shama's approval, its own dedicated commit):
step 5 runs on GEMINI (gemini-3.5-flash-lite), not Claude Haiku. It is a trivial no-web-search
classification ("which of these N addresses is this program's own") that either model does
equally well, so there was no technical reason for the Anthropic dependency. Moving it to
Gemini means it uses the key its only two callers ALREADY require (refresh_opportunities.py
and find_contact_emails.py both need GEMINI_API_KEY), so multi-candidate rows now resolve
without a second key. This is a paid-call provider swap — do not swap it back (or forward)
without Shama's approval and a dedicated commit.
"""
import html as html_module
import re
import urllib.parse

from agent_common import clean_email
from gemini_common import call_gemini, estimate_cost, extract_json
from mailing_list_common import fetch_page  # reused fetch plumbing

MAX_PAGES_PER_OPP = 5  # landing page + up to 4 contact-page guesses
CONTACT_MODEL = "gemini-3.5-flash-lite"  # cheap, no web search — matches refresh_opportunities

# Slugs tried at two levels below, in this order. "contact" and "contact-us" are by far
# the most common; "contactus" (no separator) catches older/static sites that don't use
# either — it comes last because it's the least frequent of the three.
CONTACT_SLUGS = ("contact", "contact-us", "contactus")


def candidate_urls(url):
    """The landing page, then likely contact-page guesses, same-directory and domain-root
    interleaved per slug (dir/contact, root/contact, dir/contact-us, ...).

    Same-directory guesses (e.g. .../dept/program/contact) matter because a program page
    usually lives several path segments deep, where a plain root-level guess mostly 404s.
    But interleaving, not directory-then-root in two separate blocks, is what actually
    matters here: trying every directory guess before any root one spends the whole
    MAX_PAGES_PER_OPP budget on one slug before a cheaper, often-correct root-level guess
    (university.edu/contact) is ever reached — measured directly, that ordering mistake
    cost real hits a prior version of this function had already found. Root-level guesses
    also naturally collapse into the same URL as the directory guess (and dedupe away) for
    any page already at or near the domain root, so interleaving costs nothing there.

    Deliberately NOT mailing_list_common.candidate_urls()'s path order: that list leads
    with /newsletter, /subscribe, /mailing-list because those pages are where a SIGNUP
    FORM lives, and a signup-form page routinely has no contact address on it at all.
    """
    urls = [url]
    try:
        parts = urllib.parse.urlparse(url)
        if parts.scheme and parts.netloc:
            root = f"{parts.scheme}://{parts.netloc}"
            # Force a directory-style base so urljoin appends rather than replacing the
            # program page's own last path segment (RFC 3986: a base with no trailing "/"
            # has that segment replaced by the relative reference, not extended). Built from
            # parts.path alone — not string-split on the whole URL — so a bare domain like
            # "https://www.jshs.org" (no path at all) resolves to "/" instead of stripping
            # into the "//" of the scheme and producing a hostless URL.
            path = parts.path or "/"
            if not path.endswith("/"):
                path = path.rsplit("/", 1)[0] + "/"
            base_dir = f"{root}{path}"
            for slug in CONTACT_SLUGS:
                urls.append(urllib.parse.urljoin(base_dir, slug))
                urls.append(root + "/" + slug)
    except Exception:
        pass
    # dict.fromkeys: dedupe while keeping the landing page first.
    return list(dict.fromkeys(urls))

_MAILTO_RE = re.compile(r'mailto:([^"\'?\s>]+)', re.I)
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_PLAIN_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# Mailbox local-parts that are never a program's own contact — worse to store than nothing.
_GENERIC_LOCALPARTS = {
    "noreply", "no-reply", "donotreply", "do-not-reply", "webmaster", "postmaster",
    "privacy", "abuse", "unsubscribe", "notifications", "mailer-daemon",
}
# Third-party infrastructure domains that occasionally leak into a page's markup or
# analytics snippets and would otherwise read as a plausible-looking address.
_GENERIC_DOMAIN_SUBSTRINGS = ("wixpress.com", "sentry.io", "example.com", "godaddy.com",
                              "domain.com", "yourdomain.com", "sentry.wixpress.com")


def _is_generic(email):
    local, _, domain = email.lower().partition("@")
    if local in _GENERIC_LOCALPARTS:
        return True
    return any(sub in domain for sub in _GENERIC_DOMAIN_SUBSTRINGS)


def _visible_text(html):
    """Page text with <script>/<style> content and all other tags removed.

    Stripping script/style *content* (not just the tags) matters: a bare tag-strip leaves
    inline JS behind as plain text, and analytics snippets routinely contain strings that
    match a bare email regex (a Sentry DSN looks exactly like user@host.tld) without being
    anything a person would recognise as a contact address.
    """
    html = _SCRIPT_STYLE_RE.sub(" ", html or "")
    return html_module.unescape(_TAG_RE.sub(" ", html))


def extract_emails(html):
    """Plausible contact-email candidates on one page, deduped, generic ones filtered out.

    Prefers mailto: links — an anchor with mailto: is a deliberate authoring choice — and
    only falls back to scanning visible text for bare addresses when the page has none.
    """
    mailto = []
    for m in _MAILTO_RE.findall(html or ""):
        addr = html_module.unescape(m).split("?", 1)[0].strip()
        if addr:
            mailto.append(addr)
    raw = mailto if mailto else _PLAIN_EMAIL_RE.findall(_visible_text(html))

    seen, out = set(), []
    for addr in raw:
        addr = clean_email(addr.strip().rstrip(".,;"))
        if not addr or addr.lower() in seen or _is_generic(addr):
            continue
        seen.add(addr.lower())
        out.append(addr)
    return out


def scan_pages(opp):
    """Fetch this opportunity's page (and a likely /contact subpage) and return the first
    page's worth of email candidates found, plus which URLs were actually reachable.

    Stops at the first page that yields a candidate, same reasoning as
    mailing_list_common.scan_pages: the landing page's own address is the better-attributed
    one, and a /contact page found afterwards is more likely to be a general front desk.
    """
    candidates, fetched = [], []
    for url in candidate_urls(opp["url"])[:MAX_PAGES_PER_OPP]:
        final_url, html = fetch_page(url)
        if not html:
            continue
        fetched.append(final_url)
        found = extract_emails(html)
        if found:
            candidates = found
            break
    return candidates, fetched


def build_system():
    return """You judge which of several candidate email addresses found on a web page is \
the CONTACT email for one SPECIFIC extracurricular program for high school students - not a \
shared address for the whole organization or institution hosting its page.

Many program pages live on a shared institutional site, so a page can carry both a specific \
program contact (e.g. "email the program director at...") and a general one (admissions@, \
info@, webmaster@) that answers for the whole site. Prefer the specific one when there is a \
real signal it belongs to this program; otherwise say none of them fits rather than guessing.

Respond with ONLY a raw JSON object, no markdown fences, no preamble, no text after it:
{"choice": <0-based index of the best candidate, or null if none is clearly this program's \
own contact>, "reason": "one sentence, under 20 words"}"""


def build_user_content(opp, candidates):
    lines = [
        f"Opportunity: {opp['name']}",
        f"Organization: {opp.get('org') or 'unknown'}",
        f"Page: {opp['url']}",
        "",
        "Candidate email addresses found on the page:",
    ]
    lines += [f"[{i}] {c}" for i, c in enumerate(candidates)]
    lines += ["", "Which one (if any) is the contact address for THIS specific program?"]
    return "\n".join(lines)


def resolve_contact_email(opp, api_key):
    """One opportunity -> (email or None, cost, used_model, pages_fetched).

    `api_key` is a GEMINI key (see the PROVIDER note in the module docstring — step 5 runs
    on Gemini, M9). It may be None or empty and every free-resolving row (0 or exactly 1
    candidate) still resolves; only the rare multi-candidate row is left unresolved rather
    than erroring. In practice both callers already require GEMINI_API_KEY, so the key is
    present and multi-candidate rows do get resolved.
    """
    candidates, fetched = scan_pages(opp)
    if not candidates:
        return None, 0.0, False, fetched
    if len(candidates) == 1:
        return candidates[0], 0.0, False, fetched
    if not api_key:
        return None, 0.0, False, fetched

    text, usage = call_gemini(build_system(), build_user_content(opp, candidates), api_key,
                              use_web_search=False, max_tokens=200, model=CONTACT_MODEL)
    cost = estimate_cost(usage)
    verdict = extract_json(text) or {}
    choice = verdict.get("choice")
    if not isinstance(choice, int) or not (0 <= choice < len(candidates)):
        return None, cost, True, fetched
    return candidates[choice], cost, True, fetched

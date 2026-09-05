#!/usr/bin/env python3
"""Mailing-list signup: provider detection and subscribe execution.

Shared by agents/find_mailing_lists.py (which detects a signup form and stores a *recipe* for it)
and server.py (which later replays that recipe for a real user). Kept in one module for
the same reason gemini_common is: the two halves must agree exactly on the shape of a
recipe, and a drift between "what we detected" and "what we POST" is invisible until a
student's subscribe silently goes nowhere.

DESIGN - why so little of this is AI:

Accuracy is the metric this feature is judged on, so the model is asked exactly one
question: *is this form the mailing list for THIS program, or for the institution
hosting it?* Everything else is deterministic:

  - Finding the form           -> regex over the page HTML (provider embeds are rigid)
  - Reading its fields         -> parse the <form> block
  - Building the POST          -> a per-provider adapter, below
  - Reading the response       -> a per-provider classifier, below

A model that hallucinates a plausible-looking Mailchimp endpoint produces a recipe that
fails silently for every user who ever taps the button. A regex cannot invent one.

SUPPORTED PROVIDERS, and why the list is short:

Only providers with a documented, key-free, public subscribe endpoint are here. That is
what makes an unattended POST honest - we can send it, and we can read a real success or
failure out of the reply.

  mailchimp   POST .../subscribe/post-json  -> {"result": "success"|"error", "msg": ...}
  substack    POST https://<pub>.substack.com/api/v1/free      -> 200 + JSON
  convertkit  POST https://app.convertkit.com/forms/<id>/subscriptions -> JSON status
  mailerlite  POST https://assets.mailerlite.com/jsonp/<a>/forms/<f>/subscribe -> JSON

Deliberately NOT supported:
  - beehiiv: its public embed posts through an iframe that carries a bot-check token. We
    cannot complete that without pretending to be a browser, so a beehiiv row stays a
    handoff ("open the signup page") rather than a subscribe we cannot verify.
  - Bare/self-hosted HTML forms: no success signal to read, frequently CSRF-guarded, and
    the failure mode is a 200 that did nothing. Excluded from the POC on purpose.
  - Anything CAPTCHA-guarded: excluded, and never worked around.

DOUBLE OPT-IN is the norm on every provider here. A "success" from any of them means the
provider accepted the address and (usually) sent a confirmation email - NOT that anyone
is on the list. Nothing in this repo can observe that confirmation, because we subscribe
the student's own address and have no mailbox to read. Every string this module returns
is worded accordingly, and callers must not upgrade them to "subscribed".
"""
import html as html_module
import json
import re
import urllib.error
import urllib.parse
import urllib.request

# A short, ordinary browser UA. Not evasion - several of these endpoints 403 a bare
# Python default UA, and we are making exactly the request the site's own form makes.
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

FETCH_TIMEOUT = 20
SUBSCRIBE_TIMEOUT = 20

# Every state a subscribe attempt can end in. `submitted` is the success case and is
# deliberately not called `subscribed` - see the double opt-in note above.
SUBSCRIBE_STATES = ("submitted", "already_subscribed", "failed", "handoff")

# Recipe statuses. Only `verified` is ever executed; discovery can never write it.
SIGNUP_STATUSES = ("pending_review", "verified", "rejected", "none")


# ---------- page fetching ----------

def fetch_page(url, timeout=FETCH_TIMEOUT):
    """GET a page as text. Returns (final_url, html) or (url, None) on any failure.

    Never raises: discovery runs over a catalog where dead links are ordinary, and one
    404 must not end a pass.
    """
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(2_000_000)  # 2MB ceiling; no signup form lives past that
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.geturl(), raw.decode(charset, errors="replace")
    except Exception:
        return url, None


# Subpages worth trying when the landing page has no form. Ordered by how often they
# actually carry one. Kept short: each is a real HTTP request against someone's site.
CANDIDATE_PATHS = ("/newsletter", "/subscribe", "/mailing-list", "/contact", "/join")


def candidate_urls(url):
    """The landing page plus a few conventional signup subpages on the same host."""
    urls = [url]
    try:
        parts = urllib.parse.urlparse(url)
        if parts.scheme and parts.netloc:
            root = f"{parts.scheme}://{parts.netloc}"
            urls += [root + p for p in CANDIDATE_PATHS]
    except Exception:
        pass
    # dict.fromkeys: dedupe while keeping the landing page first.
    return list(dict.fromkeys(urls))


# ---------- form extraction ----------

_FORM_RE = re.compile(r"<form\b[^>]*>.*?</form>", re.I | re.S)
_ACTION_RE = re.compile(r"""\baction\s*=\s*["']([^"']+)["']""", re.I)
_INPUT_NAME_RE = re.compile(
    r"""<(?:input|select|textarea)\b[^>]*\bname\s*=\s*["']([^"']+)["']""", re.I)
_TAG_RE = re.compile(r"<[^>]+>")


def extract_forms(html, page_url=""):
    """Every <form> on the page, as {action, inputs, text}.

    `text` is the form's visible text with tags stripped - that is what the model reads to
    judge whether the form belongs to this program or to the whole institution.
    """
    forms = []
    for block in _FORM_RE.findall(html or ""):
        action_match = _ACTION_RE.search(block)
        # Unescape before anything reads it: an action written &amp;-encoded in the
        # markup (which is correct HTML, and how Mailchimp's own embed ships) otherwise
        # parses as one query param named "amp;id" and the recipe comes out unpostable.
        action = html_module.unescape(action_match.group(1)) if action_match else ""
        if action and page_url:
            action = urllib.parse.urljoin(page_url, action)
        text = _TAG_RE.sub(" ", block)
        text = re.sub(r"\s+", " ", text).strip()
        forms.append({
            "action": action,
            "inputs": _INPUT_NAME_RE.findall(block),
            "text": text[:600],
        })
    return forms


# ---------- provider detection ----------

_MAILCHIMP_RE = re.compile(r"https?://([a-z0-9.-]+\.)?list-manage\.com/subscribe/post", re.I)
_SUBSTACK_HOST_RE = re.compile(r"https?://([a-z0-9-]+)\.substack\.com", re.I)
_CONVERTKIT_RE = re.compile(
    r"https?://app\.(?:convertkit|kit)\.com/forms/(\d+)/subscriptions", re.I)
_MAILERLITE_RE = re.compile(
    r"https?://assets\.mailerlite\.com/jsonp/(\d+)/forms/(\d+)/subscribe", re.I)


def _query_params(url):
    try:
        return {k: v[0] for k, v in
                urllib.parse.parse_qs(urllib.parse.urlparse(url).query).items() if v}
    except Exception:
        return {}


def _mailchimp_field_map(inputs):
    """Map our placeholders onto the merge tags this particular form actually declares.

    Mailchimp forms name their fields with merge tags (EMAIL, FNAME, LNAME) but a given
    list may not expose the name ones at all - sending a field the list does not have is
    ignored, while sending nothing for a REQUIRED field is a hard error. So read the
    form rather than assuming the defaults.
    """
    upper = {name.upper(): name for name in inputs}
    field_map = {upper.get("EMAIL", "EMAIL"): "$email"}
    if "FNAME" in upper:
        field_map[upper["FNAME"]] = "$first_name"
    if "LNAME" in upper:
        field_map[upper["LNAME"]] = "$last_name"
    return field_map


def detect_provider(form, page_url="", page_html=""):
    """Recognise a supported provider from one extracted form. Returns a partial recipe
    ({method, endpoint, params, field_map}) or None.

    Purely syntactic - it says "this is a Mailchimp form", never "this is the right
    Mailchimp form". Judging *whose* list it is is the model's job, and the reviewer's.
    """
    action = form.get("action") or ""
    inputs = form.get("inputs") or []

    if _MAILCHIMP_RE.search(action):
        params = _query_params(action)
        if not params.get("u") or not params.get("id"):
            return None  # a Mailchimp action without u+id cannot be posted to
        base = action.split("?", 1)[0]
        # post-json returns a readable {"result": ...}; the plain /post endpoint answers
        # with an HTML page we would have to guess at.
        endpoint = re.sub(r"/subscribe/post(-json)?$", "/subscribe/post-json", base)
        return {"method": "mailchimp", "endpoint": endpoint,
                "params": {"u": params["u"], "id": params["id"]},
                "field_map": _mailchimp_field_map(inputs)}

    if _CONVERTKIT_RE.search(action):
        return {"method": "convertkit", "endpoint": action.split("?", 1)[0], "params": {},
                "field_map": {"email_address": "$email", "first_name": "$first_name"}}

    if _MAILERLITE_RE.search(action):
        return {"method": "mailerlite", "endpoint": action.split("?", 1)[0], "params": {},
                "field_map": {"fields[email]": "$email", "fields[name]": "$first_name"}}

    host = _SUBSTACK_HOST_RE.search(action) or _SUBSTACK_HOST_RE.search(page_url or "")
    if host and ("email" in " ".join(inputs).lower() or "substack" in action.lower()):
        return {"method": "substack",
                "endpoint": f"https://{host.group(1)}.substack.com/api/v1/free",
                "params": {}, "field_map": {"email": "$email"}}

    return None


def find_candidates(page_url, html):
    """Every supported-provider form on one page, each with the text that justifies it."""
    out = []
    for form in extract_forms(html, page_url):
        recipe = detect_provider(form, page_url, html)
        if recipe:
            recipe["source_url"] = page_url
            recipe["form_text"] = form.get("text") or ""
            out.append(recipe)
    return out


# ---------- executing a recipe ----------

def resolve_fields(field_map, values):
    """Substitute a recipe's placeholders with this user's values.

    A placeholder with no value is DROPPED, not sent empty: an empty required field is
    rejected by every provider here, and an empty optional one writes a blank name onto
    the subscriber record.
    """
    resolved = {}
    for field, placeholder in (field_map or {}).items():
        if not isinstance(placeholder, str) or not placeholder.startswith("$"):
            resolved[field] = placeholder  # a literal constant the form requires
            continue
        value = (values.get(placeholder[1:]) or "").strip()
        if value:
            resolved[field] = value
    return resolved


def _post(url, data, headers=None, timeout=SUBSCRIBE_TIMEOUT):
    """POST form-encoded and return (status, body_text). HTTP errors come back as a
    status, not an exception - a 400 from a provider is an answer we want to classify."""
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "User-Agent": USER_AGENT,
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        **(headers or {}),
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(200_000).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(200_000).decode("utf-8", errors="replace")


def _loads(body):
    """Parse a provider body that may be bare JSON or a JSONP wrapper."""
    text = (body or "").strip()
    match = re.match(r"^[\w.$]+\((.*)\)[;\s]*$", text, re.S)
    if match:
        text = match.group(1)
    try:
        return json.loads(text)
    except Exception:
        return None


_ALREADY_RE = re.compile(r"already (?:a )?(?:subscribed|member|on (?:the|this) list)", re.I)

# The success wording, in one place. It says submitted, never subscribed: every provider
# here double opt-ins, and we have no way to see the confirmation land.
_CONFIRM_MSG = ("Signup submitted. Check your email for a confirmation link - most "
                "organizations only add you once you click it.")
_UNCLEAR_MSG = ("The organization's signup form did not confirm the signup. Open their "
                "page to sign up directly.")


def _mailchimp(recipe, fields):
    url = recipe["endpoint"] + "?" + urllib.parse.urlencode(recipe.get("params") or {})
    status, body = _post(url, fields)
    parsed = _loads(body) or {}
    msg = str(parsed.get("msg") or "")
    if parsed.get("result") == "success":
        return "submitted", _CONFIRM_MSG, msg[:300]
    if _ALREADY_RE.search(msg):
        return "already_subscribed", "You are already on this list.", msg[:300]
    if parsed.get("result") == "error":
        # Mailchimp's msg is user-facing and often useful ("please enter a valid email"),
        # but it can also be a raw HTML fragment - strip tags before showing it.
        clean = re.sub(r"\s+", " ", _TAG_RE.sub(" ", msg)).strip()
        return "failed", clean[:200] or "The organization's signup form rejected this.", msg[:300]
    return "failed", _UNCLEAR_MSG, f"HTTP {status}: {body[:200]}"


def _substack(recipe, fields):
    status, body = _post(recipe["endpoint"], {**fields, "first_url": recipe["endpoint"]})
    if status == 200:
        return "submitted", _CONFIRM_MSG, body[:300]
    return "failed", _UNCLEAR_MSG, f"HTTP {status}: {body[:200]}"


def _convertkit(recipe, fields):
    status, body = _post(recipe["endpoint"], fields)
    parsed = _loads(body) or {}
    if status in (200, 201) and (parsed.get("status") in ("success", "quarantined")
                                 or parsed.get("subscription")):
        return "submitted", _CONFIRM_MSG, body[:300]
    return "failed", _UNCLEAR_MSG, f"HTTP {status}: {body[:200]}"


def _mailerlite(recipe, fields):
    status, body = _post(recipe["endpoint"], {**fields, "ml-submit": "1", "anticsrf": "true"})
    parsed = _loads(body) or {}
    if status == 200 and parsed.get("success"):
        return "submitted", _CONFIRM_MSG, body[:300]
    if status == 200 and _ALREADY_RE.search(body or ""):
        return "already_subscribed", "You are already on this list.", body[:300]
    return "failed", _UNCLEAR_MSG, f"HTTP {status}: {body[:200]}"


PROVIDER_ADAPTERS = {
    "mailchimp": _mailchimp,
    "substack": _substack,
    "convertkit": _convertkit,
    "mailerlite": _mailerlite,
}

PROVIDER_NAMES = {
    "mailchimp": "Mailchimp", "substack": "Substack",
    "convertkit": "Kit (ConvertKit)", "mailerlite": "MailerLite",
}


def execute(recipe, values):
    """Run one verified recipe for one user. Returns (state, message, detail).

    `state` is one of SUBSCRIBE_STATES. `detail` is the raw provider reply, truncated -
    stored so a recipe that starts failing can be diagnosed without guesswork.
    """
    method = (recipe or {}).get("method")
    endpoint = (recipe or {}).get("endpoint")
    if method not in PROVIDER_ADAPTERS or not endpoint:
        return "handoff", "No automated signup is available for this opportunity.", ""
    fields = resolve_fields(recipe.get("field_map"), values)
    if not any("email" in k.lower() for k in fields):
        return "failed", "This signup form needs an email field we could not fill.", ""
    try:
        return PROVIDER_ADAPTERS[method](recipe, fields)
    except Exception as e:
        return "failed", "The organization's signup service did not respond.", str(e)[:300]

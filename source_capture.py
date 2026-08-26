#!/usr/bin/env python3
"""The unified substrate's CAPTURE layer (P6b, 2026-08-26). Turns a Claude `web_fetch` response
into verifiable, tier-tagged text — the "page text we hold locally" that the code-side
verifiers (`page_text.quote_is_on_page` / `claim_is_supported`) check a model's claims against.

WHY THIS REPLACES THE urllib FETCH (see DEADLINE_AND_TASK_PLAN.md §5a / decision 6). The task
agent used to fetch `opp.url` with stdlib `urllib` (`page_text.fetch_page_text`), which reads
~490 bytes of an SPA shell and rejects PDFs outright — so the richest requirement source (a
program's guidelines PDF, or a JS-rendered page) was invisible and tasks fell to generic. The
deadline agent already reads those via Claude's server-side `web_fetch`. This module lets BOTH
features fetch the same way while KEEPING code-side verification: we capture the fetched CONTENT,
not just its URL.

WHAT THE P6a PROBE ESTABLISHED (2026-08-26), which this parser encodes:
  * `web_fetch_tool_result.content.content.source`:
      - HTML → {type:'text', media_type:'text/plain', data:<clean markdown>}  → use directly.
      - PDF  → {type:'base64', media_type:'application/pdf', data:<b64 bytes>} → decode + PyPDF2.
  * `web_search_tool_result` items carry `encrypted_content` — OPAQUE. Search snippets are NOT
    verifiable, so they are IGNORED here. Search is for DISCOVERY only (finding the URL of a
    requirements page / PDF); the model must then `web_fetch` that URL for it to back a claim.

TIER (official / trusted / pending / blocked) is per source: the program's OWN domain is
`official`; everything else is classified by the operator allowlist (`aggregators_common`).
Verification proves the source SAID it; the tier says how far to trust the source (decision 5).
"""
import base64
import io
import json
import logging
import urllib.request

# PyPDF2 logs a "unknown widths"/font-glyph warning per malformed PDF at WARNING level, which
# would spam an agent-run log with thousands of lines on a batch pass. The extraction still
# succeeds; silence the noise (extraction failures degrade to "" here regardless).
logging.getLogger("PyPDF2").setLevel(logging.ERROR)

import aggregators_common
from claude_common import (
    ANTHROPIC_URL, MODEL, estimate_cost, _enforce_rate_limit, _default_timeout_secs,
)

# web_search is DISCOVERY only (its results are unverifiable); keep it cheap. web_fetch carries
# no per-call fee and is the tool that actually retrieves verifiable content, so it is generous.
MAX_SEARCH = 2
MAX_FETCH = 4
FETCH_MAX_CONTENT_TOKENS = 6000  # per fetched page, bounds input token cost


class CapturedSource:
    """One fetched page, decoded to text and tier-tagged. `text` is "" when we could not
    decode it (unknown media type, PDF extraction failed) — such a source backs no claim but
    is never a crash."""

    __slots__ = ("url", "domain", "media_type", "text", "tier")

    def __init__(self, url, domain, media_type, text, tier):
        self.url = url
        self.domain = domain
        self.media_type = media_type
        self.text = text or ""
        self.tier = tier

    def __repr__(self):
        return f"CapturedSource({self.url!r}, tier={self.tier}, {len(self.text)} chars)"


def pdf_text_from_base64(b64):
    """Extract text from base64-encoded PDF bytes (what web_fetch returns for a PDF). Uses
    PyPDF2 — already a repo dependency (resume import) — lazily, so a missing PyPDF2 or a
    malformed PDF degrades to "" (that source backs nothing) rather than raising."""
    try:
        import PyPDF2
        raw = base64.b64decode(b64)
        reader = PyPDF2.PdfReader(io.BytesIO(raw))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:
        return ""


def tier_for(url, own_domain, policy):
    """official (the program's own domain or a subdomain of it) → else the operator policy's
    verdict (trusted / blocked / pending). Official is decided HERE, not by the allowlist —
    the allowlist only governs THIRD-party domains."""
    if own_domain and aggregators_common.domain_matches(url, [own_domain]):
        return "official"
    if policy is not None:
        return policy.classify(url)
    return "pending"


def _decode_source(source):
    """(media_type, text) from a web_fetch document `source` block."""
    if not isinstance(source, dict):
        return "", ""
    media = (source.get("media_type") or "").lower()
    data = source.get("data") or ""
    stype = source.get("type")
    if stype == "text":
        return media, data
    if stype == "base64" and "pdf" in media:
        return media, pdf_text_from_base64(data)
    # Any other shape (an unexpected media type) yields no text — the source backs no claim,
    # which is the safe direction, never a crash. See the T8 residual-risk note in the plan.
    return media, ""


def parse_captured_sources(response_data, own_domain=None, policy=None):
    """PURE. Every `web_fetch_tool_result` block → a CapturedSource, deduped by URL. Ignores
    `web_search_tool_result` (encrypted, unverifiable) and error blocks. Hermetically testable
    against a recorded response — this is the T8-critical parser."""
    out, seen = [], set()
    for block in response_data.get("content") or []:
        if block.get("type") != "web_fetch_tool_result":
            continue
        content = block.get("content")
        # An errored fetch has content of a different shape (e.g. an error dict without `url`);
        # skip anything that is not a successful fetch result.
        if not isinstance(content, dict) or content.get("type") == "web_fetch_tool_error":
            continue
        url = content.get("url") or ""
        if not url or url in seen:
            continue
        seen.add(url)
        media, text = _decode_source((content.get("content") or {}).get("source"))
        domain = aggregators_common.normalize_domain(url)
        out.append(CapturedSource(url, domain, media, text, tier_for(url, own_domain, policy)))
    return out


# ---------- the fetch call ----------

FETCH_SYSTEM = (
    "You retrieve one high-school program's own application / requirements page so it can be "
    "read. Use web_search ONLY to locate the right URL on the program's own site (its "
    "application, requirements, 'how to apply', eligibility, or guidelines/PDF page), then use "
    "web_fetch to RETRIEVE each such page in full. Prefer the program's own domain. Fetch the "
    "guidelines PDF if the program publishes one. Do not answer from memory — the point is to "
    "fetch the real pages; a one-line acknowledgement of what you fetched is enough text.")


def _capture_call(user_content, api_key, timeout):
    """One raw Claude call with web_search (discovery) + web_fetch (retrieval) enabled;
    returns the raw response_data so the caller can parse the fetched blocks. Mirrors
    check_deadlines.call_claude's request shape (same model, tools, headers)."""
    _enforce_rate_limit()
    body = {
        "model": MODEL,
        "max_tokens": 600,  # we want the FETCHES, not a long answer
        "system": FETCH_SYSTEM,
        "messages": [{"role": "user", "content": user_content}],
        "tools": [
            {"type": "web_search_20250305", "name": "web_search", "max_uses": MAX_SEARCH},
            {"type": "web_fetch_20250910", "name": "web_fetch", "max_uses": MAX_FETCH,
             "max_content_tokens": FETCH_MAX_CONTENT_TOKENS},
        ],
    }
    req = urllib.request.Request(
        ANTHROPIC_URL, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "x-api-key": api_key,
                 "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(req, timeout=timeout or _default_timeout_secs) as resp:
        return json.loads(resp.read())


def fetch_and_capture(opp, api_key, timeout=None, policy=None):
    """Fetch the program's page(s) via Claude web_fetch and return
    (sources, cost, reason). `sources` is a list of CapturedSource (tier-tagged); `reason` is
    'ok' when at least one source yielded text, else a short label the caller treats exactly
    like page_text's fetch failure (generic fallback, no stamp, retry next run)."""
    own_domain = aggregators_common.normalize_domain(opp.get("url"))
    user = (f"Program: {opp.get('name', '')}\nOrganization: {opp.get('org') or ''}\n"
            f"Program URL: {opp.get('url') or ''}\n\n"
            f"Find and web_fetch this program's application / requirements page (and its "
            f"guidelines PDF if it has one). Start from the program URL above.")
    data = _capture_call(user, api_key, timeout)
    cost = estimate_cost(data.get("usage", {}) or {})
    sources = parse_captured_sources(data, own_domain=own_domain, policy=policy)
    reason = "ok" if any(s.text.strip() for s in sources) else "no-fetch"
    return sources, cost, reason

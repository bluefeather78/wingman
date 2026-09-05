#!/usr/bin/env python3
"""The combined discovery reader — one page fetch, everything the page can tell us about a NEW row.

The scraper used to be a daisy chain: discover a candidate, then a separate agent re-fetches its
page for metadata, another re-fetches for a duplicate check, and so on — each paying the expensive,
failure-prone fetch again on its own schedule. This collapses the DISCOVERY path into a single read:

    fetch the page ONCE
      -> classify        program / first_party_hub / third_party_hub / none   (classify_page)
      -> if "program" and not stale:
             metadata    read the same page for name/org/summary/eligibility/... (refresh's own
                         M1-approved extraction, reused verbatim on the text we already hold)
             dedupe      embed the page and hint at the nearest existing row     (embed_common)
      -> hubs become discovery leads; none is flagged; stale programs drop.

**Scope (operator, 2026-08-30):** this reader is classify + staleness + metadata + embedding
dedupe, for NEW opportunities only. Deadlines and action items stay STANDALONE agents (out of
scope here). `refresh_opportunities.py` also stays standalone — kept as the lightweight
existing-row updater — which is why nothing is retired and no M1 code is edited: this module only
CALLS refresh's public `build_system`/`clean_update_dict`, feeding them the page text it already
fetched. M1 is preserved by construction: metadata is only ever extracted when the fetch succeeded
(a failed fetch routes to `unreadable` and never reaches the model), so we never answer from memory.

Every model/embedding call is INJECTED, so this whole module is free to import and unit-test with
no network. The live wrapper wires the real calls; running it is PAID (M9) and gated per run. The
classifier prompt is M8; the metadata prompt is refresh's, reused unchanged.
"""
import dataclasses

import classify_page
import embed_common
import refresh_opportunities

# The metadata call matches refresh_opportunities.check_one exactly (same model, same budget), so a
# row enriched at discovery is indistinguishable from one refresh would produce. Keep these in step
# with refresh if it ever changes.
_METADATA_MODEL = "gemini-3.5-flash-lite"
_METADATA_MAX_TOKENS = 1200
_METADATA_PAGE_CHARS = 16_000

# Set by eval/dedupe_eval.py (run 2026-08-30, active catalog, 90 pairs). Two findings drive these:
#   1. Embeddings are a strong HINT but NOT an auto-suppressor — there is NO clean threshold. Real
#      duplicates and same-org SIBLINGS overlap (YoungArts category competitions, Badger Music vs
#      Arts Clinic, Stanford sibling programs all sit in the 0.83-0.95 band). So this stays a
#      dup_candidates HINT only (tenet 10), never a reject — which is what the eval confirmed.
#   2. The FIELDS representation (name+org+type+summary+eligibility) beat page text: its >=0.95 band
#      is almost purely genuine duplicates, AND it needs no page fetch, so the catalog index is
#      cheap/robust to build and covers dead-page rows. `default_representation` uses it.
# 0.93 is the hint floor — recall-leaning, since a reviewer can dismiss a hint in a glance. The
# aggregate "precision 0.12" the eval printed is against an incomplete label set (it marked many
# real dupes "distinct"); the sorted cosine list is the truth, and above ~0.95 it is nearly all
# real duplicates.
DEFAULT_DUP_THRESHOLD = 0.93
DEFAULT_DUP_TOP_K = 3


@dataclasses.dataclass
class ReadResult:
    """What one page read yields. `route` is the single disposition (see classify_page.ROUTE_*)."""
    url: str = ""
    final_url: str = ""
    route: str = classify_page.ROUTE_UNREADABLE
    classification: classify_page.Classification = None
    metadata: dict = dataclasses.field(default_factory=dict)
    dup_candidates: list = dataclasses.field(default_factory=list)
    cost: float = 0.0

    @property
    def is_row(self):
        return self.route == classify_page.ROUTE_ROW


# --- metadata (refresh's extraction, reused on the already-fetched text) ---------------

def extract_metadata(url, page_text_str, call, name_hint="", org_hint=""):
    """Metadata fields for one page, via refresh_opportunities' own prompt + validation. PAID call.

    `call(system, user) -> (text, usage)` is injected. The prompt is refresh's `build_system` (its
    M1-approved wording, unchanged) and the field validation is refresh's `clean_update_dict`, so
    this produces exactly what a refresh pass would — from the page text we already have in hand,
    not a second fetch. Returns (update_dict, cost); cost is banked before the parse.
    """
    opp = {"name": name_hint or "", "org": org_hint or "", "url": url}
    system = refresh_opportunities.build_system(opp)
    user = (f"Program: {name_hint or 'unknown'} ({org_hint or 'unknown org'})\n"
            f"URL (identification only — do not return a URL): {url}\n\n"
            f"PAGE TEXT (extract ONLY from this):\n{(page_text_str or '')[:_METADATA_PAGE_CHARS]}\n\n"
            f"Return the schema JSON now. Null for anything the page does not state.")
    text, usage = call(system, user)
    from gemini_common import estimate_cost, extract_json
    cost = estimate_cost(usage or {})
    try:
        info = extract_json(text)
    except Exception:
        return {}, cost
    if not isinstance(info, dict):
        return {}, cost
    return refresh_opportunities.clean_update_dict(info), cost


# --- dedupe hint (embed the page, find the nearest existing row) -----------------------

def default_representation(metadata, page_text_str):
    """The text embedded for duplicate detection. PROVISIONAL — replaced by dedupe_eval's winner.

    Defaults to the structured fields (boilerplate-free), falling back to the page text when the
    metadata is thin. Injectable, so swapping to the eval-chosen representation is a call-site change.
    """
    fields = [metadata.get("name"), metadata.get("org"), metadata.get("type"),
              metadata.get("summary"), metadata.get("eligibility")]
    joined = "\n".join(str(f) for f in fields if f)
    return joined or (page_text_str or "")


def dedup_hint(representation_text, embed_fn, index, threshold=DEFAULT_DUP_THRESHOLD,
               top_k=DEFAULT_DUP_TOP_K, exclude_ids=None):
    """Nearest existing rows to this page, as dup_candidates. PAID (one embedding). Free if no index.

    `embed_fn(text) -> (vector, cost)` is injected. Returns (dup_candidates, cost) where each
    candidate is {id, score, reason} — the shape the console review queue already renders inline.
    A HINT only: it never rejects, per tenet 10 ("suppress only on proof").
    """
    if not index or not representation_text:
        return [], 0.0
    vector, cost = embed_fn(representation_text)
    if not vector:
        return [], cost
    hits = embed_common.nearest(vector, index, top_k=top_k, min_score=threshold,
                                exclude_ids=exclude_ids)
    cands = [{"id": rid, "score": round(score, 4),
              "reason": f"{score:.2f} page-content similarity"} for rid, score, _ in hits]
    return cands, cost


# --- the orchestrator (pure: every call injected) -------------------------------------

def read_candidate(url, page_text_str, final_url="", *, classify_call, name_hint="", org_hint="",
                   metadata_call=None, embed_fn=None, index=None, representation_fn=None,
                   dup_threshold=DEFAULT_DUP_THRESHOLD, dup_top_k=DEFAULT_DUP_TOP_K,
                   today_year=None, exclude_ids=None):
    """Turn one already-fetched page into a ReadResult. No network here — calls are injected.

    Empty page text is `unreadable` and costs nothing (the classifier never runs — a blocked/JS/PDF
    fetch is about our HTTP client, never the page, so we keep the caller's existing behaviour). A
    program page runs the metadata call, and the dedupe hint too when an index + embed_fn are given.
    A hub, a `none`, or a stale program does none of that — there is no row to enrich.
    """
    result = ReadResult(url=url, final_url=final_url or url)
    if not page_text_str:
        result.classification = classify_page.Classification(klass=None, readable=False,
                                                             error="no text")
        result.route = classify_page.ROUTE_UNREADABLE
        return result

    c = classify_page.classify_from_text(url, page_text_str, classify_call,
                                         name_hint=name_hint, org_hint=org_hint,
                                         today_year=today_year)
    result.classification = c
    result.cost += c.cost
    result.route = classify_page.route_for(c)

    if result.route != classify_page.ROUTE_ROW:
        return result  # hub -> lead, none -> flag, stale -> drop: no row to enrich

    if metadata_call is not None:
        result.metadata, mcost = extract_metadata(url, page_text_str, metadata_call,
                                                   name_hint=name_hint, org_hint=org_hint)
        result.cost += mcost

    if embed_fn is not None and index:
        rep = (representation_fn or default_representation)(result.metadata, page_text_str)
        result.dup_candidates, dcost = dedup_hint(rep, embed_fn, index, threshold=dup_threshold,
                                                  top_k=dup_top_k, exclude_ids=exclude_ids)
        result.cost += dcost
    return result


# --- the live wrapper (wires the real, PAID calls) ------------------------------------

def read_candidate_live(url, api_key, name_hint="", org_hint="", index=None,
                        dup_threshold=DEFAULT_DUP_THRESHOLD, allow_browser=False, timeout=None,
                        today_year=None, exclude_ids=None):
    """Fetch `url` ONCE and read it end to end. PAID (classify + metadata [+ embedding]).

    The single fetch uses the M1-shaped reader (plain HTTP, optional headless fallback); its text
    feeds every downstream step, so nothing re-fetches. Returns a ReadResult.
    """
    import page_text
    import gemini_common

    text, _reason, final_url = page_text.fetch_page_text_resolved(
        url, timeout=timeout or page_text.DEFAULT_TIMEOUT, allow_browser=allow_browser)

    classify_call = lambda system, user: gemini_common.call_gemini(
        system, user, api_key, use_web_search=False, max_tokens=800, timeout=timeout)
    metadata_call = lambda system, user: gemini_common.call_gemini(
        system, user, api_key, use_web_search=False, max_tokens=_METADATA_MAX_TOKENS,
        timeout=timeout, model=_METADATA_MODEL)
    embed_fn = (lambda t: embed_common.embed_text(t, api_key, timeout=timeout or 60)) if index else None

    return read_candidate(url, text, final_url, classify_call=classify_call, name_hint=name_hint,
                          org_hint=org_hint, metadata_call=metadata_call, embed_fn=embed_fn,
                          index=index, dup_threshold=dup_threshold, today_year=today_year,
                          exclude_ids=exclude_ids)

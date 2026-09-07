"""Layer-1 SEO program pages: the ONE place that decides whether an opportunity's public
page is substantial enough to be indexed, and how its URL slug is formed.

WHY THIS IS A SHARED MODULE (not logic living in the route)
Three callers must agree exactly, or the system contradicts itself:
  * app/routes/seo_pages.py renders the page and decides `index` vs `noindex` LIVE, off the
    current row — so a page that has gone thin since the last evaluation is never indexed.
  * the /sitemap.xml route lists only rows the last evaluation stored as `indexed`.
  * ops/core.evaluate_seo_pages() walks the catalog and writes the durable seo_* columns the
    admin dashboard reads (how many pages exist, how many are indexed, how many await info).
If any of those computed the bar differently, the dashboard would count one thing and the
crawler would see another. So the bar lives here once — the same role deadline_write_decision
and action_items_write_decision play for their subsystems.

THE COMPOSITE BAR (agreed with Shama 2026-09-06, not deadline-only)
A page is indexable when it has a real summary PLUS at least 2 of four substance signals:
{verified deadline, eligibility, cost/format, an application checklist of >=3 steps}. A
verified deadline is the single strongest signal (it is both the highest-search-value field —
"[program] deadline 2027" — and the freshness edge), so it is weighted heaviest in the display
score, but it is deliberately NOT the sole gate: a page with only a summary + deadline still
reads thin to a search engine, and plenty of legitimate rows have no posted deadline (rolling
admissions, next cycle unannounced) yet carry rich eligibility and a checklist. Gating purely
on the deadline would wrongly drop those and wrongly admit the thin ones. See docs/CLAUDE-ops.md
if this ever gets "simplified" back to a single field.

This module is pure and stdlib-only: it takes an opportunity dict and returns a verdict. It
makes no network calls and imports nothing from app/ or ops/, so it is trivially unit-tested
(tests/test_seo_pages.py runs the built-in generic shapes through it).
"""
import re
import unicodedata

# A summary shorter than this is a fragment, not content a search result can stand on.
SUMMARY_MIN_CHARS = 40
# The checklist has to be a real "how to apply", not one stray step.
CHECKLIST_MIN_ITEMS = 3
# How many of the four substance signals a page needs (on top of a real summary) to be indexed.
MIN_SIGNALS_TO_INDEX = 2

MAX_SLUG_LEN = 80

# Display weights. The gate counts SIGNALS (below); this is only for the score shown in the
# console and for sorting "closest to publishable" first. A verified deadline is worth 2 to
# reflect that it is the strongest single signal.
_SCORE = {"summary": 1, "deadline_verified": 2, "deadline_estimated": 1,
          "eligibility": 1, "cost_format": 1, "checklist": 1}
MAX_SCORE = _SCORE["summary"] + _SCORE["deadline_verified"] + _SCORE["eligibility"] \
    + _SCORE["cost_format"] + _SCORE["checklist"]  # 6

STATUS_INDEXED = "indexed"
STATUS_AWAITING = "awaiting_info"

# Human labels for the signals a row is missing, shown in the console so an operator knows
# exactly what to fill in to publish a page.
_MISSING_LABEL = {
    "summary": "summary",
    "deadline": "deadline",
    "eligibility": "eligibility",
    "cost_format": "cost or format",
    "checklist": "application checklist (3+ steps)",
}


def _clean_text(value):
    return value.strip() if isinstance(value, str) else ""


def has_real_summary(opp):
    return len(_clean_text(opp.get("summary"))) >= SUMMARY_MIN_CHARS


def deadline_signal(opp):
    """Return 'verified' if a real, non-estimated date is present, 'estimated' if the only
    dates are projected, else None. important_dates entries look like
    {"type","date_iso","estimated","label"} (agents/check_deadlines.py build_system)."""
    dates = opp.get("important_dates")
    if not isinstance(dates, list):
        return None
    seen_estimated = False
    for d in dates:
        if not isinstance(d, dict) or not d.get("date_iso"):
            continue
        if d.get("estimated"):
            seen_estimated = True
        else:
            return "verified"
    return "estimated" if seen_estimated else None


def eligibility_signal(opp):
    """A curated eligibility note, or a grade band, either of which tells a reader who may apply."""
    if _clean_text(opp.get("eligibility")):
        return True
    return opp.get("grade_min") is not None or opp.get("grade_max") is not None


def cost_format_signal(opp):
    """Price, or where/how it runs — the practical facts a student weighs."""
    if _clean_text(opp.get("price")):
        return True
    return bool(_clean_text(opp.get("location")) or _clean_text(opp.get("state")))


def checklist_signal(opp):
    """A page-verified application checklist of at least CHECKLIST_MIN_ITEMS real steps."""
    items = opp.get("action_items")
    if not isinstance(items, list):
        return False
    real = 0
    for it in items:
        if isinstance(it, dict):
            if _clean_text(it.get("text")) or _clean_text(it.get("task")):
                real += 1
        elif _clean_text(it):
            real += 1
    return real >= CHECKLIST_MIN_ITEMS


def evaluate_seo_page(opp):
    """The verdict for one opportunity row. Pure: no I/O.

    Returns a dict:
      indexable            bool   — may this page be crawled/indexed?
      status               str    — STATUS_INDEXED | STATUS_AWAITING (for the durable column)
      score / max_score    int    — display score, verified-deadline-weighted
      signals              dict    — the raw signal readout (for the console drill-down)
      signal_count         int    — how many of the four substance signals are present
      has_verified_deadline bool
      missing              list[str] — human labels for what is missing to publish
    """
    summary = has_real_summary(opp)
    dl = deadline_signal(opp)                 # None | 'estimated' | 'verified'
    elig = eligibility_signal(opp)
    cost = cost_format_signal(opp)
    checklist = checklist_signal(opp)

    signal_present = {
        "deadline": dl is not None,
        "eligibility": elig,
        "cost_format": cost,
        "checklist": checklist,
    }
    signal_count = sum(1 for v in signal_present.values() if v)
    indexable = summary and signal_count >= MIN_SIGNALS_TO_INDEX

    score = 0
    if summary:
        score += _SCORE["summary"]
    if dl == "verified":
        score += _SCORE["deadline_verified"]
    elif dl == "estimated":
        score += _SCORE["deadline_estimated"]
    if elig:
        score += _SCORE["eligibility"]
    if cost:
        score += _SCORE["cost_format"]
    if checklist:
        score += _SCORE["checklist"]

    missing = []
    if not summary:
        missing.append(_MISSING_LABEL["summary"])
    for key, present in signal_present.items():
        if not present:
            missing.append(_MISSING_LABEL[key])

    return {
        "indexable": indexable,
        "status": STATUS_INDEXED if indexable else STATUS_AWAITING,
        "score": score,
        "max_score": MAX_SCORE,
        "signals": {"summary": summary, "deadline": dl, "eligibility": elig,
                    "cost_format": cost, "checklist": checklist},
        "signal_count": signal_count,
        "has_verified_deadline": dl == "verified",
        "missing": missing,
    }


def slugify(name):
    """A URL slug from a program name. ASCII-only, lowercase, hyphen-separated, capped.

    Never empty — a nameless row still needs a resolvable path — so it falls back to
    'opportunity', which the caller then makes unique with the id suffix."""
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    s = re.sub(r"-{2,}", "-", s)[:MAX_SLUG_LEN].strip("-")
    return s or "opportunity"


def assign_unique_slug(name, opp_id, taken):
    """A slug for `name` guaranteed not to collide with anything in `taken` (a set).

    On a collision the id is appended (ids are unique), so two programs that slugify the same
    ("Summer Research Program" x2) still get distinct, stable URLs. Does NOT mutate `taken`;
    the caller adds the returned slug once it commits it, so a dry pass cannot poison the set."""
    base = slugify(name)
    if base not in taken:
        return base
    with_id = f"{base}-{opp_id}"
    if with_id not in taken:
        return with_id
    # Pathological: same name AND same id already taken (shouldn't happen). Disambiguate.
    n = 2
    while f"{with_id}-{n}" in taken:
        n += 1
    return f"{with_id}-{n}"

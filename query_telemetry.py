#!/usr/bin/env python3
"""What each angle actually SEARCHED — reading the scraper's per-seed query telemetry. FREE.

An angle is a prompt fragment, never a query: `scrape_opportunities.research_seed` hands the
angle to Gemini, and Gemini decides on its own which queries to issue and how many. The queries
come back only as telemetry (`usage.server_tool_use.web_search_queries`), are printed to the run
log, and are written to `agent_logs/scraper_<stamp>_seed<id>.json`.

Until now **nothing read them back**, so the one question that matters about an angle — *did this
phrase turn into broad searches that could surface many programs, or into narrow searches that
could only ever confirm one program the model already had in mind?* — could not be asked at all.
This module is the read side. It is PURE and stdlib-only: the callers do the file I/O
(`ops/core.list_seed_query_runs`) so the judgement is unit-testable with no disk.

CLASSIFICATION IS A HEURISTIC AND IS LABELLED AS ONE. Three shapes, and the distinction that
matters is the first one:

    broad     no program named — the query describes a CLASS ("high school marine biology
              research programs"). This is the only shape that can discover something we have
              never heard of.
    named     a specific program or organisation is named, either quoted ("MassArt" "Summer
              Intensives") or as a capitalised proper noun / acronym. Its ceiling is one program,
              and that program was already in the model's head before the search.
    metadata  no program named, but the query asks for cost / eligibility / contact / deadline —
              enrichment of something already found rather than discovery.

`named` and `metadata` overlap constantly in practice (`"NYU" "User Experience Design" cost
eligibility` is both). The shape is resolved in that order — named wins — because "this search
could only ever return one program" is the more important fact about it.

MEASURED on the 2026-08-23 run (40 seeds, 213 queries), which is what motivated this module:
roughly half of every paid search was `named`, i.e. the per-search fee was being spent
confirming programs the model already knew rather than finding new ones.
"""
import re

# A quoted phrase, a run of two or more Capitalised words, or a 2+ letter ALL-CAPS acronym.
# Any of the three means the query has a specific program/org in it and cannot return a class.
_QUOTED_RE = re.compile(r'"[^"]{2,}"')
_PROPER_RE = re.compile(r"\b[A-Z][a-z]{2,}(?:\s+(?:of|and|the|for))?\s+[A-Z][a-z]{2,}")
_ACRONYM_RE = re.compile(r"\b[A-Z]{2,6}\b")
# Words that make a query an enrichment lookup rather than a discovery one.
_META_RE = re.compile(r"\b(cost|costs|tuition|fee|fees|price|pricing|eligibility|eligible|"
                      r"stipend|paid|contact email|email|deadline|deadlines|application "
                      r"deadline|requirements|prerequisites)\b", re.I)
# Who the query is for. An angle whose queries drop this is searching the whole internet, not
# the high-school slice of it — worth seeing, never worth failing on.
_AUDIENCE_RE = re.compile(r"\b(high school(?:ers?|s)?|highschool|teen(?:s|age|ager)?|"
                          r"pre-?college|grades?\s*9|9th\s*grade|secondary school)\b", re.I)
# Geography markers. `local` mode angles should produce place-anchored queries; a national angle
# that suddenly produces them has drifted. City list is deliberately tiny — the Seattle metro is
# the only local mode that exists — plus the generic shapes any locality would use.
_PLACE_RE = re.compile(r"\b(seattle|bellevue|redmond|tacoma|kirkland|everett|renton|"
                       r"king county|puget sound|washington state|wa\b|near me|"
                       r"local|city of|county)\b", re.I)

SHAPE_BROAD = "broad"
SHAPE_NAMED = "named"
SHAPE_METADATA = "metadata"
SHAPES = (SHAPE_BROAD, SHAPE_NAMED, SHAPE_METADATA)

# An acronym-looking token that is not a program name. Without this, "AI ethics high school
# fellowship" and "UX UI design programs for high schoolers" read as named searches, which is
# exactly backwards — they are the broad ones.
_ACRONYM_STOPWORDS = {
    "AI", "ML", "UX", "UI", "CS", "STEM", "STEAM", "US", "USA", "SAT", "ACT", "GPA", "PHD",
    "MBA", "HS", "IT", "AP", "IB", "NLP", "VR", "AR", "CTF", "3D", "K12",
}


def _has_proper_name(text):
    """True when the query names a specific program or organisation.

    Quoted phrases and capitalised runs are unambiguous. A lone acronym is not: half the
    subject vocabulary of this catalog is acronyms (AI, UX, STEM, CTF), so only acronyms
    outside `_ACRONYM_STOPWORDS` count.
    """
    if _QUOTED_RE.search(text):
        return True
    if _PROPER_RE.search(text):
        return True
    return any(a not in _ACRONYM_STOPWORDS for a in _ACRONYM_RE.findall(text))


def classify_query(query):
    """One query -> {query, shape, named, metadata, audience, place}. Pure, no I/O."""
    text = (query or "").strip()
    named = _has_proper_name(text)
    metadata = bool(_META_RE.search(text))
    if named:
        shape = SHAPE_NAMED
    elif metadata:
        shape = SHAPE_METADATA
    else:
        shape = SHAPE_BROAD
    return {
        "query": text,
        "shape": shape,
        "named": named,
        "metadata": metadata,
        "audience": bool(_AUDIENCE_RE.search(text)),
        "place": bool(_PLACE_RE.search(text)),
    }


def summarize_queries(queries):
    """Shape counts + rates for one list of query strings.

    `breadth` is the headline: the share of this angle's paid searches that could have
    surfaced a program nobody had named yet. It is None (not 0) when there were no queries,
    because "searched nothing" and "searched only narrowly" are different failures — one is a
    silent call, the other is a mis-shaped angle, and rendering both as 0% hides which.
    """
    classified = [classify_query(q) for q in (queries or []) if str(q or "").strip()]
    total = len(classified)
    counts = {s: 0 for s in SHAPES}
    for c in classified:
        counts[c["shape"]] += 1
    return {
        "queries": classified,
        "total": total,
        "counts": counts,
        "breadth": round(counts[SHAPE_BROAD] / total, 3) if total else None,
        "named_rate": round(counts[SHAPE_NAMED] / total, 3) if total else None,
        "audience_rate": round(sum(1 for c in classified if c["audience"]) / total, 3) if total else None,
        "place_rate": round(sum(1 for c in classified if c["place"]) / total, 3) if total else None,
    }


def summarize_seed(entry):
    """One parsed seed log -> the row the console renders.

    `entry` is the dict written by `scrape_opportunities.main()`: angle, searches, attempts,
    queries, resolved_urls, candidates. Every field is treated as optional — these files are
    written by a run that may have been killed part-way, and a half-written log must degrade to
    a thin row rather than break the whole view.
    """
    entry = entry or {}
    summary = summarize_queries(entry.get("queries"))
    searches = entry.get("searches")
    attempts = entry.get("attempts")
    candidates = entry.get("candidates")
    resolved = entry.get("resolved_urls")
    return {
        "angle": entry.get("angle") or "",
        "searches": searches if isinstance(searches, int) else None,
        "attempts": attempts if isinstance(attempts, int) else None,
        "candidates": len(candidates) if isinstance(candidates, list) else None,
        "resolved_urls": len(resolved) if isinstance(resolved, list) else None,
        # A run that searched but logged no query strings is not the same as a silent call.
        "silent": searches == 0,
        "retried": attempts == 2,
        **summary,
    }


def summarize_run(seed_rows):
    """Roll a run's per-seed summaries into the run-level header figures.

    Rates are computed over the QUERY population, not by averaging per-seed rates: an angle
    that issued 8 queries says more about the run than one that issued 2, and averaging the
    rates would weight them equally.
    """
    rows = list(seed_rows or [])
    total_queries = sum(r.get("total") or 0 for r in rows)
    counts = {s: sum((r.get("counts") or {}).get(s, 0) for r in rows) for s in SHAPES}
    with_queries = [r for r in rows if (r.get("total") or 0)]
    return {
        "seeds": len(rows),
        "total_queries": total_queries,
        "counts": counts,
        "breadth": round(counts[SHAPE_BROAD] / total_queries, 3) if total_queries else None,
        "named_rate": round(counts[SHAPE_NAMED] / total_queries, 3) if total_queries else None,
        "silent_seeds": sum(1 for r in rows if r.get("silent")),
        "retried_seeds": sum(1 for r in rows if r.get("retried")),
        "queries_per_seed": round(total_queries / len(with_queries), 2) if with_queries else None,
        # Distinct query strings across the whole run. Two angles issuing the same search is
        # money spent twice for one answer, and it is the only overlap signal available today.
        "distinct_queries": len({(c.get("query") or "").lower()
                                 for r in rows for c in (r.get("queries") or [])}),
    }

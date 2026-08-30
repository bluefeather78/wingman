"""Recall stage for the curated-matching pipeline (OPPORTUNITY_MATCHING_PLAN.md, Phase 5).

The recall stage takes the whole active catalog and narrows it to a ~100-row candidate pool
by SEMANTIC similarity (cosine between a row's embedding and each of the student's per-theme
embeddings, best match wins), gated by a small set of objective, code-level filters. It runs
SERVER-SIDE — the row embeddings live in the backend `_opportunities_cache` (jsonb
`match_vector` column), never shipped to the client (~9MB/load would be a real regression),
so the cosine happens here in Python, not in the RN client where `preFilter` used to live.

Everything in this module is PURE (rows in, values out) and offline-testable. The embedding
CALLS (turning text into vectors) live elsewhere — this module consumes vectors already
computed. Filters are deliberately minimal: recall's only job is to not drop a feasible row
before Phase 1's live LLM reasoning ever sees it, and to stop clearly-infeasible rows from
crowding the fixed ~100 slots. The final, exact eligibility call belongs to Phase 1.

WHAT EXCLUDES A ROW HERE (and nothing else — see the plan's "Recall's filter set"):
  * status == "not_running"          — discontinued; a dead program is never a match.
  * a LOOSENED grade check           — drops only rows unambiguously for OLDER students,
                                       with escape hatches; see recall_grade_ok's docstring.
  * a geo scope check                — a location-restricted (local-only) row whose place the
                                       student cannot reach; national/remote rows always pass.
(`is_active` is already enforced upstream by fetch_opportunities' `is_active=eq.true`, so it
is not re-checked here.)
"""
from __future__ import annotations

import hashlib
import re

import numpy as np

# --------------------------------------------------------------------------- content hash
#
# The five fields the row embedding is computed from. A row's embedding is recomputed only
# when the hash of these exact fields changes (see match_vector_schema.sql / the
# activation-gated refresh hook), so the vector can never be stale relative to the text it
# describes — the same exact-identity freshness idea `profileDerivedIsFresh` uses on the
# profile side. Eligibility / price / season / location / state are deliberately EXCLUDED:
# they are restriction/logistics signal, and folding them into a FIT embedding risks the
# vector keying on who's excluded or where a program runs rather than what it is about.
EMBED_FIELDS = ("name", "org", "summary", "subject_tags", "type")


def embed_text(row: dict) -> str:
    """The exact text fed to the embedding model for a catalog row. One field per line,
    labeled, so the model sees structure; subject_tags joined with commas. Stable ordering
    (EMBED_FIELDS) so the same row always produces the same text — the hash depends on it."""
    parts = []
    for field in EMBED_FIELDS:
        val = row.get(field)
        if field == "subject_tags":
            tags = val if isinstance(val, list) else []
            val = ", ".join(str(t).strip() for t in tags if str(t).strip())
        val = "" if val is None else str(val).strip()
        parts.append(f"{field}: {val}")
    return "\n".join(parts)


def match_vector_content_hash(row: dict) -> str:
    """A stable hash of the fields the embedding depends on. Recompute the embedding iff this
    differs from the row's stored `match_vector_hash`. Uses embed_text so the hash and the
    embedded text can never disagree about what went into the vector."""
    return hashlib.sha256(embed_text(row).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- grade filter
#
# The LOOSENED recall grade filter (decision 2026-08-29). Two extremes were both wrong: a
# STRICT numeric cut unrecoverably drops "rising Nth grader"/age-phrased rows the LLM would
# keep; ZERO grade influence dilutes the fixed ~100-row pool, because grade gates ~68% of the
# catalog, so a young student's top-100 SEMANTIC matches fill up with older-only programs and
# push the one perfect eligible match past rank 100 (the recall failure curation makes matter
# MOST).
#
# NOTE ON THE SPEC (flagged for the operator): the plan's prose lists the keep clause as
# "grade_max >= student_grade", but that reading KEEPS a junior-only program (grade_max=12)
# for a 9th grader — which does nothing about the older-only dilution the same paragraph says
# is the whole point. The coherent reading that achieves the stated goal is grade_MIN-based:
# drop a row only when it is unambiguously for students OLDER than this one (grade_min >
# student_grade). Implemented that way here; confirm before this ships.
_RISING_RE = re.compile(r"\brising\b", re.IGNORECASE)
# Age phrasing whose presence means the numeric grade bounds may have been derived wrongly
# (e.g. "ages 13-19" mis-mapped), so we do not trust them to DROP the row at recall.
_AGE_RE = re.compile(r"\bages?\b|\byears?\s+old\b|\byo\b", re.IGNORECASE)


def _has_grade_escape_phrasing(row: dict) -> bool:
    """True if the row's eligibility text uses 'rising' or age phrasing — in which case the
    structured grade_min may be backwards/derived-wrong and must not be trusted to drop."""
    text = row.get("eligibility") or ""
    if not isinstance(text, str):
        return False
    return bool(_RISING_RE.search(text) or _AGE_RE.search(text))


def recall_grade_ok(row: dict, student_grade: int | None) -> bool:
    """Loosened grade gate for RECALL only (not the final eligibility call — that is Phase 1).

    KEEP (return True) unless the row is *unambiguously* for students older than this one:
      * unknown student grade                     -> keep (unknown != ineligible)
      * no structured grade_min                   -> keep
      * grade_min <= student_grade                -> keep (student is old enough)
      * eligibility uses rising/age phrasing       -> keep (numeric bounds not trusted)
    Only DROP when grade_min is a real number strictly above the student's grade AND no
    escape phrasing applies. Deliberately does NOT drop on the upper bound (a too-old-for-the
    -program student is a rare, low-dilution case Phase 1 resolves; dropping it risks the same
    edge-case loss a strict cut causes)."""
    if student_grade is None:
        return True
    gmin = row.get("grade_min")
    if not isinstance(gmin, int):
        return True
    if student_grade >= gmin:
        return True
    # student is below the program's stated minimum grade -> older-only program.
    return _has_grade_escape_phrasing(row)


# --------------------------------------------------------------------------- geo scope
#
# A row is location-restricted only if it is IN-PERSON (or hybrid) AND tied to a specific
# state the student is not in. Remote rows are location-neutral. A row with no state, or a
# student with no known state, is never dropped here (unknown != ineligible) — the plan's
# residency ELIGIBILITY reasoning (Boston-Public-Schools-only, etc.) is Phase 1's live text
# job; this is only the coarse "don't let a far-away in-person local row crowd the pool"
# scope, the same tier as the status filter.
_REMOTE_LOCATIONS = {"remote", "in-person and remote", "in-person & remote", "online", "virtual"}

# The catalog stores 2-letter state codes (`WA`); a student's location is often spelled out
# (`Washington`). Normalize both to the 2-letter code before comparing, or an in-state
# in-person row is wrongly dropped for a "Washington" student. Full name <-> abbrev both ways.
_STATE_ABBREV = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA",
    "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington": "WA", "west virginia": "WV", "wisconsin": "WI",
    "wyoming": "WY", "district of columbia": "DC", "washington dc": "DC", "washington d.c.": "DC",
}


def _normalize_state(s):
    """A US state as its 2-letter code, upper-cased. Accepts a code or a full name; returns the
    stripped upper input unchanged if it is neither (so an unknown value still self-compares)."""
    if not s:
        return None
    t = str(s).strip()
    if not t:
        return None
    return _STATE_ABBREV.get(t.lower(), t.upper())


def geo_scope_ok(row: dict, student_state: str | None) -> bool:
    """Keep unless the row is an in-person, state-specific opportunity in a state other than
    the student's. Remote/hybrid rows, stateless rows, and unknown-student-state all pass."""
    location = (row.get("location") or "").strip().lower()
    if location in _REMOTE_LOCATIONS:
        return True  # remote/hybrid — location-neutral
    row_state = _normalize_state(row.get("state"))
    student = _normalize_state(student_state)
    if not row_state or not student:
        return True  # unknown either side -> never drop on geo
    return row_state == student


# --------------------------------------------------------------------------- cost / time scope
#
# cost + time are asked BEFORE recall (alongside interest), so they filter the catalog HERE and
# the top-`limit` pool is already affordable + available. Both are structured fields (`price`,
# `season`), so the check is exact; unknown values are NEVER cut ("unknown != ineligible", the
# same posture grade/geo take).

def _price_is_paid(row: dict) -> bool:
    p = row.get("price")
    s = str(p).strip().lower() if p is not None else ""
    if s in ("", "none", "null", "free", "$0", "0", "0.0", "no cost"):
        return False   # free or unknown -> not "paid"
    return True


def recall_cost_ok(row: dict, cost_pref: str | None) -> bool:
    """Free-only cuts CLEARLY-paid rows; any other value (incl. "any"/None) keeps everything."""
    if cost_pref != "free":
        return True
    return not _price_is_paid(row)


def _season_bucket(row: dict) -> str:
    s = str(row.get("season") or "").strip().lower()
    if not s or s in ("none", "null"):
        return "unknown"
    if "year" in s:            # Year-Long / Year-Round
        return "both"
    if "summer" in s:
        return "summer"
    return "school_year"       # Spring / Fall / Winter


def recall_time_ok(row: dict, time_pref: str | None) -> bool:
    """'summer'/'school_year' keep that season + year-round + unknown; any other value keeps all."""
    if time_pref not in ("summer", "school_year"):
        return True
    bucket = _season_bucket(row)
    if bucket in ("both", "unknown"):
        return True
    return bucket == time_pref


# --------------------------------------------------------------------------- cosine recall

def _to_matrix(vectors: list) -> np.ndarray:
    """Stack a list of equal-length float vectors into a 2-D float array. Empty -> (0,0)."""
    if not vectors:
        return np.zeros((0, 0), dtype=np.float64)
    return np.asarray(vectors, dtype=np.float64)


def best_theme_scores(row_vectors: np.ndarray, theme_vectors: np.ndarray) -> np.ndarray:
    """For each row vector, the MAX cosine similarity against any of the student's theme
    vectors. Vectorized (numpy/BLAS) — a naive Python triple loop over ~1500 rows x ~10
    themes x ~768 dims is ~1s; this is ~milliseconds. Returns a 1-D array, one score per row.

    A zero-norm vector (row or theme) contributes similarity 0 rather than a divide-by-zero:
    an all-zero embedding is a QA signal (a bad row), not a match.
    """
    n_rows = row_vectors.shape[0]
    if n_rows == 0 or theme_vectors.shape[0] == 0 or theme_vectors.shape[1] == 0:
        return np.zeros(n_rows, dtype=np.float64)
    rn = np.linalg.norm(row_vectors, axis=1, keepdims=True)
    tn = np.linalg.norm(theme_vectors, axis=1, keepdims=True)
    # Guard zero norms: replace 0 with 1 for the division, then zero those rows/cols back out.
    rn_safe = np.where(rn == 0, 1.0, rn)
    tn_safe = np.where(tn == 0, 1.0, tn)
    row_unit = row_vectors / rn_safe
    theme_unit = theme_vectors / tn_safe
    sims = row_unit @ theme_unit.T  # (n_rows, n_themes)
    # Kill contributions from any zero-norm vector so they read as 0 similarity, not NaN/1.
    sims[(rn == 0).ravel(), :] = 0.0
    sims[:, (tn == 0).ravel()] = 0.0
    return sims.max(axis=1)


# --------------------------------------------------------------------------- orchestration

RECALL_POOL_SIZE = 100


def recall(
    rows: list[dict],
    theme_vectors: list,
    student_grade: int | None = None,
    student_state: str | None = None,
    cost_pref: str | None = None,
    time_pref: str | None = None,
    limit: int = RECALL_POOL_SIZE,
) -> list[dict]:
    """Narrow the active catalog to the top-`limit` semantic matches for a student.

    `rows` are catalog rows, each expected to carry a `match_vector` (list[float]) — rows
    without one are dropped (they cannot be scored; an unembedded row is a gap, not a match).
    `theme_vectors` are the student's per-theme embeddings (one per profile theme). With no
    theme vectors (thin/empty profile) the semantic signal is absent, so this returns the
    filtered rows unscored/untruncated order-preserved — the caller falls back to whatever
    non-semantic ordering it wants; recall's contract is "never silently drop a feasible row".

    Pure: no I/O, no wall-clock. Ordering is by descending best-theme cosine; ties keep input
    order (stable sort)."""
    # 1. Objective filters — status, loosened grade, geo scope, and the pre-recall cost/time asks.
    survivors = [
        r for r in rows
        if (r.get("status") != "not_running")
        and recall_grade_ok(r, student_grade)
        and geo_scope_ok(r, student_state)
        and recall_cost_ok(r, cost_pref)
        and recall_time_ok(r, time_pref)
    ]

    tmat = _to_matrix([v for v in (theme_vectors or []) if v])
    if tmat.shape[0] == 0:
        # No semantic signal available — return the filtered set (capped), order preserved.
        return survivors[:limit]

    # 2. Keep only rows that actually have a usable embedding, then score.
    scorable = [r for r in survivors if isinstance(r.get("match_vector"), list) and r["match_vector"]]
    if not scorable:
        return survivors[:limit]
    rmat = _to_matrix([r["match_vector"] for r in scorable])
    if rmat.shape[1] != tmat.shape[1]:
        # Dimensionality mismatch means the row and theme vectors came from different embedding
        # models/dims — cosine between them is meaningless. Refuse to score rather than return
        # garbage; the caller/eval should catch this (a pinned single model prevents it).
        raise ValueError(
            f"embedding dim mismatch: rows={rmat.shape[1]} themes={tmat.shape[1]} "
            "(row and student vectors must come from the same pinned model)"
        )
    scores = best_theme_scores(rmat, tmat)
    ranked = sorted(zip(scores, range(len(scorable)), scorable), key=lambda t: (-t[0], t[1]))
    return [row for _, _, row in ranked[:limit]]

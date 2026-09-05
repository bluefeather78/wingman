"""Recall-query helpers for the main-style grid (docs/plans/RECALL_GRID_MERGE_PLAN.md).

The funnel-free half of what `match_pipeline` does on the branch: turn a student blob into the
embed texts, run recall, and attach the per-row cosine SCORE so the grid can show a
"strong match" badge. Deliberately does NOT import `funnel` / `curation` — this path never
curates to <=10; it returns the whole recall pool for the client grid.

Pure except for the injected embed function (so it is unit-testable with a stub). The paid
embed call and the cost banking live in the route.
"""
from __future__ import annotations

import os

import numpy as np

from app.services.matching import (
    recall, best_theme_scores, RECALL_POOL_SIZE, PROJECT_MATCH_BOOST, _to_matrix,
)

# Fields attached to each returned row so the client grid renders a card without a second
# lookup. `url` is included (the card needs it) even though the embedding did not use it.
RESULT_DISPLAY_FIELDS = (
    "id", "name", "org", "type", "summary", "url", "price", "location", "state", "intl",
    "season", "subject_tags", "review_status", "review_summary", "grade_min", "grade_max",
    "eligibility", "status",
)

# The fixed cosine cut for the "strong match" badge (docs/plans/RECALL_GRID_MERGE_PLAN.md decision 2).
# PROVISIONAL — calibrate against the live gemini-embedding-001 score distribution before
# trusting it (log real recall scores, pick the value that separates the on-lane cluster from
# the tail). Env-tunable so calibration needs no code change. A fixed bar (not top-N%) means
# "strong" has the same meaning on a broad profile and a thin one.
def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


STRONG_MATCH_MIN = _env_float("WINGMAN_STRONG_MATCH_MIN", 0.6)


def theme_embed_text(theme) -> str:
    """The text embedded for one profile theme. A theme is either a bare string or the
    {theme, intent, next_steps} shape the filterTags slot produces; compose the parts so the
    vector captures the interest AND what the student wants from it."""
    if isinstance(theme, str):
        return theme.strip()
    if not isinstance(theme, dict):
        return ""
    parts = [str(theme.get(k, "")).strip() for k in ("theme", "intent", "next_steps")]
    return ". ".join(p for p in parts if p)


# "Passion Project:" / "Research Project:" prefixes are metadata, not content — strip before
# embedding so the vector keys on what the project IS about, not the label.
_PROJECT_PREFIXES = ("Passion Project:", "Research Project:")


def project_embed_text(project) -> str:
    """The text embedded for one highlight project — the paragraph with its kind prefix stripped."""
    s = str(project or "").strip()
    for pre in _PROJECT_PREFIXES:
        if s.startswith(pre):
            return s[len(pre):].strip()
    return s


def student_embed_texts(student):
    """The ordered (theme_texts, project_texts) a student's blob embeds — the single definition
    of what goes into recall, so themes and projects are embedded in one call and split back."""
    theme_texts = [t for t in (theme_embed_text(x) for x in (student.get("profile_themes") or [])) if t]
    project_texts = [t for t in (project_embed_text(x) for x in (student.get("highlight_projects") or [])) if t]
    return theme_texts, project_texts


def _scores_by_id(pool, theme_vecs, project_vecs):
    """Best cosine score per pool row, computed the SAME way recall ranks (max over theme cosines
    and BOOSTED project cosines). Only the <=100 pool is scored, so this is milliseconds. Rows
    without a usable vector (or a thin profile with no query vectors) score 0.0."""
    scorable = [r for r in pool if isinstance(r.get("match_vector"), list) and r["match_vector"]]
    tmat = _to_matrix([v for v in (theme_vecs or []) if v])
    pmat = _to_matrix([v for v in (project_vecs or []) if v])
    if not scorable or (tmat.shape[0] == 0 and pmat.shape[0] == 0):
        return {}  # nothing to score, or no query vectors (thin profile) -> no meaningful score
    rmat = _to_matrix([r["match_vector"] for r in scorable])
    theme_scores = best_theme_scores(rmat, tmat) if tmat.shape[0] else np.zeros(len(scorable))
    project_scores = (best_theme_scores(rmat, pmat) * PROJECT_MATCH_BOOST
                      if pmat.shape[0] else np.zeros(len(scorable)))
    scores = np.maximum(theme_scores, project_scores)
    return {r.get("id"): float(s) for r, s in zip(scorable, scores)}


def recall_pool(rows, student, embed_themes_fn, recall_limit=RECALL_POOL_SIZE):
    """Embed the student's themes + highlight projects, run recall, and attach each survivor's
    cosine score. Returns (pool, embed_cost, scores_by_id). A thin/empty profile still gets a
    filtered, unscored pool (recall's contract); its scores map is empty and nothing is "strong".
    Themes + projects embed in ONE call, then split — project matches carry PROJECT_MATCH_BOOST."""
    theme_texts, project_texts = student_embed_texts(student)
    theme_vecs, project_vecs, embed_cost = ([], [], 0.0)
    if theme_texts or project_texts:
        vecs, embed_cost = embed_themes_fn(theme_texts + project_texts)
        theme_vecs = vecs[:len(theme_texts)]
        project_vecs = vecs[len(theme_texts):]
    location = student.get("location") or {}
    state = location.get("state") if isinstance(location, dict) else None
    pool = recall(rows, theme_vecs, student_grade=student.get("grade"),
                  student_state=state, limit=recall_limit, project_vectors=project_vecs)
    return pool, embed_cost, _scores_by_id(pool, theme_vecs, project_vecs)


def attach_display(pool, scores_by_id):
    """Shape each pool row for the client grid: the display fields + its cosine `score` and a
    `strong` badge (score >= the fixed cut). Order is recall order (best first)."""
    out = []
    for r in pool:
        score = scores_by_id.get(r.get("id"))
        out.append({
            **{k: r.get(k) for k in RESULT_DISPLAY_FIELDS},
            "score": score,
            "strong": bool(score is not None and score >= STRONG_MATCH_MIN),
        })
    return out

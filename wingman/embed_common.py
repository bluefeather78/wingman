#!/usr/bin/env python3
"""Content embeddings for duplicate detection. The similarity half of the new scraper gate.

Today's dedupe suppresses only on same-URL + similar-name (url_dedupe, tenet 10). The hard case it
misses is **the same program at a DIFFERENT URL** — `/alp/` vs `/accelerated-learning-program/`,
reorganised paths, a program reposted on a second domain. Name-similarity cannot fill the gap: of
96 same-site/diff-URL/name-similar catalog pairs, only 5 are true aliases and 91 are genuinely
different programs. Page CONTENT might separate those 5 from those 91; a URL and a name cannot.

This module is the plumbing: turn text into a vector (Gemini `gemini-embedding-001`), compare
vectors (cosine), and hold the catalog's vectors in a small on-disk index. Whether content
actually separates aliases from siblings is an EMPIRICAL question answered by `eval/dedupe_eval.py`
before any of this is wired live — if it does not separate, the dedupe axis stops there.

**No numpy, no pgvector, no SQL RPC.** ~1300 rows x a ~768-float vector is ~4 MB; loading it and
doing 1300 dot products is microseconds in pure Python, and the repo's offline agents are
stdlib-only. The index is a repo-root JSONL sidecar (the "file now, table later" shape
`wingman/discovered_leads.py` uses), one object per line: {"id", "vector", "rep", "embedded_at", "source"}.

The vector call is PAID (M9) — cheap (~$0.15 / 1M input tokens) but still a money seam, so gated
per run like everything else. Everything below the network functions is free and pure.
"""
import json
import math
import os
import time
import urllib.error
import urllib.request
from wingman import REPO_ROOT   # the repo root, defined once (see wingman/__init__.py)

# gemini-embedding-001: the current general-purpose Gemini embedding model. Like the generateContent
# model pin, these ids churn — reconfirm against the live model list if a call 404s.
EMBED_MODEL = "gemini-embedding-001"
_EMBED_URL_TMPL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:embedContent"
_BATCH_URL_TMPL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:batchEmbedContents"

# ~$0.15 per 1M input tokens for gemini-embedding-001 (pricing page). Like every cost figure in
# this repo it is estimated locally, not read from billing — the embed API does not return a token
# count, so tokens are approximated from text length below. Treat totals as a floor.
EMBED_PRICE_PER_TOKEN = 0.15 / 1_000_000
# One request cannot carry an unbounded string; a program page's readable text is truncated to the
# same window the classifier uses, so the two see the same page.
MAX_EMBED_CHARS = 14_000
# batchEmbedContents accepts many texts per call. Keep batches modest so one 400 does not sink a
# whole backfill; the caller re-batches the remainder.
DEFAULT_BATCH = 50
_DEFAULT_TIMEOUT = 60

LEADS_DIR = REPO_ROOT
DEFAULT_INDEX_PATH = os.path.join(LEADS_DIR, "catalog_embeddings.jsonl")


# --- cost (pure) ----------------------------------------------------------------------

def approx_tokens(text):
    """A conservative token estimate from characters. The embed API returns no usage, so cost has
    to be estimated from the input; ~4 chars/token is Gemini's own rule of thumb."""
    return max(1, len((text or "")[:MAX_EMBED_CHARS]) // 4)


def estimate_embed_cost(texts):
    """Dollar floor for embedding these texts. Pure."""
    if isinstance(texts, str):
        texts = [texts]
    return sum(approx_tokens(t) for t in texts) * EMBED_PRICE_PER_TOKEN


# --- vector math (pure) ---------------------------------------------------------------

def cosine(a, b):
    """Cosine similarity of two equal-length vectors, in [-1, 1]. 0.0 if either is degenerate."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def nearest(vector, index, top_k=3, min_score=0.0, exclude_ids=None):
    """The top_k index entries closest to `vector`. Pure — no I/O.

    Returns [(id, score, entry)] sorted by score descending, keeping only scores >= min_score.
    `exclude_ids` drops a candidate's own row so it never matches itself.
    """
    exclude = set(exclude_ids or ())
    scored = []
    for entry in index or []:
        rid = entry.get("id")
        if rid in exclude or not entry.get("vector"):
            continue
        s = cosine(vector, entry["vector"])
        if s >= min_score:
            scored.append((rid, s, entry))
    scored.sort(key=lambda t: t[1], reverse=True)
    return scored[:top_k]


# --- the on-disk index (pure I/O) -----------------------------------------------------

def load_index(path=DEFAULT_INDEX_PATH):
    """Every index entry on file. A malformed or vector-less line is skipped, never fatal."""
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict) and entry.get("id") and entry.get("vector"):
                out.append(entry)
    return out


def save_index(entries, path=DEFAULT_INDEX_PATH):
    """Rewrite the index, keeping the LAST entry per id (a re-embed supersedes an older vector)."""
    latest = {}
    for e in entries or []:
        if e.get("id") and e.get("vector"):
            latest[e["id"]] = e
    with open(path, "w", encoding="utf-8") as f:
        for e in latest.values():
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return len(latest)


def index_entry(row_id, vector, rep="", source=""):
    """One index record. Stamped with the day it was embedded so staleness is visible later."""
    return {"id": row_id, "vector": list(vector), "rep": rep, "source": source,
            "embedded_at": time.strftime("%Y-%m-%d")}


# --- the vector call (PAID — M9) ------------------------------------------------------

def _parse_embedding(data):
    """Pull the float vector out of an embedContent response, or [] if the shape is off."""
    emb = (data or {}).get("embedding") or {}
    return list(emb.get("values") or [])


def _parse_batch(data):
    """Pull the list of vectors out of a batchEmbedContents response."""
    return [list((e or {}).get("values") or []) for e in (data or {}).get("embeddings") or []]


def _post(url, body, api_key, timeout):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 429:
            time.sleep(5)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        raise


def embed_text(text, api_key, model=EMBED_MODEL, timeout=_DEFAULT_TIMEOUT):
    """Embed one string. Returns (vector, cost). PAID.

    MARQUEE M9: a money seam. Do not change model/pricing/whether this spends without approval.
    """
    text = (text or "")[:MAX_EMBED_CHARS]
    body = {"model": f"models/{model}", "content": {"parts": [{"text": text}]}}
    data = _post(_EMBED_URL_TMPL.format(model=model), body, api_key, timeout)
    return _parse_embedding(data), estimate_embed_cost(text)


def embed_batch(texts, api_key, model=EMBED_MODEL, timeout=_DEFAULT_TIMEOUT):
    """Embed a list of strings in ONE call. Returns (vectors, cost). PAID.

    Vectors come back positionally aligned to `texts`. A short-count response (some providers drop
    entries on partial failure) is padded with [] so the alignment the caller relies on holds.
    """
    texts = [(t or "")[:MAX_EMBED_CHARS] for t in texts]
    if not texts:
        return [], 0.0
    body = {"requests": [{"model": f"models/{model}", "content": {"parts": [{"text": t}]}}
                         for t in texts]}
    data = _post(_BATCH_URL_TMPL.format(model=model), body, api_key, timeout)
    vectors = _parse_batch(data)
    if len(vectors) < len(texts):
        vectors += [[]] * (len(texts) - len(vectors))
    return vectors[:len(texts)], estimate_embed_cost(texts)

"""Resolve ONE dedupe verdict per catalog row and (optionally) stamp `dup_verdict`. FREE.

Phase 1 of DEDUPE_SIMPLIFICATION_PLAN.md — the shadow-mode runner. It generates candidate pairs
from BOTH detection tracks and hands each to dup_verdict.resolve_dup_verdict (which fuses them via
dedupe_confidence into one label). It writes ONLY the new `dup_verdict` column and reads/changes
nothing else, so nothing in the app or console behaves differently yet — this exists to VERIFY the
one-verdict-per-row output against the live catalog before Phase 2 wires the console to it.

    Track A  url_dedupe.find_duplicates within each registrable-domain bucket (exact-URL,
             apply-URL, sub-page, same-domain name >= 0.82) — cheap, near-linear.
    Track B  embedding nearest-neighbours over the WHOLE catalog via one bulk matmul on the
             stored dedupe_vector index — catches the same program at a different URL / a barely
             overlapping name (the SYCCL case) that Track A can never see.

FREE: pure string logic + one in-memory matmul over vectors already in the DB. No model calls.
The reverted 18-minute per-row embedding SEARCH is avoided by doing a single matmul, not N index
lookups (measured 2026-09-02: 1,690 rows in ~33s, ~31s of which is the read).

Usage:
    python dedupe_resolve.py                 # preview: histogram + sample, writes nothing
    python dedupe_resolve.py --write         # ALSO PATCH dup_verdict (null when no duplicate)
    python dedupe_resolve.py --limit 200     # first N rows (debugging)
    python dedupe_resolve.py --id ec18702    # inspect one row's resolution in detail
"""
import argparse
import os
import sys
from collections import Counter, defaultdict

import numpy as np

import dedupe_confidence as dc  # noqa: F401  (imported so a missing dep fails loudly up front)
import dup_verdict as dv
import url_dedupe
from dedupe_embed_store import fetch_dedupe_index
from supabase_common import load_dotenv, supabase_get, supabase_patch

# The columns dedupe_confidence's discriminators read + moderation_status for context. The
# dedupe_vector is read SEPARATELY (fetch_dedupe_index, paged at 200) — a full-catalog select that
# includes the ~42KB/row vector exceeds Supabase's statement timeout (500), the trap CLAUDE.md and
# dedupe_embed_store both flag for this read.
_SELECT = ("id,name,org,url,type,season,grade_min,grade_max,price,"
           "is_active,moderation_status")

# Track-B neighbour generation: consider a row's top-K nearest by cosine above this floor as
# CANDIDATES (the resolver still judges each with the full engine; a low-cosine neighbour that is
# really unrelated comes back TIER_NONE and is dropped). The floor only bounds candidate volume.
_NN_FLOOR = 0.80
_NN_K = 10


def fetch_rows(url, key, limit=None):
    # ACTIVE rows ONLY. This is the ad-hoc catalog duplicate detector: its job is to find
    # active-vs-active duplicates already live in the catalog and publish them to the DUPLICATE
    # queue. It must NEVER touch pending (is_active=false) review-queue rows — those are deduped
    # at INSERT time by the detection path, and writing a verdict onto them here is exactly the
    # cross-contamination between the two queues this revamp exists to remove.
    rows = supabase_get(url, "opportunities", {"select": _SELECT, "is_active": "eq.true"}, key)
    if limit:
        rows = rows[:limit]
    return rows


def _domain_of(row):
    try:
        _, host, _, _ = url_dedupe.split_url(row.get("url") or "")
    except Exception:
        return None
    return url_dedupe.registrable_domain(host) if host else None


def build_candidate_sources(rows, vec_by_id):
    """Precompute the per-row candidate generators. Returns (domain_buckets, cos_matrix, index).

    cos_matrix[i, j] is the dedupe_vector cosine between rows i and j (NaN when either lacks a
    vector). index maps row id -> position i. `vec_by_id` maps id -> vector (list[float]).
    """
    index = {r["id"]: i for i, r in enumerate(rows)}
    buckets = defaultdict(list)
    for r in rows:
        d = _domain_of(r)
        if d:
            buckets[d].append(r)

    n = len(rows)
    have = np.zeros(n, dtype=bool)
    dim = None
    for i, r in enumerate(rows):
        v = vec_by_id.get(r["id"])
        if isinstance(v, list) and v:
            have[i] = True
            dim = dim or len(v)
    X = np.zeros((n, dim or 1), dtype=np.float32)
    for i, r in enumerate(rows):
        if have[i]:
            X[i] = vec_by_id[r["id"]]
    norms = np.linalg.norm(X, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    Xn = X / norms
    C = Xn @ Xn.T
    C[~have, :] = np.nan
    C[:, ~have] = np.nan
    return buckets, C, index


def candidates_for(row, i, rows, buckets, C, index):
    """The union of Track-A and Track-B candidate ROWS for `rows[i]`. Excludes self."""
    seen, out = {row["id"]}, []

    # Track A — url_dedupe within the row's own registrable-domain bucket (near-linear).
    dom = _domain_of(row)
    if dom:
        bucket = buckets.get(dom, [])
        exact, hints = url_dedupe.find_duplicates(
            row.get("url") or "", row.get("name") or "", bucket, include_weak=False)
        for cand in ([exact] if exact else []) + list(hints):
            cid = cand.get("id")
            if cid and cid not in seen:
                seen.add(cid)
                out.append(cand)

    # Track B — embedding nearest-neighbours over the whole catalog (the SYCCL backstop).
    row_cos = C[i]
    order = np.argsort(-np.nan_to_num(row_cos, nan=-1.0))
    added = 0
    for j in order:
        if added >= _NN_K:
            break
        c = row_cos[j]
        if np.isnan(c) or c < _NN_FLOOR:
            break
        cand = rows[j]
        if cand["id"] in seen:
            continue
        seen.add(cand["id"])
        out.append(cand)
        added += 1
    return out


def resolve_all(rows, buckets, C, index):
    verdicts = {}
    for i, row in enumerate(rows):
        cands = candidates_for(row, i, rows, buckets, C, index)

        def cosine_of(oid, _i=i):
            j = index.get(oid)
            if j is None:
                return None
            c = C[_i, j]
            return None if np.isnan(c) else float(c)

        verdicts[row["id"]] = dv.resolve_dup_verdict(row, cands, cosine_of)
    return verdicts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="PATCH dup_verdict (else preview only)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--id", help="inspect one row's resolution in detail")
    args = ap.parse_args()

    load_dotenv()
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        print("[ERROR] SUPABASE_URL / SUPABASE_SERVICE_KEY not set in .env.")
        sys.exit(1)

    print("reading active catalog…", flush=True)
    rows = fetch_rows(url, key, args.limit)
    print(f"  {len(rows)} rows", flush=True)
    print("reading dedupe vector index (paged)…", flush=True)
    vec_by_id = {e["id"]: e["vector"] for e in fetch_dedupe_index(url, key) if e.get("vector")}
    print(f"  {len(vec_by_id)} rows embedded", flush=True)
    buckets, C, index = build_candidate_sources(rows, vec_by_id)
    verdicts = resolve_all(rows, buckets, C, index)

    if args.id:
        row = next((r for r in rows if r["id"] == args.id), None)
        if not row:
            print(f"[not found] {args.id}")
            return
        v = verdicts.get(args.id)
        print(f"\n{args.id}  {row['name']}\n  url: {row['url']}")
        if not v:
            print("  verdict: (none — no suspected duplicate)")
        else:
            print(f"  verdict: {v.confidence.upper()}  ->  {v.duplicate_of} {v.name}")
            print(f"    url: {v.url}\n    tier={v.tier} cos={v.cosine} sibling={v.sibling}")
            print(f"    reasons: {v.reasons}")
        return

    labelled = {k: v for k, v in verdicts.items() if v}
    hist = Counter(v.confidence for v in labelled.values())
    sib = sum(1 for v in labelled.values() if v.sibling)
    print(f"\nrows with a suspected duplicate: {len(labelled)}/{len(rows)}")
    for lab in (dv.CONFIDENCE_CERTAIN, dv.CONFIDENCE_LIKELY, dv.CONFIDENCE_POSSIBLE):
        print(f"  {lab:8}: {hist.get(lab, 0)}")
    print(f"  (of which surfaced siblings: {sib})")

    ranked = sorted(labelled.items(),
                    key=lambda kv: (kv[1].cosine if kv[1].cosine is not None else -1), reverse=True)
    print("\nsample (highest-cosine first):")
    by_id = {r["id"]: r for r in rows}
    for rid, v in ranked[:25]:
        nm = (by_id[rid]["name"] or "")[:30]
        print(f"  [{v.confidence:8}] cos={v.cosine if v.cosine is not None else float('nan'):.3f} "
              f"{'sib ' if v.sibling else '    '}{nm:30} ({rid})  ->  {v.name[:30]} ({v.duplicate_of})")

    if not args.write:
        print("\n[preview] nothing written. Re-run with --write to stamp dup_verdict.")
        return

    print("\n[write] stamping dup_verdict (shadow mode — no other column touched)…", flush=True)
    wrote = 0
    for rid, v in verdicts.items():
        body = {"dup_verdict": v.as_dict() if v else None}
        try:
            supabase_patch(url, "opportunities", {"id": f"eq.{rid}"}, body, key)
            wrote += 1
        except Exception as e:
            print(f"  [warn] {rid}: {e}")
    print(f"[write] stamped {wrote}/{len(rows)} rows.")


if __name__ == "__main__":
    main()

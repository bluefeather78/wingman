#!/usr/bin/env python3
"""Build the catalog embedding index — the vector side of duplicate detection.

Embeds every active catalog row's FIELDS (name + org + type + summary + eligibility) into the
sidecar `catalog_embeddings.jsonl`, so the combined reader can, for each NEW candidate, find the
nearest existing row and attach a duplicate hint.

**Fields, not pages — and that is the whole practical win.** The 2026-08-30 eval chose the fields
representation over page text for two reasons: its high-cosine band is nearly pure duplicates, and
it needs NO page fetch — so this backfill embeds ~1500 stored rows with no catalog-wide crawl, it is
fast, and it covers rows whose page has since died (10% link rot). The index is built with
`combined_reader.default_representation`, the SAME function the reader queries with, so the index and
the query can never drift apart.

    python build_catalog_embeddings.py                 # FREE preview: row count + estimated cost
    python build_catalog_embeddings.py --commit        # PAID: embed the rows missing from the index
    python build_catalog_embeddings.py --commit --rebuild   # re-embed everything (e.g. rep changed)

Incremental by default: a row already in the index is skipped, so a second run only pays for what is
new. Embedding is PAID (M9) — cheap (~$0.15/1M tokens, ~$0.20 for the whole catalog) but a money
seam, so the write is gated behind --commit and each run needs fresh approval.
"""
import argparse
import os

import embed_common
from combined_reader import default_representation

# The Supabase columns the fields representation needs. is_active drives the active-only default.
_SELECT = "id,name,org,type,summary,eligibility,is_active"


def row_representation(row):
    """The exact text embedded for one row — the reader's own fields representation. Pure."""
    return default_representation(row, "")


def select_rows_to_embed(rows, existing_ids, rebuild=False):
    """The rows that actually need embedding: those with a non-empty representation and (unless
    rebuild) not already in the index. Pure — the testable core of the incremental logic."""
    existing = set(existing_ids or ())
    out = []
    for r in rows or []:
        if not r.get("id") or not row_representation(r).strip():
            continue
        if not rebuild and r["id"] in existing:
            continue
        out.append(r)
    return out


def _fetch_rows(include_inactive=False):
    from supabase_common import supabase_get, load_dotenv
    load_dotenv()
    su = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_ANON_KEY")
    if not su or not key:
        raise SystemExit("[ERROR] SUPABASE_URL and a key must be set in .env.")
    rows = supabase_get(su, "opportunities", {"select": _SELECT}, key) or []
    return rows if include_inactive else [r for r in rows if r.get("is_active")]


def _gemini_key():
    from supabase_common import load_dotenv
    load_dotenv()
    k = os.environ.get("GEMINI_API_KEY")
    if not k:
        raise SystemExit("[ERROR] GEMINI_API_KEY must be set in .env to embed (--commit).")
    return k


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true",
                    help="PAID: embed the rows and write the index. Without it, a FREE preview.")
    ap.add_argument("--rebuild", action="store_true",
                    help="Re-embed EVERY row, not just those missing from the index.")
    ap.add_argument("--include-inactive", action="store_true",
                    help="Also embed is_active=false rows (default: active catalog only).")
    ap.add_argument("--limit", type=int, help="Cap the rows embedded this run (a pilot).")
    ap.add_argument("--out", default=embed_common.DEFAULT_INDEX_PATH, help="Index file path.")
    args = ap.parse_args()

    rows = _fetch_rows(include_inactive=args.include_inactive)
    existing = embed_common.load_index(args.out)
    existing_ids = {e["id"] for e in existing}
    todo = select_rows_to_embed(rows, existing_ids, rebuild=args.rebuild)
    if args.limit:
        todo = todo[:args.limit]

    est = embed_common.estimate_embed_cost([row_representation(r) for r in todo])
    print(f"[OK] {len(rows)} row(s) fetched; index holds {len(existing_ids)}; "
          f"{len(todo)} to embed this run. Estimated cost ~${est:.4f}.")
    if not todo:
        print("[OK] Nothing to embed — index is up to date.")
        return
    if not args.commit:
        print("\n[PREVIEW] free. Re-run with --commit to embed and write the index (PAID).")
        return

    api_key = _gemini_key()
    # Start from the existing index and append this run's vectors. save_index keeps the LAST entry
    # per id, so a re-embed (rebuild, or a changed row) supersedes its old vector while rows not
    # touched this run — including inactive ones — are preserved. One path serves both modes.
    entries, total_cost = list(existing), 0.0
    B = embed_common.DEFAULT_BATCH
    for i in range(0, len(todo), B):
        chunk = todo[i:i + B]
        vectors, cost = embed_common.embed_batch([row_representation(r) for r in chunk], api_key)
        total_cost += cost
        for r, v in zip(chunk, vectors):
            if v:
                entries.append(embed_common.index_entry(r["id"], v, rep="fields",
                                                         source="catalog"))
        print(f"    embedded {min(i + B, len(todo))}/{len(todo)}  (~${total_cost:.4f})")

    written = embed_common.save_index(entries, args.out)
    print(f"\n[OK] Index written: {written} row(s) at {args.out}. Spend this run ~${total_cost:.4f}.")


if __name__ == "__main__":
    main()

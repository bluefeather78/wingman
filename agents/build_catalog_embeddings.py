#!/usr/bin/env python3
"""Build / backfill the catalog DEDUPE embeddings — the vector side of duplicate detection.

Embeds every active catalog row's FIELDS (name + org + type + summary + eligibility) into the
`opportunities.dedupe_vector` column, so the scraper gate can, for each NEW candidate, find the
nearest existing row and attach a duplicate hint. New scraped rows embed at ACTIVATION via the
console hook (ops/core._index_activated_rows); this script is the ad-hoc catch-up that fills in
whatever the hook missed (rows activated before the migration, or a text edit that changed the
representation).

**Fields, not pages — and that is the whole practical win.** The 2026-08-30 eval chose the fields
representation over page text: its high-cosine band is nearly pure duplicates, and it needs NO page
fetch — so this backfill embeds ~1500 stored rows with no catalog-wide crawl, it is fast, and it
covers rows whose page has since died (10% link rot). The representation and its freshness hash come
from dedupe_embed_store, the SAME functions the activation hook and the scraper query with, so the
stored index and the query can never drift apart.

    python -m agents.build_catalog_embeddings --dry-run        # FREE: count what needs embedding + est cost
    python -m agents.build_catalog_embeddings --yes-really     # PAID: embed the stale/missing rows + write
    python -m agents.build_catalog_embeddings --dry-run --limit 50

Incremental by content hash: a row is embedded only when its dedupe_vector_hash differs from the
current field values, so a second run right after a first does nothing, and a refresh_opportunities
edit self-heals on the next pass. Embedding is PAID (M9) — cheap (~$0.15/1M tokens, ~$0.20 for the
whole catalog) but a money seam, so the write is gated behind --yes-really and each run needs fresh
approval. See MARQUEE_DECISIONS.md M9.
"""
import argparse
import datetime
import os
import sys

from wingman import embed_common
from wingman.dedupe_embed_store import (dedupe_representation, rows_needing_dedupe_embedding,
                                DEDUPE_SELECT_FIELDS)

WRITE_CHUNK = 100  # embed + PATCH in chunks so a mid-run failure doesn't lose the whole pass


def row_representation(row):
    """The exact text embedded for one row — the dedupe store's fields representation. Pure.
    Kept as a thin alias so the activation hook and tests have one name to call."""
    return dedupe_representation(row)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="Count + estimate, write nothing (FREE).")
    ap.add_argument("--yes-really", action="store_true",
                    help="PAID: embed the stale/missing rows and write the dedupe_vector column.")
    ap.add_argument("--include-inactive", action="store_true",
                    help="Also embed is_active=false rows (default: active catalog only).")
    ap.add_argument("--limit", type=int, default=None, help="Cap the rows embedded this run (a pilot).")
    args = ap.parse_args()
    if not args.dry_run and not args.yes_really:
        ap.error("pass --dry-run to preview, or --yes-really to embed + write (PAID)")

    from wingman.supabase_common import load_dotenv, supabase_get, supabase_patch
    load_dotenv()
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_ANON_KEY")
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    if not url or not key:
        print("[ERROR] SUPABASE_URL and a key must be set in .env.")
        sys.exit(1)
    if not args.dry_run and not gemini_key:
        print("[ERROR] GEMINI_API_KEY not set — cannot embed (--yes-really).")
        sys.exit(1)

    try:
        params = {"select": DEDUPE_SELECT_FIELDS}
        if not args.include_inactive:
            params["is_active"] = "eq.true"
        rows = supabase_get(url, "opportunities", params, key) or []
    except Exception as e:
        # A 400 here is almost always the migration not being run yet — name the file.
        print(f"[ERROR] Could not read the catalog: {e}")
        print("        If this is a missing-column error, run db/dedupe_vector_schema.sql first.")
        sys.exit(1)

    needing = rows_needing_dedupe_embedding(rows)
    if args.limit is not None:
        needing = needing[:args.limit]

    est = embed_common.estimate_embed_cost([row_representation(r) for r, _ in needing])
    print(f"[OK] {len(rows)} row(s) fetched; {len(needing)} need (re)embedding this run "
          f"(model {embed_common.EMBED_MODEL}). Estimated cost ~${est:.4f}.")
    if not needing:
        print("[OK] Nothing to embed — every row's dedupe vector is current.")
        return
    if args.dry_run:
        for r, _ in needing[:15]:
            print(f"     would embed {r['id']}  {str(r.get('name'))[:55]}")
        if len(needing) > 15:
            print(f"     ... and {len(needing) - 15} more")
        print("\n[DRY RUN] No writes performed. Re-run with --yes-really to embed + write (PAID).")
        return

    written, errors, spent = 0, 0, 0.0
    B = WRITE_CHUNK
    for start in range(0, len(needing), B):
        chunk = needing[start:start + B]
        texts = [row_representation(r) for r, _ in chunk]
        try:
            vectors, cost = embed_common.embed_batch(texts, gemini_key)
            spent += cost
        except Exception as e:
            errors += len(chunk)
            print(f"[ERROR] embed batch @{start} failed: {e}")
            continue
        stamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
        for (r, current_hash), vec in zip(chunk, vectors):
            if not vec:
                errors += 1
                print(f"[WARN] {r['id']}: empty vector, skipped (not persisted).")
                continue
            try:
                supabase_patch(url, "opportunities", {"id": f"eq.{r['id']}"}, {
                    "dedupe_vector": vec,
                    "dedupe_vector_hash": current_hash,
                    "dedupe_vector_computed_at": stamp,
                }, key)
                written += 1
            except Exception as e:
                errors += 1
                print(f"[ERROR] PATCH {r['id']}: {e}")
        print(f"     ...{min(start + B, len(needing))}/{len(needing)}  (~${spent:.4f})")

    print(f"\n[OK] Embedded {written} row(s), {errors} error(s). Spend this run ~${spent:.4f}.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""One-time (and re-runnable) backfill of opportunities.match_vector — the embeddings the
curated-matching recall stage needs (docs/plans/OPPORTUNITY_MATCHING_PLAN.md, Phase 5).

MARQUEE M9: this makes paid Gemini embedding calls. Approved for the matching pipeline.
It is idempotent and cheap to re-run: a row is embedded only when its match_vector_hash
(the hash of name+org+summary+subject_tags+type) differs from the current field values, so
a second run right after a first does nothing. That is also how it self-heals after
refresh_opportunities.py edits a row's text — the next backfill re-embeds exactly those rows.

Reuses app.services.matching.embed_text / match_vector_content_hash so the backfill and the
runtime activation hook compute the SAME text and the SAME hash — if they diverged, every row
would look permanently stale and re-embed forever.

Reads ACTIVE rows only (recall serves active rows). New scraped rows embed at ACTIVATION via
the hook, not here.

USAGE:
    python backfill_match_vectors.py --dry-run        # count what needs embedding + est cost
    python backfill_match_vectors.py --yes-really     # embed + write (paid)
    python backfill_match_vectors.py --dry-run --limit 50
"""
import argparse
import datetime
import os
import sys

from supabase_common import load_dotenv, supabase_get, supabase_patch
from app.services.matching import embed_text, match_vector_content_hash
from app.services.embeddings import should_recompute_embedding
from gemini_common import call_gemini_embed, estimate_embed_cost, EMBED_MODEL

# Only the fields embed_text/hash read, plus the stored hash — deliberately NOT match_vector
# itself (that would pull ~9MB of float text for nothing; the hash tells us what we need).
SELECT_FIELDS = "id,name,org,summary,subject_tags,type,match_vector_hash"
WRITE_CHUNK = 100  # embed + PATCH in chunks so a mid-run failure doesn't lose the whole pass


def rows_needing_embedding(rows):
    """[(row, current_hash)] for active rows whose stored hash differs from current content.
    Pure — the selection half of the backfill, unit-tested without the network."""
    out = []
    for r in rows:
        current = match_vector_content_hash(r)
        if should_recompute_embedding(True, r.get("match_vector_hash"), current):
            out.append((r, current))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="Count + estimate, write nothing.")
    ap.add_argument("--yes-really", action="store_true", help="Perform the paid embed + write.")
    ap.add_argument("--limit", type=int, default=None, help="Cap rows processed (testing).")
    args = ap.parse_args()
    if not args.dry_run and not args.yes_really:
        ap.error("pass --dry-run to preview, or --yes-really to embed + write")

    load_dotenv()
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    if not url or not key:
        print("[ERROR] SUPABASE_URL / SUPABASE_SERVICE_KEY not set in .env.")
        sys.exit(1)
    if not args.dry_run and not gemini_key:
        print("[ERROR] GEMINI_API_KEY not set — cannot embed.")
        sys.exit(1)

    try:
        rows = supabase_get(url, "opportunities",
                            {"select": SELECT_FIELDS, "is_active": "eq.true"}, key) or []
    except Exception as e:
        print(f"[ERROR] Could not read the catalog: {e}")
        sys.exit(1)

    needing = rows_needing_embedding(rows)
    if args.limit is not None:
        needing = needing[:args.limit]

    print(f"[OK] {len(rows)} active rows; {len(needing)} need (re)embedding "
          f"(model {EMBED_MODEL}).")
    if needing:
        approx_tokens = sum(max(1, len(embed_text(r)) // 4) for r, _ in needing)
        est = estimate_embed_cost({"input_tokens": approx_tokens})
        print(f"     Estimated embedding cost: ~${est:.4f} (~{approx_tokens} input tokens).")

    if not needing:
        print("[OK] Nothing to do — every active row's embedding is current.")
        return
    if args.dry_run:
        for r, _ in needing[:15]:
            print(f"     would embed {r['id']}  {str(r.get('name'))[:55]}")
        if len(needing) > 15:
            print(f"     ... and {len(needing) - 15} more")
        print("[DRY RUN] No writes performed.")
        return

    written, errors, spent = 0, 0, 0.0
    for start in range(0, len(needing), WRITE_CHUNK):
        chunk = needing[start:start + WRITE_CHUNK]
        texts = [embed_text(r) for r, _ in chunk]
        try:
            vectors, usage = call_gemini_embed(texts, gemini_key)
            spent += estimate_embed_cost(usage)
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
                    "match_vector": vec,
                    "match_vector_hash": current_hash,
                    "match_vector_computed_at": stamp,
                }, key)
                written += 1
            except Exception as e:
                errors += 1
                print(f"[ERROR] PATCH {r['id']}: {e}")
        print(f"     ...{min(start + WRITE_CHUNK, len(needing))}/{len(needing)}")

    print(f"[OK] Embedded {written} row(s), {errors} error(s). Est spend ${spent:.4f}.")


if __name__ == "__main__":
    main()

"""Report duplicate rows already inside the opportunities table. Free, READ-ONLY.

Retired 2026-08-30: this used to run three hand-tuned heuristic cuts (exact URL, a name-
similarity ratio, and an acronym/token-overlap pass). All three were reasoning toward the same
thing embeddings already do better — url_dedupe's own measurements found the name-similarity
cut wrong often enough to be "eyeballed only", and the token/acronym cut existed purely to catch
what a plain ratio missed. Both are now one embedding nearest-neighbor pass through the SAME
`dedupe_confidence` tier engine dedupe_queue.py uses for the review queue, so a catalog pair and
a queue pair are judged identically instead of by two different rulebooks.

Two passes, both free — no embedding calls happen here, only vector comparisons against the
prebuilt index (`catalog_embeddings.jsonl`, built by build_catalog_embeddings.py / the console's
"Refresh Dedupe Embeddings", and kept current automatically: every activation embeds its own row):

  1. Exact match_key collision — two active rows whose URL normalizes identically. Free, and
     found even for a row missing from the index. Judged HERE, not delegated to
     dedupe_confidence.classify_rows: a bare URL match is PROOF only when the names also
     agree — a shared multi-program application portal (spicestanford.smapply.io hosts six
     distinct Stanford programs) shares one stored URL across genuinely different rows. A
     differing name still surfaces, as a HINT asking a human to confirm it isn't a shared
     portal, rather than being silently dropped or (the 2026-08-30 bug this fixed) wrongly
     reported as certain-duplicate PROOF regardless of name.
  2. Embedding nearest-neighbor — for every row the index holds a vector for, its closest OTHER
     active row by cosine, tiered PROOF / CONFIDENT / ADJUDICATE / SIBLING / HINT / NONE by
     dedupe_confidence.classify_rows. Only PROOF/CONFIDENT/ADJUDICATE/HINT are reported — SIBLING
     is a discriminator-confirmed DIFFERENT program, NONE isn't similar enough to matter.

Rows the index has no vector for are reported as `unembedded`, never silently skipped — run
"Refresh Dedupe Embeddings" first if that list is nonzero and matters for this pass.

Nothing here writes. Catalog changes remain a human decision (prefer the console's
duplicate/reject actions over SQL DELETE — see scraper_tombstones.json for why).

Usage:
    python find_catalog_dups.py [--json out.json]
"""

import argparse
import json
import os
import sys

from supabase_common import load_dotenv, supabase_get
import url_dedupe
import embed_common
import dedupe_confidence as dc

# The columns dedupe_confidence's discriminators read (name/org for identity, the hard fields for
# the conflict check) plus moderation_status so a caller can rank keep/flag candidates.
_SELECT = ("id,name,org,url,type,season,grade_min,grade_max,price,"
           "is_active,moderation_status")

# SIBLING is a discriminator-confirmed DIFFERENT program; NONE isn't similar enough to matter.
# Same set dedupe_queue.py surfaces for the review queue, so the two pipelines agree on what
# counts as "worth a human's time".
_SURFACE_TIERS = (dc.TIER_PROOF, dc.TIER_CONFIDENT, dc.TIER_ADJUDICATE, dc.TIER_HINT)

_TIER_ORDER = {dc.TIER_PROOF: 0, dc.TIER_CONFIDENT: 1, dc.TIER_ADJUDICATE: 2, dc.TIER_HINT: 3}


def fetch_all_rows(supabase_url, key):
    """Active rows only — this scan is live-catalog-vs-live-catalog. The review queue's
    equivalent (pending-vs-catalog) is dedupe_queue.py; the two never overlap in scope.
    supabase_get paginates internally via Range headers, so this must NOT add its own
    limit/offset loop on top (double pagination 416s once the window passes the table's end)."""
    return supabase_get(supabase_url, "opportunities", {
        "select": _SELECT, "is_active": "eq.true", "order": "id.asc",
    }, key)


def _match_key(url):
    """url_dedupe.match_key, degrading to '' on a malformed URL instead of raising — a
    catalog row's URL is user/model-entered and not guaranteed parseable."""
    try:
        return url_dedupe.match_key(url or "")
    except ValueError:
        return ""


def find_duplicate_pairs(rows, index=None, top_k=1):
    """The whole scan. Returns (pairs, unembedded_ids).

    Each pair is {"rows": (a, b), "tier": <TIER_*>, "reasons": [...], "cosine": float|None},
    tier restricted to `_SURFACE_TIERS`. `index` defaults to the prebuilt on-disk index
    (embed_common.load_index()) but takes an explicit list so this stays unit-testable with no
    disk or network access, matching the "every model call is injected" rule the rest of the
    dedupe stack follows.

    Pure other than the index default load; no network calls, no writes.
    """
    if index is None:
        index = embed_common.load_index()
    by_id = {r["id"]: r for r in rows if r.get("id")}
    vec = {e["id"]: e["vector"] for e in index if e.get("id") in by_id and e.get("vector")}
    unembedded = [rid for rid in by_id if rid not in vec]

    seen = set()
    pairs = []

    def surface(a, b, tier, reasons, cosine):
        if a["id"] == b["id"]:
            return
        key = tuple(sorted([a["id"], b["id"]]))
        if key in seen or tier not in _SURFACE_TIERS:
            return
        seen.add(key)
        pairs.append({"rows": (a, b), "tier": tier, "reasons": reasons, "cosine": cosine})

    # Pass 1: exact URL collisions. Free, and catches a pair even when one or both rows are
    # missing from the embedding index. Judged directly here, NOT via dc.classify_rows: a bare
    # URL match is proof only when the names also agree (dc.classify_rows enforces that same
    # guard now), but a differing name is still worth a human's look — it is exactly the
    # spicestanford.smapply.io shape, one portal URL hosting six distinct programs — so it is
    # surfaced as a HINT with an explicit warning rather than silently dropped.
    by_key = {}
    for r in rows:
        k = _match_key(r.get("url"))
        if k:
            by_key.setdefault(k, []).append(r)
    for members in by_key.values():
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                nr = dc.name_relation(a.get("name"), a.get("org"), b.get("name"), b.get("org"))
                if nr == dc.NAME_SAME:
                    surface(a, b, dc.TIER_PROOF, ["identical URL, same name"], cosine=None)
                else:
                    surface(a, b, dc.TIER_HINT,
                           ["identical URL, different name — confirm it is not a shared "
                            "application portal"], cosine=None)

    # Pass 2: embedding nearest-neighbor, restricted to rows the index actually holds a vector
    # for. `search` excludes anything not in `rows` (e.g. a row deactivated since the index was
    # last built) so a stale index entry can never surface a pair for a row we didn't fetch.
    search = [e for e in index if e.get("id") in by_id and e.get("vector")]
    for rid, v0 in vec.items():
        hits = embed_common.nearest(v0, search, top_k=top_k, min_score=dc.HINT_FLOOR,
                                    exclude_ids={rid})
        for mid, cos, _entry in hits:
            a, b = by_id[rid], by_id[mid]
            v = dc.classify_rows(a, b, cosine=cos)
            surface(a, b, v.tier, v.reasons, cosine=cos)

    return pairs, unembedded


def _fmt_row(r):
    return f"{r['id']} {r.get('name')}  |  {r.get('url')}"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", default=None, help="Also write the report here.")
    args = parser.parse_args()

    load_dotenv()
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_ANON_KEY", "")
    if not supabase_url or not key:
        print("[ERROR] SUPABASE_URL / key not set in .env.")
        sys.exit(1)

    rows = fetch_all_rows(supabase_url, key)
    print(f"[OK] {len(rows)} active row(s) loaded.")

    pairs, unembedded = find_duplicate_pairs(rows)
    pairs.sort(key=lambda p: (_TIER_ORDER[p["tier"]], -(p["cosine"] or 1.0)))
    print(f"\n=== {len(pairs)} duplicate pair(s) === "
          f"({len(unembedded)} row(s) not yet in the dedupe index"
          + (' — run "Refresh Dedupe Embeddings" to cover them)' if unembedded else ')'))
    for p in pairs:
        a, b = p["rows"]
        print(f"\n  [{p['tier'].upper()}] {', '.join(p['reasons'])}")
        print(f"    {_fmt_row(a)}")
        print(f"    {_fmt_row(b)}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump([{"tier": p["tier"], "reasons": p["reasons"], "cosine": p["cosine"],
                       "rows": list(p["rows"])} for p in pairs],
                      f, indent=1, ensure_ascii=False)
        print(f"\n[OK] wrote {args.json}")
    print("\n[NOTE] Report only — nothing was written. Consolidate via the console's "
          "duplicate/reject actions, not SQL DELETE (deletes need tombstones).")


if __name__ == "__main__":
    main()

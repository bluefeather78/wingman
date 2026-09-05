"""Report duplicate rows already inside the opportunities table. Free, READ-ONLY.

PARTLY SUPERSEDED 2026-09-02 (dedupe revamp Phase 3): the console's 'Scan for duplicates' button
now runs `dedupe_resolve.run` (via `ops.core.scan_catalog_duplicates`), which resolves ONE
`dup_verdict` per active row instead of this module's heuristic pair report — and closes this
module's blind spot (its candidate generation never pairs same-program rows whose names barely
overlap, the SYCCL case). But `find_duplicate_pairs` is STILL imported by `agents/dedupe_queue.py`, the
live 'Dedupe the Review Queue' tool, so this file is NOT dead and cannot be deleted until the
review-queue dedupe path is also migrated (the deferred insert-time half of Phase 4). Do not
build NEW callers on it. See docs/plans/DEDUPE_SIMPLIFICATION_PLAN.md.


2026-08-30: this was briefly rewritten to a pure embedding nearest-neighbor sweep (search every
active row against every other), then reverted the same day once that sweep was measured live —
1686 active rows, 3072-dim vectors (gemini-embedding-001) — at ~0.66s per row's search, i.e.
~18.6 minutes for the full catalog. wingman/embed_common.py's own "1300 dot products is microseconds"
claim assumed a ~768-dim model; a full O(n^2) sweep at today's dimensionality is not a "free, run
whenever" operation.

Back to the ORIGINAL three heuristic cuts (exact URL, same-domain name-similarity ratio,
acronym/token overlap) for what they were always good at: cheap, near-linear CANDIDATE
generation over the whole catalog (domain bucketing and an inverted token index, not O(n^2)).
What changed is the VERDICT: each candidate pair is now tiered by the SAME dedupe_confidence
engine (PROOF / CONFIDENT / ADJUDICATE / SIBLING / HINT / NONE) agents/dedupe_queue.py uses for the
review queue, using a cosine looked up directly from the stored embedding index (the
`opportunities.dedupe_vector` column) when both rows are embedded — one dot product per CANDIDATE,
not a search over the whole index. A catalog with a few hundred candidate pairs costs a few hundred
dot products, regardless of how large the catalog itself is.

Cut 1 (exact URL) is judged directly here rather than delegated to classify_rows: a bare match on
the rows' STORED url is proof only when the names also agree — a shared multi-program application
portal (spicestanford.smapply.io hosts six distinct Stanford programs) shares one stored URL
across genuinely different rows. A differing name still surfaces, as a HINT asking a human to
confirm it isn't a shared portal, rather than being silently dropped or (a live bug, fixed
2026-08-30) wrongly reported as certain-duplicate PROOF regardless of name. Cuts 2 and 3 route
through dedupe_confidence.classify_rows with whatever cosine is available (None is fine — the
free discriminators alone still separate a SIBLING from a real duplicate).

Rows the index has no vector for are reported as `unembedded`, never silently skipped — a
candidate involving one is still tiered (from the free discriminators alone, cosine=None), just
without the embedding signal's help. Run "Refresh Dedupe Embeddings" to close that gap.

Nothing here writes. Catalog changes remain a human decision (prefer the console's
duplicate/reject actions over SQL DELETE — see scraper_tombstones.json for why).

Usage:
    python -m wingman.find_catalog_dups [--json out.json]
"""

import argparse
import json
import os
import sys
from collections import defaultdict

from wingman.supabase_common import load_dotenv, supabase_get
from wingman import url_dedupe
from wingman import embed_common
from wingman import dedupe_confidence as dc

# The columns dedupe_confidence's discriminators read (name/org for identity, the hard fields for
# the conflict check) plus moderation_status so a caller can rank keep/flag candidates.
_SELECT = ("id,name,org,url,type,season,grade_min,grade_max,price,"
           "is_active,moderation_status")

# SIBLING is a discriminator-confirmed DIFFERENT program; NONE isn't similar enough to matter.
# Same set agents/dedupe_queue.py surfaces for the review queue, so the two pipelines agree on what
# counts as "worth a human's time".
_SURFACE_TIERS = (dc.TIER_PROOF, dc.TIER_CONFIDENT, dc.TIER_ADJUDICATE, dc.TIER_HINT)

_TIER_ORDER = {dc.TIER_PROOF: 0, dc.TIER_CONFIDENT: 1, dc.TIER_ADJUDICATE: 2, dc.TIER_HINT: 3}


def fetch_all_rows(supabase_url, key):
    """Active rows only — this scan is live-catalog-vs-live-catalog. The review queue's
    equivalent (pending-vs-catalog) is agents/dedupe_queue.py; the two never overlap in scope.
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


# ---------- candidate generation: cheap, near-linear, no embeddings ----------

def key_collision_groups(rows):
    """CANDIDATES only (cut 1): groups of rows sharing one match_key (identical normalized URL).
    O(n) — one dict grouping pass."""
    by_key = defaultdict(list)
    for r in rows:
        k = _match_key(r.get("url"))
        if k:
            by_key[k].append(r)
    return [members for members in by_key.values() if len(members) >= 2]


def name_hint_candidates(rows):
    """CANDIDATES only (cut 2): same-domain pairs with name similarity >= 0.90, excluding pairs
    cut 1 already owns. O(n) grouping by domain, then pairwise only WITHIN each (typically small)
    domain group — not O(n^2) over the whole catalog."""
    by_domain = defaultdict(list)
    for r in rows:
        _, host, _, _ = url_dedupe.split_url(r.get("url") or "")
        d = url_dedupe.registrable_domain(host)
        if d:
            by_domain[d].append(r)
    out = []
    for members in by_domain.values():
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                ka, kb = _match_key(a.get("url")), _match_key(b.get("url"))
                if ka and ka == kb:
                    continue  # cut 1 already owns this pair
                if url_dedupe.name_similarity(a.get("name"), b.get("name")) >= 0.90:
                    out.append((a, b))
    return out


# Cut 3 tuning. Candidate generation only — the tier comes from dedupe_confidence below, so the
# bar here is "worth tiering", not "certainly a duplicate".
_COMMON_TOKEN_DF = 30    # a token on more rows than this is a category word ('summer'), not an
                         # identity — skipped for candidate generation (keeps this near-linear)
_JACCARD_MIN = 0.6
_MIN_SHARED = 2
_ACRONYM_MIN, _ACRONYM_MAX = 3, 8
_MAX_EXTRA_CANDIDATES = 400


def _row_tokens(r):
    """Distinctive name tokens: connectors, 'program' and institution words dropped, len>=2.
    Reuses url_dedupe's own splitters so this and the reject path agree on identity words."""
    return [t for t in url_dedupe._distinctive_tokens(r.get("name"))
            if t not in url_dedupe._INSTITUTION_WORDS and len(t) >= 2]


def _host_of(r):
    _, host, _, _ = url_dedupe.split_url(r.get("url") or "")
    return host


def extra_name_candidates(rows):
    """CANDIDATES only (cut 3): the pairs cuts 1/2 miss — ACRONYM<->expansion and token-set
    overlap, both allowed to cross domains. An inverted token index keeps this near-linear
    instead of O(n^2): only rows sharing a discriminative token even get compared."""
    info = []
    for r in rows:
        nm = url_dedupe.normalize_name(r.get("name"))
        if not nm or nm in url_dedupe.GENERIC_NAMES or len(nm) < 4:
            info.append(None)
            continue
        toks = _row_tokens(r)
        info.append({
            "row": r, "toks": toks, "set": set(toks), "host": _host_of(r),
            "acr": "".join(t[0] for t in toks) if len(toks) >= 3 else "",
        })

    postings = defaultdict(list)
    for idx, it in enumerate(info):
        if it:
            for t in it["set"]:
                postings[t].append(idx)
    discriminative = {t: idxs for t, idxs in postings.items() if len(idxs) <= _COMMON_TOKEN_DF}

    seen, out = set(), []

    def emit(i, j):
        key = (i, j) if i < j else (j, i)
        if key in seen:
            return
        seen.add(key)
        a, b = info[i], info[j]
        if (url_dedupe._is_bare_institution(a["row"].get("name"), a["host"])
                and url_dedupe._is_bare_institution(b["row"].get("name"), b["host"])):
            return
        out.append((a["row"], b["row"]))

    for i, it in enumerate(info):
        if not it:
            continue
        cand = defaultdict(int)
        for t in it["set"]:
            for j in discriminative.get(t, ()):
                if j > i:
                    cand[j] += 1
        for j, shared in cand.items():
            if shared < _MIN_SHARED:
                continue
            sj = info[j]["set"]
            inter = it["set"] & sj
            union = it["set"] | sj
            if len(inter) < _MIN_SHARED or not union:
                continue
            if len(inter) / len(union) < _JACCARD_MIN:
                continue
            emit(i, j)
        if len(out) >= _MAX_EXTRA_CANDIDATES:
            return out[:_MAX_EXTRA_CANDIDATES]

    by_acr = defaultdict(list)
    for idx, it in enumerate(info):
        if it and it["acr"]:
            by_acr[it["acr"]].append(idx)
    for idx, it in enumerate(info):
        if not it or len(it["toks"]) != 1:
            continue
        cand = it["toks"][0]
        if not (_ACRONYM_MIN <= len(cand) <= _ACRONYM_MAX):
            continue
        for j in by_acr.get(cand, []):
            if j != idx:
                emit(idx, j)
        if len(out) >= _MAX_EXTRA_CANDIDATES:
            break

    return out[:_MAX_EXTRA_CANDIDATES]


# ---------- the scan: candidates in, dedupe_confidence-tiered verdicts out ----------

def find_duplicate_pairs(rows, index=None):
    """The whole scan. Returns (pairs, unembedded_ids).

    Each pair is {"rows": (a, b), "tier": <TIER_*>, "reasons": [...], "cosine": float|None},
    tier restricted to `_SURFACE_TIERS`. `index` defaults to the prebuilt on-disk index
    (the DB dedupe_vector columns) but takes an explicit list so this stays unit-testable with no
    disk or network access, matching the "every model call is injected" rule the rest of the
    dedupe stack follows.

    Pure other than the index default load; no network calls, no writes, and — the whole point —
    no O(n^2) embedding search: a cosine is looked up as a single dot product between two known
    vectors for each CANDIDATE the heuristic passes propose, never a nearest-neighbor search over
    the whole index.
    """
    if index is None:
        from wingman import dedupe_embed_store
        index = dedupe_embed_store.fetch_dedupe_index_from_env()
    by_id = {r["id"]: r for r in rows if r.get("id")}
    vec = {e["id"]: e["vector"] for e in index if e.get("id") in by_id and e.get("vector")}
    unembedded = [rid for rid in by_id if rid not in vec]

    def cosine_for(a, b):
        va, vb = vec.get(a["id"]), vec.get(b["id"])
        return embed_common.cosine(va, vb) if va and vb else None

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

    # Cut 1: exact URL collision. Judged directly, not via classify_rows: a bare stored-URL
    # match is PROOF only when the names also agree; a differing name is still worth a human's
    # look (a shared application portal), so it surfaces as a HINT rather than vanishing.
    for members in key_collision_groups(rows):
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

    # Cut 2: same-domain, name >= 0.90 similar. Tiered by dedupe_confidence with a real cosine
    # when both rows are embedded.
    for a, b in name_hint_candidates(rows):
        cos = cosine_for(a, b)
        v = dc.classify_rows(a, b, cosine=cos)
        surface(a, b, v.tier, v.reasons, cos)

    # Cut 3: acronym / token-overlap, cross-host. Same tiering.
    for a, b in extra_name_candidates(rows):
        cos = cosine_for(a, b)
        v = dc.classify_rows(a, b, cosine=cos)
        surface(a, b, v.tier, v.reasons, cos)

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

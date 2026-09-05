#!/usr/bin/env python3
"""The go/no-go gate for the content-embedding dedupe axis. Measure BEFORE building.

The question is narrow and decisive: does page CONTENT separate the 5 true aliases from the 91
genuinely-different-but-name-similar siblings in the catalog? URL cannot (different URLs). Name
cannot (that is what makes them the 96-pair problem). If content cannot either, the dedupe axis
stops here — learned for ~$0.10 instead of after shipping a false-positive machine.

The make-or-break risk is **institutional boilerplate**: two different CMU pre-college programs
share the chrome, the apply block, the footer, the tuition language. A naive full-page embedding
can read that shared boilerplate as a duplicate — and the 96 pairs are exactly siblings at the
same institution, so this set is a direct stress test of that failure.

So we bake off three REPRESENTATIONS of a page and see which, if any, separates the two
populations, and at what cosine threshold:

    fields       name + org + type + summary + eligibility   (from the catalog row; no fetch)
    page         the chrome-stripped readable page text      (one free HTTP fetch per row)
    descriptor   a model-normalized one-line "what is this"  (paid; --with-descriptor only)

Ground truth: the pair is an ALIAS if either row is one of the 5 the operator confirmed
(`KNOWN_ALIAS_IDS`); every other qualifying pair is a DISTINCT sibling. A richer hand-label set can
be supplied with --labels (JSONL of {"a","b","label"}); it overrides the id heuristic per pair.

    python dedupe_eval.py                 # FREE preview: which pairs, which labels, no embedding
    python dedupe_eval.py --run           # PAID: fetch + embed + report separation per rep
    python dedupe_eval.py --run --with-descriptor   # + the paid descriptor rep

FREE to import and unit-test (pair generation, labelling, the separation metric are all pure).
`--run` is PAID (embeddings; and one model call per row with --with-descriptor). Gated per run.
"""
import argparse
import json
import os

import sys
# This script lives under eval/ but imports the repo-root shared
# libraries (dedupe_confidence, dedupe_embed_store, embed_common, gemini_common, page_text, supabase_common, url_dedupe) by bare name, the way every
# root script does. Running it as `python eval/dedupe_eval.py` puts its OWN directory on
# sys.path, not the repo root, so the root has to be added explicitly.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import url_dedupe

# The 5 pairs the operator confirmed as true aliases in the deferred 96-pair analysis (`/alp/`
# 301s to `/accelerated-learning-program/`, etc.). Each id anchors one alias pair; a qualifying
# pair containing one of these is labelled ALIAS, everything else DISTINCT.
KNOWN_ALIAS_IDS = {"ec18774", "ec18771", "ec18856", "ec18918", "ec18865"}

# The same name-similarity floor the 96-pair analysis used. A pair below this is not name-similar
# enough to be the problem this axis targets, so it is not in the eval set.
NAME_SIM_FLOOR = 0.85

LABEL_ALIAS = "alias"
LABEL_DISTINCT = "distinct"

REP_FIELDS = "fields"
REP_PAGE = "page"
REP_DESCRIPTOR = "descriptor"

# MARQUEE M8: eval-ONLY prompt, exercised solely by --with-descriptor. If the descriptor
# representation wins the bake-off, this wording is what would be folded into classify_page's
# prompt (a fresh M8 approval at that point) — it is deliberately not in the shipped classifier yet.
DESCRIPTOR_SYSTEM = """\
Given the readable text of one web page for a high-school opportunity, write ONE line that \
canonically identifies the opportunity it is about, ignoring page chrome, navigation, and \
boilerplate. Format: "<official program name> — <one clause on what it is>, run by <organization>". \
Use only what the page states. Return just that one line, no JSON, no extra text.
"""


# --- pair generation and labelling (pure) ---------------------------------------------

def _key(url):
    try:
        return url_dedupe.match_key(url or "")
    except ValueError:
        return ""


def _domain(url):
    from urllib.parse import urlsplit
    try:
        return url_dedupe.registrable_domain(urlsplit(url or "").hostname or "")
    except ValueError:
        return ""


def candidate_pairs(rows, name_floor=NAME_SIM_FLOOR):
    """Every same-registrable-domain, different-URL, name-similar pair. Pure.

    This reconstructs the 96-pair set deterministically from the live catalog, so the eval needs
    no stored fixture — it re-derives its own population every time it runs.
    """
    by_domain = {}
    for r in rows or []:
        d = _domain(r.get("url"))
        if d:
            by_domain.setdefault(d, []).append(r)
    pairs = []
    for group in by_domain.values():
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                if _key(a.get("url")) == _key(b.get("url")):
                    continue  # same URL is the EXISTING dedupe's job, not this axis
                if url_dedupe.name_similarity(a.get("name"), b.get("name")) >= name_floor:
                    pairs.append((a, b))
    return pairs


def label_pair(a, b, overrides=None):
    """ALIAS if either row is a known alias (or an override says so), else DISTINCT. Pure."""
    if overrides:
        key = frozenset((a.get("id"), b.get("id")))
        if key in overrides:
            return overrides[key]
    if a.get("id") in KNOWN_ALIAS_IDS or b.get("id") in KNOWN_ALIAS_IDS:
        return LABEL_ALIAS
    return LABEL_DISTINCT


def load_label_overrides(path):
    """{frozenset(a,b): label} from a JSONL hand-label file, or {} if none. Pure I/O."""
    if not path or not os.path.exists(path):
        return {}
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("a") and rec.get("b") and rec.get("label") in (LABEL_ALIAS, LABEL_DISTINCT):
                out[frozenset((rec["a"], rec["b"]))] = rec["label"]
    return out


# --- representations (pure) -----------------------------------------------------------

def repr_fields(row):
    """The structured-fields representation of a row — no fetch needed. Pure."""
    parts = [row.get("name"), row.get("org"), row.get("type"),
             row.get("summary"), row.get("eligibility")]
    return "\n".join(str(p) for p in parts if p)


# --- the separation metric (pure) -----------------------------------------------------

def separation(labeled_scores):
    """Given [(label, score)], how well does score separate ALIAS from DISTINCT? Pure.

    Returns a dict with the two score ranges, the clean gap (min alias minus max distinct; positive
    means perfectly separable), and the best threshold by Youden's J with its confusion counts.
    """
    pos = sorted(s for lab, s in labeled_scores if lab == LABEL_ALIAS)
    neg = sorted(s for lab, s in labeled_scores if lab == LABEL_DISTINCT)
    out = {"n_alias": len(pos), "n_distinct": len(neg),
           "alias_min": pos[0] if pos else None, "alias_max": pos[-1] if pos else None,
           "distinct_min": neg[0] if neg else None, "distinct_max": neg[-1] if neg else None,
           "clean_gap": (pos[0] - neg[-1]) if pos and neg else None,
           "best_threshold": None, "tp": 0, "fp": 0, "fn": len(pos), "tn": len(neg),
           "precision": None, "recall": None, "youden_j": None}
    if not pos or not neg:
        return out
    best_j, best = -1.0, None
    for t in sorted({round(s, 6) for _, s in labeled_scores}):
        tp = sum(1 for s in pos if s >= t)
        fp = sum(1 for s in neg if s >= t)
        fn = len(pos) - tp
        tn = len(neg) - fp
        tpr = tp / len(pos)
        fpr = fp / len(neg)
        j = tpr - fpr
        if j > best_j:
            best_j, best = j, (t, tp, fp, fn, tn)
    t, tp, fp, fn, tn = best
    prec = tp / (tp + fp) if (tp + fp) else None
    out.update({"best_threshold": t, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
                "precision": prec, "recall": tp / len(pos), "youden_j": round(best_j, 4)})
    return out


def format_report(rep_name, sep):
    lines = [f"  [{rep_name}]  aliases={sep['n_alias']}  distinct={sep['n_distinct']}"]
    if sep["n_alias"] and sep["n_distinct"]:
        lines.append(f"      alias cosine   {sep['alias_min']:.3f} .. {sep['alias_max']:.3f}")
        lines.append(f"      distinct cosine{sep['distinct_min']:.3f} .. {sep['distinct_max']:.3f}")
        lines.append(f"      clean gap = {sep['clean_gap']:+.3f}  "
                     f"({'perfectly separable' if sep['clean_gap'] > 0 else 'overlap'})")
        lines.append(f"      best threshold {sep['best_threshold']:.3f}: "
                     f"catches {sep['tp']}/{sep['n_alias']} aliases, "
                     f"false-flags {sep['fp']}/{sep['n_distinct']} distinct  "
                     f"(precision {sep['precision'] if sep['precision'] is None else round(sep['precision'],3)}, "
                     f"J={sep['youden_j']})")
    else:
        lines.append("      not enough of one class to measure separation")
    return "\n".join(lines)


# --- the paid run (network lives here) ------------------------------------------------

def _fetch_rows():
    """The catalog rows, from Supabase (free). Returns just the rows — the Gemini key for the
    embedding/model calls is read separately (`_gemini_key`); they are different credentials."""
    from supabase_common import supabase_get, load_dotenv
    load_dotenv()
    su = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_ANON_KEY")
    if not su or not key:
        raise SystemExit("[ERROR] SUPABASE_URL and a key must be set in .env.")
    params = {"select": "id,name,org,url,type,summary,eligibility,is_active,moderation_status"}
    return supabase_get(su, "opportunities", params, key) or []


def _gemini_key():
    """The Gemini API key for the PAID embedding/descriptor calls — NOT the Supabase key."""
    from supabase_common import load_dotenv
    load_dotenv()
    k = os.environ.get("GEMINI_API_KEY")
    if not k:
        raise SystemExit("[ERROR] GEMINI_API_KEY must be set in .env for --run.")
    return k


def _embed_all(texts, api_key, timeout=120):
    """Embed a list of texts in chunks of embed_common.DEFAULT_BATCH. Returns (vectors, cost)."""
    import embed_common
    vectors, cost = [], 0.0
    for i in range(0, len(texts), embed_common.DEFAULT_BATCH):
        chunk = texts[i:i + embed_common.DEFAULT_BATCH]
        vs, c = embed_common.embed_batch(chunk, api_key, timeout=timeout)
        vectors += vs
        cost += c
    return vectors, cost


def _signals(pairs, labels):
    """FREE: run the name + field discriminators over the pairs, no embedding. Shows how many
    high-similarity pairs the free signals would pull OUT as siblings vs confirm as duplicates."""
    import dedupe_confidence as dc
    buckets = {}
    print(f"\n[SIGNALS] name/field discriminators over {len(pairs)} pairs (FREE, no embedding):\n")
    for a, b in pairs:
        nr = dc.name_relation(a.get("name"), a.get("org"), b.get("name"), b.get("org"))
        fr, cf = dc.field_relation(a, b)
        conflict = nr == dc.NAME_CONFLICT or fr == dc.FIELD_CONFLICT
        bucket = "SIBLING (conflict)" if conflict else f"name={nr}"
        buckets[bucket] = buckets.get(bucket, 0) + 1
        mark = " <ALIAS>" if labels[frozenset((a.get('id'), b.get('id')))] == LABEL_ALIAS else ""
        tag = "SIBLING" if conflict else nr.upper()
        print(f"  {tag:9} name={nr:8} field={fr:8}{('/' + ','.join(cf)) if cf else '':14}"
              f" {(a.get('name') or '')[:30]!r} ~ {(b.get('name') or '')[:30]!r}{mark}")
    print("\n[SUMMARY] " + ", ".join(f"{k}={v}" for k, v in sorted(buckets.items())))
    print("  SIBLING pairs are pulled OUT of the auto/dup set by a free signal the embedding missed;")
    print("  name=same pairs are the confident-duplicate candidates.")


def _tiers(pairs, labels):
    """FREE: the FULL tier pipeline (cosine from the built index + discriminators) over the pairs.
    Measures how many pairs land in each tier — the CONFIDENT/PROOF count is the auto-merge set, and
    printing them lets the operator eyeball the false-positive rate before auto-merge is turned on."""
    import embed_common
    import dedupe_confidence as dc
    import dedupe_embed_store
    index = {e["id"]: e["vector"] for e in dedupe_embed_store.fetch_dedupe_index_from_env()}
    if not index:
        print("[ERROR] No catalog dedupe index — run build_catalog_embeddings.py --yes-really first.")
        return
    by_tier = {}
    detail = {dc.TIER_CONFIDENT: [], dc.TIER_SIBLING: [], dc.TIER_ADJUDICATE: []}
    for a, b in pairs:
        va, vb = index.get(a.get("id")), index.get(b.get("id"))
        cos = embed_common.cosine(va, vb) if va and vb else None
        v = dc.classify_rows(a, b, cosine=cos)  # runs discriminators + proof + context guard
        by_tier[v.tier] = by_tier.get(v.tier, 0) + 1
        if v.tier in detail:
            al = " <ALIAS>" if labels[frozenset((a.get('id'), b.get('id')))] == LABEL_ALIAS else ""
            detail[v.tier].append(f"      {cos if cos is None else round(cos,3)}  "
                                  f"{(a.get('name') or '')[:32]!r} ~ {(b.get('name') or '')[:32]!r}{al}")
    print(f"\n[TIERS] full pipeline (index cosine + discriminators) over {len(pairs)} pairs:\n")
    for t in (dc.TIER_CONFIDENT, dc.TIER_ADJUDICATE, dc.TIER_SIBLING, dc.TIER_HINT, dc.TIER_NONE):
        print(f"  {t:11} {by_tier.get(t, 0)}")
    print(f"\n  --- CONFIDENT (auto-merge candidates -- eyeball for false positives) ---")
    print("\n".join(detail[dc.TIER_CONFIDENT]) or "      (none)")
    print(f"\n  --- SIBLING (pulled OUT of the dup set by a discriminator) ---")
    print("\n".join(detail[dc.TIER_SIBLING]) or "      (none)")


def _run(args):
    import page_text
    import embed_common

    rows = _fetch_rows()
    if not args.include_inactive:
        # The active, CURATED catalog. The 96-pair analysis lived here; the pending hub-batch rows
        # (is_active=false) are full of unresolved dupes that would pollute the "distinct" bucket
        # and make separation read worse than it is. The 5 known aliases are active (unfixed).
        rows = [r for r in rows if r.get("is_active")]
    overrides = load_label_overrides(args.labels)
    pairs = candidate_pairs(rows)
    labels = {frozenset((a.get("id"), b.get("id"))): label_pair(a, b, overrides) for a, b in pairs}
    n_alias = sum(1 for v in labels.values() if v == LABEL_ALIAS)
    print(f"[OK] {len(pairs)} candidate pair(s) over {len(rows)} rows "
          f"({'all' if args.include_inactive else 'active only'}): "
          f"{n_alias} alias, {len(pairs) - n_alias} distinct.")
    if args.signals:
        _signals(pairs, labels)
        return
    if args.tiers:
        _tiers(pairs, labels)
        return
    if not args.run:
        for a, b in pairs:
            lab = labels[frozenset((a.get('id'), b.get('id')))]
            print(f"    {lab:8} {a.get('id')} <-> {b.get('id')}  "
                  f"{(a.get('name') or '')[:40]!r} ~ {(b.get('name') or '')[:40]!r}")
        print("\n[PREVIEW] free. Re-run with --run to fetch, embed, and score (PAID), "
              "or --signals for the FREE discriminator measurement.")
        return

    api_key = _gemini_key()

    # Unique rows that appear in any pair — only these need fetching/embedding.
    involved = {r.get("id"): r for pair in pairs for r in pair}
    reps = [REP_FIELDS, REP_PAGE] + ([REP_DESCRIPTOR] if args.with_descriptor else [])
    total_cost = 0.0
    # rep -> {id: text}
    texts = {rep: {} for rep in reps}
    page_cache = {}
    for rid, row in involved.items():
        texts[REP_FIELDS][rid] = repr_fields(row)
        if REP_PAGE in reps or REP_DESCRIPTOR in reps:
            txt, _reason = page_text.fetch_page_text(row.get("url"), timeout=args.timeout)
            page_cache[rid] = txt or ""
            texts[REP_PAGE][rid] = page_cache[rid]
    if REP_DESCRIPTOR in reps:
        import gemini_common
        for rid in involved:
            page = page_cache.get(rid, "")
            if not page:
                texts[REP_DESCRIPTOR][rid] = texts[REP_FIELDS][rid]
                continue
            out, usage = gemini_common.call_gemini(DESCRIPTOR_SYSTEM, page[:14000], api_key,
                                                   use_web_search=False, max_tokens=200,
                                                   timeout=args.timeout)
            total_cost += gemini_common.estimate_cost(usage or {})
            texts[REP_DESCRIPTOR][rid] = (out or "").strip() or texts[REP_FIELDS][rid]

    name_of = {rid: (r.get("name") or "")[:34] for rid, r in involved.items()}
    print(f"\n[OK] Representations built for {len(involved)} rows. Embedding...")
    for rep in reps:
        # Only embed rows whose rep text is non-empty. A failed page fetch would otherwise embed
        # an empty string, and two empty pages would read as similar — a false duplicate.
        ids = [i for i in texts[rep] if (texts[rep][i] or "").strip()]
        vectors, cost = _embed_all([texts[rep][i] for i in ids], api_key)
        total_cost += cost
        vec = {i: v for i, v in zip(ids, vectors)}
        scored, rich = [], []
        for a, b in pairs:
            va, vb = vec.get(a.get("id")), vec.get(b.get("id"))
            if va and vb:
                lab = labels[frozenset((a.get('id'), b.get('id')))]
                s = embed_common.cosine(va, vb)
                scored.append((lab, s))
                rich.append((s, lab, a.get("id"), b.get("id")))
        print(format_report(rep, separation(scored)))
        # The raw truth: every pair by cosine, so the natural break is visible on named examples
        # rather than hidden behind one label-dependent number. ALIAS rows are marked.
        rich.sort(reverse=True)
        print(f"      --- all {len(rich)} pairs by {rep} cosine (high=similar) ---")
        for s, lab, ida, idb in rich:
            mark = " <== ALIAS" if lab == LABEL_ALIAS else ""
            print(f"      {s:.3f}  {name_of.get(ida,'?')!r:36} ~ {name_of.get(idb,'?')!r:36}{mark}")

    print(f"\n[COST] ~${total_cost:.4f} spent this run.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true",
                    help="PAID: fetch pages, embed each representation, and score separation. "
                         "Without it, a FREE preview of the pairs and labels.")
    ap.add_argument("--signals", action="store_true",
                    help="FREE: run the name/field discriminators over the pairs (no embedding).")
    ap.add_argument("--tiers", action="store_true",
                    help="FREE: full tier pipeline (cosine from the built index + discriminators).")
    ap.add_argument("--with-descriptor", action="store_true",
                    help="Also test the model-descriptor representation (adds one paid call/row).")
    ap.add_argument("--labels", help="Optional JSONL of hand labels {a,b,label} overriding ids.")
    ap.add_argument("--include-inactive", action="store_true",
                    help="Include is_active=false rows (default: active/curated catalog only). "
                         "The pending hub-batch rows are full of unresolved dupes.")
    ap.add_argument("--timeout", type=int, default=20)
    args = ap.parse_args()
    _run(args)


if __name__ == "__main__":
    main()

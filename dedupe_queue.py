#!/usr/bin/env python3
"""Run the dedupe confidence logic over the CURRENT review queue. Read-only unless --write.

For every pending row, find its nearest match — across BOTH the active catalog and the other pending
rows (the hub batch left intra-queue duplicates too) — and report the tier the confidence logic
would assign: PROOF / CONFIDENT (would auto-merge) / ADJUDICATE (LLM judge) / SIBLING (kept, not a
dup) / HINT (human) / NONE (a genuinely new program).

The only cost is embedding the ~279 queue rows (a fraction of a cent); the active catalog is already
in `catalog_embeddings.jsonl`. Nothing is written to Supabase — this only tells you what the logic
WOULD judge.

    python dedupe_queue.py            # embeds the queue, prints per-row tier verdicts (no writes)
    python dedupe_queue.py --preview  # FREE: just the counts + estimated embed cost
    python dedupe_queue.py --write    # ALSO stamps a dup_candidates back-link onto matched rows

`--write` PATCHes a dup_candidates entry (pointing at the survivor, tagged `via: content-embedding`)
onto each pending row whose nearest match reaches a surfaced tier (proof/confident/adjudicate/hint),
so the review console shows a 'possible duplicate of' back-link with the tier. It replaces only its
own prior entries -- url_dedupe's submission-time candidates are left untouched.

The LIVE catalog (active-vs-active) is a separate job — see find_catalog_dups.py, which the
Run -> Duplicates -> "Scan for duplicates" button calls; it shares this module's
`dedupe_confidence` tier engine but reads vectors straight from the prebuilt index instead of
re-embedding, so it costs nothing per run.
"""
import argparse
import os

import embed_common
import dedupe_confidence as dc
import queue_flags
from combined_reader import default_representation

_SELECT = ("id,name,org,type,summary,eligibility,url,is_active,moderation_status,"
           "dup_candidates,quality_flags")
_QUEUE_STATUSES = (None, "", "pending_review")

# Tiers worth showing an operator as a "possible duplicate of" back-link. SIBLING is judged a
# DISTINCT program, so surfacing it as a possible duplicate would mislead; NONE has no match at all.
_SURFACE_TIERS = (dc.TIER_PROOF, dc.TIER_CONFIDENT, dc.TIER_ADJUDICATE, dc.TIER_HINT)


def _fetch_all():
    from supabase_common import supabase_get, load_dotenv
    load_dotenv()
    su = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_ANON_KEY")
    if not su or not key:
        raise SystemExit("[ERROR] SUPABASE_URL and a key must be set in .env.")
    return supabase_get(su, "opportunities", {"select": _SELECT}, key) or [], (
        os.environ.get("GEMINI_API_KEY"))


def _match_type(row):
    return "active" if row.get("is_active") else "queue"


def _write_candidate(su, key, prow, survivor, tier, cosine):
    """PATCH the pending row's dup_candidates so the console shows a 'possible duplicate of'
    back-link to the survivor. Replaces this module's own prior entries, keeps url_dedupe's."""
    from supabase_common import supabase_patch
    cand = queue_flags.dedupe_candidate(survivor, tier, cosine)
    merged = queue_flags.merge_candidates(prow.get("dup_candidates"), [cand])
    supabase_patch(su, "opportunities", {"id": f"eq.{prow.get('id')}"},
                   {"dup_candidates": merged}, key)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true", help="FREE: counts + estimated cost only.")
    ap.add_argument("--top-k", type=int, default=3)
    ap.add_argument("--write", action="store_true",
                    help="Persist each surfaced verdict as a dup_candidates entry (pointing at the "
                         "survivor) so the review console shows a 'possible duplicate of' back-link. "
                         "Read-only without it. Idempotent -- replaces only its own prior entries.")
    ap.add_argument("--classified-only", action="store_true",
                    help="Only judge (and, with --write, stamp) rows already carrying a `classify:` "
                         "flag -- i.e. the rows classify_queue.py has triaged. Each is still matched "
                         "against the WHOLE active catalog; only the queue-vs-queue search space "
                         "narrows to this set.")
    args = ap.parse_args()

    rows, gemini_key = _fetch_all()
    by_id = {r["id"]: r for r in rows if r.get("id")}
    queue = [r for r in rows if not r.get("is_active")
             and (r.get("moderation_status") in _QUEUE_STATUSES)]
    if args.classified_only:
        queue = [r for r in queue
                 if any(str(f).startswith(queue_flags.CLASSIFY_PREFIX)
                        for f in (r.get("quality_flags") or []))]
    reps = {r["id"]: default_representation(r, "") for r in queue}
    queue = [r for r in queue if reps[r["id"]].strip()]

    est = embed_common.estimate_embed_cost([reps[r["id"]] for r in queue])
    active_index = embed_common.load_index()
    print(f"[OK] {len(queue)} queue row(s); active index holds {len(active_index)}. "
          f"Embedding the queue costs ~${est:.4f}.")
    if not active_index:
        print("[ERROR] No catalog index — run build_catalog_embeddings.py --commit first.")
        return
    if args.preview:
        print("\n[PREVIEW] free. Re-run without --preview to embed the queue and judge (PAID ~cent).")
        return
    if not gemini_key:
        raise SystemExit("[ERROR] GEMINI_API_KEY must be set in .env.")

    su = sk = None
    if args.write:
        su = os.environ.get("SUPABASE_URL", "").rstrip("/")
        sk = os.environ.get("SUPABASE_SERVICE_KEY")
        if not su or not sk:
            raise SystemExit("[ERROR] --write needs SUPABASE_URL and SUPABASE_SERVICE_KEY in .env.")

    # Embed the queue and add it to the search space, so a pending row can match either an active
    # row (a re-discovery) or another pending row (an intra-queue twin from the hub batch).
    B = embed_common.DEFAULT_BATCH
    qvec = {}
    for i in range(0, len(queue), B):
        chunk = queue[i:i + B]
        vs, _c = embed_common.embed_batch([reps[r["id"]] for r in chunk], gemini_key)
        qvec.update({r["id"]: v for r, v in zip(chunk, vs) if v})
    search = active_index + [embed_common.index_entry(rid, v) for rid, v in qvec.items()]

    tally, detail = {}, {dc.TIER_PROOF: [], dc.TIER_CONFIDENT: [], dc.TIER_ADJUDICATE: [],
                        dc.TIER_SIBLING: []}
    written = 0
    for p in queue:
        pv = qvec.get(p["id"])
        hits = embed_common.nearest(pv, search, top_k=args.top_k, min_score=dc.HINT_FLOOR,
                                    exclude_ids={p["id"]}) if pv else []
        if not hits:
            tally[dc.TIER_NONE] = tally.get(dc.TIER_NONE, 0) + 1
            continue
        mid, cos, _e = hits[0]
        m = by_id.get(mid, {})
        # classify_rows runs every free signal: proof (same URL), name + field discriminators, and
        # the same-institution context guard (so a generic name across orgs can't reach CONFIDENT).
        v = dc.classify_rows(p, m, cosine=cos)
        tally[v.tier] = tally.get(v.tier, 0) + 1
        if args.write and v.tier in _SURFACE_TIERS and m.get("id"):
            try:
                _write_candidate(su, sk, p, m, v.tier, cos)
                written += 1
            except Exception as e:  # a failed write must not abort the run mid-queue
                print(f"      [WRITE FAILED] {p.get('id')}: {type(e).__name__}: {e}")
        if v.tier in detail:
            detail[v.tier].append(
                f"      {p['id']} {(p.get('name') or '')[:30]!r}  ->  {mid} [{_match_type(m)}] "
                f"{(m.get('name') or '')[:30]!r}   cos={cos:.3f} {' '.join(v.reasons[1:])}")

    print(f"\n[QUEUE DEDUPE VERDICTS] {len(queue)} pending rows:\n")
    for t in (dc.TIER_PROOF, dc.TIER_CONFIDENT, dc.TIER_ADJUDICATE, dc.TIER_SIBLING,
              dc.TIER_HINT, dc.TIER_NONE):
        print(f"  {t:11} {tally.get(t, 0)}")
    for t, label in ((dc.TIER_PROOF, "PROOF -- same normalized URL (certain duplicate)"),
                     (dc.TIER_CONFIDENT, "CONFIDENT -- would auto-merge (high sim + name-same)"),
                     (dc.TIER_ADJUDICATE, "ADJUDICATE -- send to LLM judge (qualifier/discrepancy)"),
                     (dc.TIER_SIBLING, "SIBLING -- kept as a distinct program")):
        print(f"\n  --- {label} ---")
        print("\n".join(detail[t]) or "      (none)")
    auto = tally.get(dc.TIER_PROOF, 0) + tally.get(dc.TIER_CONFIDENT, 0)
    print(f"\n  {auto} of {len(queue)} would auto-resolve; "
          f"{tally.get(dc.TIER_NONE, 0)} look like genuinely new programs.")
    if args.write:
        print(f"  [WROTE] {written} row(s) stamped with a dup_candidates back-link "
              f"(the review console now shows the tier).")


if __name__ == "__main__":
    main()

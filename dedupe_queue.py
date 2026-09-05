#!/usr/bin/env python3
"""Run the dedupe confidence logic over a set of rows. Read-only unless --write.

PARTLY SUPERSEDED (dedupe revamp, docs/plans/DEDUPE_SIMPLIFICATION_PLAN.md): the ONE-verdict resolver
(`dup_verdict.resolve_dup_verdict`) is replacing this tool's back-link writes in phases. As of
Phase 3 the catalog/`--source flagged` role is handled by `dedupe_resolve.run` behind the
console's Scan button. The `--source queue` (pending review-queue) role is STILL LIVE here — it
was to move to INSERT time in Phase 4, but that half is DEFERRED (it spans four paid feeders and
needs a paid validation run), so this remains the review queue's dedupe tool. Not deletable yet;
do not build new callers.


Two independent selections feed the SAME embedding + dedupe_confidence tier engine (`--source`):

    queue    (default) the REVIEW QUEUE — pending rows (is_active=false). For each, find its
             nearest match across BOTH the active catalog and the other pending rows (the hub
             batch left intra-queue duplicates too).
    flagged  the rows the catalog scan already flagged `suspected_duplicate` (Run -> Duplicates
             -> "Scan for duplicates" / find_catalog_dups.py). That scan's own verdict is
             embedding-informed where a candidate is already indexed, but a flagged row's
             `dup_candidates` back-link is otherwise whatever the scan's `moderation_reason`
             string said at flag time. This mode re-embeds the flagged rows fresh and writes a
             proper `dup_candidates` entry onto them, same shape as a queue row gets, so the
             Duplicates tab's flagged-row detail carries the same structured tier/cosine evidence
             a pending row would.

Either way the report is the tier the confidence logic assigns: PROOF / CONFIDENT (would
auto-merge) / ADJUDICATE (LLM judge) / SIBLING (kept, not a dup) / HINT (human) / NONE (a
genuinely new program). This NEVER changes moderation_status or is_active — `--write` only adds a
dup_candidates back-link (evidence for a human to read), it does not flag, unflag, activate, or
deactivate anything. A flagged row stays exactly as flagged; a pending row stays exactly pending.

The only cost is embedding the selected rows (a fraction of a cent for either source, since
`--source flagged` selections are typically small); the active catalog's dedupe vectors are read
from the `opportunities.dedupe_vector` column (see dedupe_vector_schema.sql). Nothing is written to
Supabase without --write — this only tells you what the logic WOULD judge.

    python dedupe_queue.py                     # embeds the review queue, prints tier verdicts
    python dedupe_queue.py --preview            # FREE: just the counts + estimated embed cost
    python dedupe_queue.py --write              # ALSO stamps a dup_candidates back-link
    python dedupe_queue.py --source flagged     # same, but over the suspected_duplicate rows

`--write` PATCHes a dup_candidates entry (pointing at the survivor, tagged `via: content-embedding`)
onto each selected row whose nearest match reaches a surfaced tier (proof/confident/adjudicate/hint),
so the review console shows a 'possible duplicate of' back-link with the tier. It replaces only its
own prior entries -- url_dedupe's submission-time candidates (and, for a flagged row, the scan's own
moderation_reason) are left untouched.

The CATALOG-WIDE scan (Run -> Duplicates -> "Scan for duplicates") is a separate job — see
find_catalog_dups.py. It generates candidates over the WHOLE catalog cheaply (heuristic cuts,
near-linear) and tiers each one with a cosine looked up directly from the prebuilt index — no
re-embedding, no per-row search. This script's `--source flagged` mode is for going back and
adding stronger, freshly-embedded evidence to specific rows that scan already flagged, not for
re-running the whole-catalog scan itself.
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
# Mirrors ops/core.py's FLAGGED_STATUSES. Not imported — this script stays a standalone,
# stdlib/repo-root module like the rest of the offline agents; ops/ imports repo-root modules,
# never the other way around.
_FLAGGED_STATUS = "suspected_duplicate"

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


def select_rows(rows, source, classified_only=False):
    """The rows this run judges, for either `source`. Pure.

    'queue' is unchanged: pending rows (is_active=false), optionally narrowed to ones
    classify_queue.py has already labelled. 'flagged' is is_active=true rows the catalog scan
    already marked suspected_duplicate — classified_only is meaningless there (classify labels are
    a review-queue concept) and is ignored.
    """
    if source == "flagged":
        return [r for r in rows if r.get("is_active")
                and r.get("moderation_status") == _FLAGGED_STATUS]
    selected = [r for r in rows if not r.get("is_active")
               and (r.get("moderation_status") in _QUEUE_STATUSES)]
    if classified_only:
        selected = [r for r in selected
                   if any(str(f).startswith(queue_flags.CLASSIFY_PREFIX)
                          for f in (r.get("quality_flags") or []))]
    return selected


def _write_candidate(su, key, prow, survivor, tier, cosine):
    """PATCH the row's dup_candidates so the console shows a 'possible duplicate of' back-link to
    the survivor. Replaces this module's own prior entries, keeps url_dedupe's (and, for a
    flagged row, leaves moderation_status/duplicate_of/moderation_reason untouched — this is
    evidence, not a verdict)."""
    from supabase_common import supabase_patch
    cand = queue_flags.dedupe_candidate(survivor, tier, cosine)
    merged = queue_flags.merge_candidates(prow.get("dup_candidates"), [cand])
    supabase_patch(su, "opportunities", {"id": f"eq.{prow.get('id')}"},
                   {"dup_candidates": merged}, key)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=("queue", "flagged"), default="queue",
                    help="'queue' (default): pending review-queue rows. 'flagged': rows the "
                         "catalog scan already marked suspected_duplicate.")
    ap.add_argument("--preview", action="store_true", help="FREE: counts + estimated cost only.")
    ap.add_argument("--top-k", type=int, default=3)
    ap.add_argument("--write", action="store_true",
                    help="Persist each surfaced verdict as a dup_candidates entry (pointing at the "
                         "survivor) so the review console shows a 'possible duplicate of' back-link. "
                         "Never touches moderation_status or is_active. Read-only without it. "
                         "Idempotent -- replaces only its own prior entries.")
    ap.add_argument("--classified-only", action="store_true",
                    help="--source queue only: judge (and, with --write, stamp) only rows already "
                         "carrying a `classify:` flag -- i.e. the rows classify_queue.py has "
                         "triaged. Each is still matched against the WHOLE active catalog; only "
                         "the queue-vs-queue search space narrows to this set. Ignored for "
                         "--source flagged.")
    args = ap.parse_args()

    label = "flagged" if args.source == "flagged" else "queue"
    rows, gemini_key = _fetch_all()
    by_id = {r["id"]: r for r in rows if r.get("id")}
    selected = select_rows(rows, args.source, args.classified_only)
    reps = {r["id"]: default_representation(r, "") for r in selected}
    selected = [r for r in selected if reps[r["id"]].strip()]

    est = embed_common.estimate_embed_cost([reps[r["id"]] for r in selected])
    import dedupe_embed_store
    active_index = dedupe_embed_store.fetch_dedupe_index_from_env()
    print(f"[OK] {len(selected)} {label} row(s); active index holds {len(active_index)}. "
          f"Embedding them costs ~${est:.4f}.")
    if not active_index:
        print("[ERROR] No catalog dedupe index — run build_catalog_embeddings.py --yes-really first.")
        return
    if not selected:
        print(f"[OK] Nothing to judge — no {label} rows.")
        return
    if args.preview:
        print(f"\n[PREVIEW] free. Re-run without --preview to embed the {label} rows and judge "
              f"(PAID ~cent).")
        return
    if not gemini_key:
        raise SystemExit("[ERROR] GEMINI_API_KEY must be set in .env.")

    su = sk = None
    if args.write:
        su = os.environ.get("SUPABASE_URL", "").rstrip("/")
        sk = os.environ.get("SUPABASE_SERVICE_KEY")
        if not su or not sk:
            raise SystemExit("[ERROR] --write needs SUPABASE_URL and SUPABASE_SERVICE_KEY in .env.")

    # Embed the selection and add it to the search space, so a row can match either an active
    # catalog row (a re-discovery) or another selected row (an intra-batch twin). For --source
    # flagged this may re-embed a row the index already holds a (possibly stale) vector for --
    # exclude_ids below still keeps it from matching its own old entry.
    B = embed_common.DEFAULT_BATCH
    qvec = {}
    for i in range(0, len(selected), B):
        chunk = selected[i:i + B]
        vs, _c = embed_common.embed_batch([reps[r["id"]] for r in chunk], gemini_key)
        qvec.update({r["id"]: v for r, v in zip(chunk, vs) if v})
    search = active_index + [embed_common.index_entry(rid, v) for rid, v in qvec.items()]

    tally, detail = {}, {dc.TIER_PROOF: [], dc.TIER_CONFIDENT: [], dc.TIER_ADJUDICATE: [],
                        dc.TIER_SIBLING: []}
    written = 0
    for p in selected:
        pv = qvec.get(p["id"])
        hits = embed_common.nearest(pv, search, top_k=args.top_k, min_score=dc.HINT_FLOOR,
                                    exclude_ids={p["id"]}) if pv else []
        if not hits:
            tally[dc.TIER_NONE] = tally.get(dc.TIER_NONE, 0) + 1
            continue
        mid, cos, _e = hits[0]
        m = by_id.get(mid, {})
        # classify_rows runs every free signal: proof (same URL, name-gated), name + field
        # discriminators, and the same-institution context guard (so a generic name across orgs
        # can't reach CONFIDENT).
        v = dc.classify_rows(p, m, cosine=cos)
        tally[v.tier] = tally.get(v.tier, 0) + 1
        if args.write and v.tier in _SURFACE_TIERS and m.get("id"):
            try:
                _write_candidate(su, sk, p, m, v.tier, cos)
                written += 1
            except Exception as e:  # a failed write must not abort the run mid-batch
                print(f"      [WRITE FAILED] {p.get('id')}: {type(e).__name__}: {e}")
        if v.tier in detail:
            detail[v.tier].append(
                f"      {p['id']} {(p.get('name') or '')[:30]!r}  ->  {mid} [{_match_type(m)}] "
                f"{(m.get('name') or '')[:30]!r}   cos={cos:.3f} {' '.join(v.reasons[1:])}")

    print(f"\n[{label.upper()} DEDUPE VERDICTS] {len(selected)} {label} row(s):\n")
    for t in (dc.TIER_PROOF, dc.TIER_CONFIDENT, dc.TIER_ADJUDICATE, dc.TIER_SIBLING,
              dc.TIER_HINT, dc.TIER_NONE):
        print(f"  {t:11} {tally.get(t, 0)}")
    for t, tlabel in ((dc.TIER_PROOF, "PROOF -- same normalized URL (certain duplicate)"),
                      (dc.TIER_CONFIDENT, "CONFIDENT -- would auto-merge (high sim + name-same)"),
                      (dc.TIER_ADJUDICATE, "ADJUDICATE -- send to LLM judge (qualifier/discrepancy)"),
                      (dc.TIER_SIBLING, "SIBLING -- kept as a distinct program")):
        print(f"\n  --- {tlabel} ---")
        print("\n".join(detail[t]) or "      (none)")
    auto = tally.get(dc.TIER_PROOF, 0) + tally.get(dc.TIER_CONFIDENT, 0)
    print(f"\n  {auto} of {len(selected)} would auto-resolve; "
          f"{tally.get(dc.TIER_NONE, 0)} look like genuinely new programs.")
    if args.write:
        print(f"  [WROTE] {written} row(s) stamped with a dup_candidates back-link "
              f"(the review console now shows the tier). moderation_status/is_active untouched.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Run the page CLASSIFIER over the review queue. Read-only by default; --write persists verdicts.

For each pending row, fetch its page and classify it — program / first_party_hub / third_party_hub /
none — plus the deterministic staleness check. Reports which pending rows are actually program pages
(good rows), which are HUBS that should have become discovery leads instead of rows, which are NONE
(don't belong), and which are STALE. This is the first LIVE exercise of the M8 classifier prompt;
until now it has only been run against injected test responses.

Unlike the dedupe dry-run (embeddings, ~$0.004 total), this is one no-search model call PER ROW
(~$0.004/row) and is rate-limited to one call / 5s, so the whole 279-row queue is ~$1 and ~25 min.
Pilot with --limit first.

    python classify_queue.py --preview            # FREE: row count + estimated cost
    python classify_queue.py --limit 30           # PAID pilot (~$0.15): classify a sample
    python classify_queue.py                      # PAID full queue (~$1, ~25 min), prints only
    python classify_queue.py --limit 30 --write   # PAID pilot AND stamp each verdict onto the row

`--write` PATCHes a `classify:` entry into each row's quality_flags so the review console shows the
class/confidence pill. The write itself is free (the model call is the paid part) and idempotent --
re-running replaces the row's prior `classify:` flag rather than stacking a second one.
"""
import argparse
import datetime
import os

import classify_page
import discovered_leads as dl
import queue_flags

_SELECT = "id,name,org,url,type,is_active,moderation_status,quality_flags"
_QUEUE_STATUSES = (None, "", "pending_review")
_PER_ROW_EST = 0.004

# A hub classified here is a page whose PROGRAMS should be mined, not a row of its own. First-party
# hubs list their OWN programs (mine same-domain, the walk_up_hubs shape); third-party hubs list
# others' (mine off-domain, the round-up shape). Feed both into the hub-mining work-list.
_HUB_SCOPE = {classify_page.CLASS_FIRST_PARTY_HUB: dl.SCOPE_SAME_DOMAIN,
              classify_page.CLASS_THIRD_PARTY_HUB: dl.SCOPE_OFF_DOMAIN}


def _hub_lead(row, klass):
    """A discovered_leads hub lead for a queue row the classifier called a hub."""
    return {"url": row.get("url"), "kind": dl.KIND_HUB, "scope": _HUB_SCOPE[klass],
            "seed_id": None,
            "angle": f"classifier queue triage: {row.get('id')} {(row.get('name') or '')[:50]}",
            "signal": f"page classifier: {klass}",
            "first_seen": datetime.date.today().isoformat(), "status": dl.STATUS_NEW}


def _write_verdict(su, key, row, c):
    """PATCH the row's quality_flags to carry this classifier verdict, replacing any prior
    `classify:` entry (idempotent) and leaving every other flag untouched. PAID? No -- a free write
    of an already-computed verdict; the model call was the paid part."""
    from supabase_common import supabase_patch
    flags = queue_flags.upsert_flag(row.get("quality_flags"), queue_flags.CLASSIFY_PREFIX, c.flag())
    supabase_patch(su, "opportunities", {"id": f"eq.{row.get('id')}"},
                   {"quality_flags": flags}, key)


def _fetch_queue():
    from supabase_common import supabase_get, load_dotenv
    load_dotenv()
    su = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_ANON_KEY")
    if not su or not key:
        raise SystemExit("[ERROR] SUPABASE_URL and a key must be set in .env.")
    rows = supabase_get(su, "opportunities", {"select": _SELECT}, key) or []
    return [r for r in rows if not r.get("is_active")
            and (r.get("moderation_status") in _QUEUE_STATUSES)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true", help="FREE: count + estimated cost only.")
    ap.add_argument("--limit", type=int, help="Classify only the first N rows (a paid pilot).")
    ap.add_argument("--browser", action="store_true",
                    help="Use the headless-browser fallback for JS/blocked pages (slower).")
    ap.add_argument("--no-feed", action="store_true",
                    help="Do NOT append classified hubs to the hub-mining queue (default: feed).")
    ap.add_argument("--write", action="store_true",
                    help="Persist each verdict as a `classify:` quality_flags entry so the review "
                         "console surfaces it (default: read-only dry-run). Idempotent per row.")
    args = ap.parse_args()

    queue = _fetch_queue()
    if args.limit:
        queue = queue[:args.limit]
    print(f"[OK] {len(queue)} queue row(s) to classify. Estimated cost ~${len(queue) * _PER_ROW_EST:.2f} "
          f"(one no-search model call each, 5s apart -> ~{len(queue) * 5 // 60 + 1} min).")
    if args.preview:
        print("\n[PREVIEW] free. Re-run with --limit N (pilot) or no flag (full) to classify (PAID).")
        return

    from supabase_common import load_dotenv
    load_dotenv()
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise SystemExit("[ERROR] GEMINI_API_KEY must be set in .env.")

    su = sk = None
    if args.write:
        su = os.environ.get("SUPABASE_URL", "").rstrip("/")
        sk = os.environ.get("SUPABASE_SERVICE_KEY")
        if not su or not sk:
            raise SystemExit("[ERROR] --write needs SUPABASE_URL and SUPABASE_SERVICE_KEY in .env.")

    by_route, by_class, flagged, hub_leads = {}, {}, [], []
    total_cost, written = 0.0, 0
    for i, r in enumerate(queue, 1):
        c = classify_page.classify_page(r.get("url"), key, name_hint=r.get("name") or "",
                                        org_hint=r.get("org") or "", allow_browser=args.browser)
        total_cost += c.cost
        route = c.route()
        by_route[route] = by_route.get(route, 0) + 1
        by_class[c.klass or "unreadable"] = by_class.get(c.klass or "unreadable", 0) + 1
        # Everything that is NOT a clean fresh program row is worth the operator's eye.
        if route != classify_page.ROUTE_ROW:
            flagged.append((r, c, route))
        # Feed the hub pipe: a hub row's PROGRAMS belong in the mining queue, not this row.
        if c.klass in _HUB_SCOPE and r.get("url"):
            hub_leads.append(_hub_lead(r, c.klass))
        if args.write:
            try:
                _write_verdict(su, sk, r, c)
                written += 1
            except Exception as e:  # a failed write must not abort a paid run mid-queue
                print(f"      [WRITE FAILED] {r.get('id')}: {type(e).__name__}: {e}")
        print(f"  [{i}/{len(queue)}] {r.get('id')} {(r.get('name') or '')[:34]!r:36} "
              f"-> {c.klass or 'unreadable'}/{c.confidence} route={route}"
              f"{' STALE' if c.stale else ''}  (~${total_cost:.3f})")

    kind = "RUN (--write)" if args.write else "DRY-RUN"
    print(f"\n[CLASSIFIER {kind}] {len(queue)} rows, ~${total_cost:.4f}\n")
    if args.write:
        print(f"  [WROTE] {written}/{len(queue)} rows stamped with a `classify:` flag "
              f"(the review console now surfaces the verdict).")
    print("  by class:  " + ", ".join(f"{k}={v}" for k, v in sorted(by_class.items())))
    print("  by route:  " + ", ".join(f"{k}={v}" for k, v in sorted(by_route.items())))
    print(f"\n  --- NOT a clean program row (hub / none / stale / unreadable) -- {len(flagged)} ---")
    for r, c, route in flagged:
        ev = (c.evidence or c.error or "")[:70]
        print(f"      {r.get('id')} {(r.get('name') or '')[:32]!r:34} {c.klass or 'unreadable'}"
              f"/{c.confidence} route={route}{' STALE' if c.stale else ''}")
        if ev:
            print(f"             {ev!r}")

    # Feed the hub-mining queue (free, local jsonl). Deduped by URL against existing leads.
    if hub_leads and not args.no_feed:
        n = dl.append_leads(hub_leads)
        print(f"\n  [HUB PIPE] {len(hub_leads)} hub(s) classified; {n} new lead(s) fed into the "
              f"hub-mining queue. Now: {dl.summarize(dl.load_leads())}")
        print(f"  Mine them: python mine_hub_pages.py --from-leads   (PAID, gated)")
    elif hub_leads:
        print(f"\n  [HUB PIPE] {len(hub_leads)} hub(s) classified; --no-feed set, nothing queued.")


if __name__ == "__main__":
    main()

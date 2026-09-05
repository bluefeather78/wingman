#!/usr/bin/env python3
"""Bulk-triage the review queue by its page-classifier verdict. FREE (no model calls).

Once `agents/classify_queue.py --write` (or the scraper's discovery gate) has stamped `classify:` flags
on the queue, most of the backlog sorts itself: a HUB page is not a program row (its programs are
already routed to the mining queue), a `none` page is not an opportunity, and a STALE program's own
page says it stopped years ago. This drains those in one command instead of filtering-and-clicking
them class by class in the console.

It only ever REJECTS the safe-to-reject classes and NEVER touches a fresh `program` row (those are
the good rows, left for a human to activate) or an `unreadable` row (a fetch failure is about our
HTTP client, not the page). Rejecting is reversible — the row stays in the table (its URL keeps
blocking re-submission) and can be moderated back to pending_review — and it reuses the console's
own moderation endpoint, so there is no logic drift.

    python -m agents.triage_queue                       # FREE: full class breakdown, writes nothing
    python -m agents.triage_queue --all-junk --dry-run  # FREE: show exactly what --all-junk would reject
    python -m agents.triage_queue --all-junk            # reject hubs + none + stale programs
    python -m agents.triage_queue --reject-hubs         # reject only the hub rows

Talks to the LOCAL ops server (localhost-gated, like every /api/agents/* route). Start it first
(python server.py); override the base with --api-base or WINGMAN_API_BASE.
"""
import argparse
import json
import os
import urllib.request

from wingman import queue_flags

_HUB_CLASSES = ("first_party_hub", "third_party_hub")
_REASON = {
    "hub": "discovery gate: hub page - its programs are routed to the mining queue",
    "none": "discovery gate: not an opportunity page",
    "stale": "discovery gate: stale program - the page's newest date is >= 3 years old",
}


def classify_flag(row):
    """The row's `classify:` flag string, or None. Pure."""
    for f in (row.get("quality_flags") or []):
        if str(f).startswith(queue_flags.CLASSIFY_PREFIX):
            return str(f)
    return None


def row_bucket(row):
    """Which triage bucket a row falls in: 'hub' / 'none' / 'stale' / 'program' / 'unreadable' /
    '' (unclassified). Pure. A STALE program is its own bucket so it can be rejected separately
    from a live program."""
    flag = classify_flag(row)
    if not flag:
        return ""
    klass = queue_flags.flag_class([flag])
    if klass in _HUB_CLASSES:
        return "hub"
    if klass == "none":
        return "none"
    if klass == "program":
        return "stale" if "STALE" in flag else "program"
    if klass == "unreadable":
        return "unreadable"
    return klass or ""


def breakdown(rows):
    """Count queue rows by triage bucket. Pure."""
    counts = {}
    for r in rows:
        b = row_bucket(r) or "(unclassified)"
        counts[b] = counts.get(b, 0) + 1
    return counts


def plan_triage(rows, *, reject_hubs=False, reject_none=False, reject_stale=False):
    """The rejection plan: a list of {bucket, reason, ids} for the enabled actions. Pure.

    A fresh `program`, an `unreadable`, and an unclassified row are NEVER included — only the
    explicitly enabled junk buckets are.
    """
    picked = {"hub": reject_hubs, "none": reject_none, "stale": reject_stale}
    ids = {"hub": [], "none": [], "stale": []}
    for r in rows:
        b = row_bucket(r)
        if picked.get(b) and r.get("id"):
            ids[b].append(r["id"])
    return [{"bucket": b, "reason": _REASON[b], "ids": ids[b]} for b in ("hub", "none", "stale")
            if ids[b]]


# --- I/O (the only impure part) -------------------------------------------------------

def _get_json(url, timeout=60):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.load(resp)


def _post_json(url, body, timeout=60):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reject-hubs", action="store_true",
                    help="Reject first_party_hub + third_party_hub rows (their programs are mined).")
    ap.add_argument("--reject-none", action="store_true", help="Reject `none` (non-opportunity) rows.")
    ap.add_argument("--reject-stale", action="store_true",
                    help="Reject program rows the classifier marked STALE.")
    ap.add_argument("--all-junk", action="store_true", help="= --reject-hubs --reject-none --reject-stale.")
    ap.add_argument("--dry-run", action="store_true", help="Plan and print, but write nothing.")
    ap.add_argument("--api-base", default=os.environ.get("WINGMAN_API_BASE", "http://127.0.0.1:8000"),
                    help="Local ops server base (default http://127.0.0.1:8000).")
    args = ap.parse_args()

    reject_hubs = args.reject_hubs or args.all_junk
    reject_none = args.reject_none or args.all_junk
    reject_stale = args.reject_stale or args.all_junk

    base = args.api_base.rstrip("/")
    try:
        data = _get_json(f"{base}/api/agents/pending?status=queue&limit=2000")
    except Exception as e:
        raise SystemExit(f"[ERROR] Could not read the queue from {base}: {e}. Is the ops server up "
                         f"(python server.py)?")
    rows = data.get("opportunities", []) if isinstance(data, dict) else []
    print(f"[OK] {len(rows)} row(s) in the review queue. By classifier verdict:")
    for b, n in sorted(breakdown(rows).items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"    {b:15} {n}")

    if not (reject_hubs or reject_none or reject_stale):
        print("\n[PREVIEW] No action flags given, so nothing will be rejected. Re-run with "
              "--all-junk (or --reject-hubs/--reject-none/--reject-stale) to act; add --dry-run "
              "to see the exact plan for free first.")
        return

    plan = plan_triage(rows, reject_hubs=reject_hubs, reject_none=reject_none,
                       reject_stale=reject_stale)
    total = sum(len(p["ids"]) for p in plan)
    print(f"\n[PLAN] reject {total} row(s):")
    for p in plan:
        print(f"    {p['bucket']:6} {len(p['ids'])} -> {p['reason']}")
    if not plan:
        print("    (nothing matched the enabled buckets)")
        return
    if args.dry_run:
        print("\n[DRY RUN] Nothing was written. Re-run without --dry-run to reject (reversible: "
              "each row stays in the table and can be moderated back to pending_review).")
        return

    rejected = 0
    for p in plan:
        try:
            r = _post_json(f"{base}/api/agents/pending/moderate",
                           {"ids": p["ids"], "status": "rejected", "reason": p["reason"]})
        except Exception as e:
            print(f"    [ERROR] {p['bucket']}: {e}")
            continue
        if r.get("ok"):
            done = r.get("done", len(p["ids"]))
            rejected += done
            print(f"    [OK] {p['bucket']}: rejected {done} row(s).")
        else:
            print(f"    [ERROR] {p['bucket']}: {r.get('error')}")
    print(f"\n[DONE] {rejected} row(s) rejected. They stay in the table (URL still blocks "
          f"re-submission) and are reversible from the console's Rejected tab.")


if __name__ == "__main__":
    main()

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

When it rejects the hub bucket it ALSO feeds those pages into the hub-mining queue
(wingman/discovered_leads) so their programs can be harvested — BOTH kinds, each with the correct
scope: a first_party_hub is mined same-domain (the institution's own index), a third_party_hub
off-domain (the round-up's outbound links). It reuses classify_queue's lead builder, so the
class->scope mapping has one definition. --dry-run previews the feed and writes nothing.

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
import urllib.error
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


def hub_leads_for(rows, hub_ids):
    """discovered_leads hub leads for the hub rows being rejected. Pure — builds dicts, no I/O.

    Each lead is scoped by the row's OWN classifier verdict, so both hub kinds are handled:
    first_party_hub -> same-domain (mine the institution's own index), third_party_hub ->
    off-domain (mine the round-up's outbound links). Reuses classify_queue._hub_lead so the
    lead shape and the class->scope mapping have exactly ONE definition in the repo.
    """
    from agents.classify_queue import _hub_lead
    want = {str(i) for i in (hub_ids or [])}
    leads = []
    for r in rows:
        if str(r.get("id")) in want:
            klass = queue_flags.flag_class([classify_flag(r) or ""])
            if klass in _HUB_CLASSES and r.get("url"):
                leads.append(_hub_lead(r, klass))
    return leads


def feed_hub_leads(rows, hub_ids, *, commit):
    """Feed the rejected hubs into the mining queue (commit=True) or preview it (commit=False).

    Returns the number of leads built. Prints a [HUB PIPE] line naming the same/off-domain split
    so an operator can see both kinds were routed. append_leads dedupes by url_key, so a hub
    already queued is absorbed rather than duplicated.
    """
    from wingman import discovered_leads
    leads = hub_leads_for(rows, hub_ids)
    if not leads:
        return 0
    sd = sum(1 for l in leads if l.get("scope") == discovered_leads.SCOPE_SAME_DOMAIN)
    od = len(leads) - sd
    if not commit:
        print(f"\n  [HUB PIPE] {len(leads)} hub(s) would be fed into the mining queue "
              f"(same-domain {sd}, off-domain {od}).")
        return len(leads)
    try:
        n = discovered_leads.append_leads(leads)
    except Exception as e:                                             # noqa: BLE001
        print(f"\n  [HUB PIPE] could not feed the mining queue: {e}")
        return len(leads)
    print(f"\n  [HUB PIPE] {len(leads)} hub(s) rejected -> {n} new lead(s) fed into the mining "
          f"queue (same-domain {sd}, off-domain {od}; the rest were already queued). "
          f"Harvest them with: python -m agents.mine_hub_pages --from-leads")
    return len(leads)


# --- I/O (the only impure part) -------------------------------------------------------

def _ops_token():
    """The ops token to authenticate /api/agents/* (S1-8), from the env or .env (same order
    server.py uses). Every ops route fails closed with 403 without a matching X-Ops-Token."""
    tok = os.environ.get("WINGMAN_OPS_TOKEN")
    if tok:
        return tok
    try:
        env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
        with open(env_path) as fh:
            for line in fh:
                key, _, value = line.partition("=")
                if key.strip() == "WINGMAN_OPS_TOKEN" and value.strip():
                    return value.strip().strip("'\"")
    except OSError:
        pass
    return ""


def _ops_headers(extra=None):
    headers = dict(extra or {})
    tok = _ops_token()
    if tok:
        headers["X-Ops-Token"] = tok
    return headers


def _get_json(url, timeout=60):
    req = urllib.request.Request(url, headers=_ops_headers(), method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def _post_json(url, body, timeout=60):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers=_ops_headers({"Content-Type": "application/json"}),
                                 method="POST")
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
    except urllib.error.HTTPError as e:
        if e.code == 403:
            raise SystemExit(
                f"[ERROR] Ops server refused the request (403) at {base}. The /api/agents/* routes "
                f"require WINGMAN_OPS_TOKEN in an X-Ops-Token header (S1-8). Set WINGMAN_OPS_TOKEN in "
                f".env to the value the server is using (python server.py prints it if it minted one) "
                f"and re-run.")
        raise SystemExit(f"[ERROR] Could not read the queue from {base}: {e}. Is the ops server up "
                         f"(python server.py)?")
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
    hub_ids = next((p["ids"] for p in plan if p["bucket"] == "hub"), [])
    if args.dry_run:
        feed_hub_leads(rows, hub_ids, commit=False)
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
    # Route the rejected hubs' programs into the mining queue — first-party same-domain,
    # third-party off-domain — so mine_hub_pages --from-leads can harvest them. The reason
    # stamped on the row ("its programs are routed to the mining queue") is now true.
    feed_hub_leads(rows, hub_ids, commit=True)
    print(f"\n[DONE] {rejected} row(s) rejected. They stay in the table (URL still blocks "
          f"re-submission) and are reversible from the console's Rejected tab.")


if __name__ == "__main__":
    main()

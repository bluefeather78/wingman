"""Report duplicate rows already inside the opportunities table. Free, READ-ONLY.

The 2026-08-23/24 review pass proved the live catalog still holds self-duplicates from
before url_dedupe existed: Conrad Challenge sat in the table twice as www/trailing-slash
variants of one URL, Girls Who Code twice under one URL with two names, The Concord
Review three times. Those were found by accident — a scraped twin's dup-candidates
happened to point at them — and consolidated by hand with SQL DELETEs. This script finds
the rest deliberately, as a report for the console/operator. It writes NOTHING: catalog
changes remain a human decision (prefer the console's duplicate/reject actions over
DELETE — see scraper_tombstones.json for why).

Two cuts, in decreasing confidence:

  1. match_key collisions — two rows whose URLs normalize identically. These are the
     same page. Same-name pairs are near-certain duplicates; different-name pairs may be
     a shared application portal (spicestanford.smapply.io hosts six real programs), so
     even these are REPORTED, never auto-actioned.
  2. same registrable domain + name similarity >= 0.90 (active rows only) — hints.
     url_dedupe's own measurements say name similarity is wrong often ('1-Week' vs
     '3-Week Medical Academy' scores 0.95), so this cut exists to be eyeballed, and its
     threshold is deliberately above the 0.82/0.88 hint thresholds used live.

Usage:
    python find_catalog_dups.py [--json out.json] [--all-rows]

--all-rows extends cut 2 to inactive rows too (noisier: the review queue legitimately
holds near-copies of active rows awaiting a verdict).
"""

import argparse
import json
import os
import sys
from collections import defaultdict

from supabase_common import load_dotenv, supabase_get
import url_dedupe

def fetch_all_rows(supabase_url, key):
    """Every row, active and inactive. supabase_get paginates internally via Range
    headers, so this must NOT add its own limit/offset loop on top (double pagination
    416s once the window passes the end of the table)."""
    return supabase_get(supabase_url, "opportunities", {
        "select": "id,name,org,url,is_active,moderation_status",
        "order": "id.asc",
    }, key)


def key_collisions(rows):
    """Cut 1: groups of rows sharing one match_key."""
    by_key = defaultdict(list)
    for r in rows:
        k = url_dedupe.match_key(r.get("url") or "")
        if k:
            by_key[k].append(r)
    groups = []
    for k, members in by_key.items():
        if len(members) < 2:
            continue
        names = {url_dedupe.normalize_name(m.get("name")) for m in members}
        groups.append({
            "match_key": k,
            "same_name": len(names) == 1,
            "active_count": sum(1 for m in members if m.get("is_active")),
            "rows": members,
        })
    # Most actionable first: same-page same-name with 2+ active rows is student-visible
    # duplication; shared portals (different names) sort after.
    groups.sort(key=lambda g: (-g["active_count"], not g["same_name"]))
    return groups


def name_hints(rows, include_inactive=False):
    """Cut 2: same-domain, high-name-similarity pairs not already in cut 1."""
    pool = rows if include_inactive else [r for r in rows if r.get("is_active")]
    by_domain = defaultdict(list)
    for r in pool:
        _, host, _, _ = url_dedupe.split_url(r.get("url") or "")
        d = url_dedupe.registrable_domain(host)
        if d:
            by_domain[d].append(r)
    hints = []
    for domain, members in by_domain.items():
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                ka = url_dedupe.match_key(a.get("url") or "")
                kb = url_dedupe.match_key(b.get("url") or "")
                if ka and ka == kb:
                    continue  # cut 1 already owns this pair
                ratio = url_dedupe.name_similarity(a.get("name"), b.get("name"))
                if ratio >= 0.90:
                    hints.append({"domain": domain, "ratio": round(ratio, 3),
                                  "rows": [a, b]})
    hints.sort(key=lambda h: -h["ratio"])
    return hints


def _fmt_row(r):
    state = "ACTIVE" if r.get("is_active") else (r.get("moderation_status") or "inactive")
    return f"{r['id']} [{state}] {r.get('name')}  |  {r.get('url')}"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", default=None, help="Also write the report here.")
    parser.add_argument("--all-rows", action="store_true",
                        help="Include inactive rows in the name-similarity cut.")
    args = parser.parse_args()

    load_dotenv()
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_ANON_KEY", "")
    if not supabase_url or not key:
        print("[ERROR] SUPABASE_URL / key not set in .env.")
        sys.exit(1)

    rows = fetch_all_rows(supabase_url, key)
    print(f"[OK] {len(rows)} rows loaded "
          f"({sum(1 for r in rows if r.get('is_active'))} active).")

    groups = key_collisions(rows)
    print(f"\n=== Cut 1: identical normalized URL — {len(groups)} group(s) ===")
    for g in groups:
        kind = "SAME NAME (near-certain duplicate)" if g["same_name"] else \
               "different names (may be a shared portal — check before acting)"
        print(f"\n  {g['match_key']}  ({g['active_count']} active)  {kind}")
        for r in g["rows"]:
            print(f"    {_fmt_row(r)}")

    hints = name_hints(rows, include_inactive=args.all_rows)
    scope = "all rows" if args.all_rows else "active rows only"
    print(f"\n=== Cut 2: same domain, name >=90% similar ({scope}) — "
          f"{len(hints)} pair(s), hints ONLY ===")
    for h in hints:
        print(f"\n  {h['domain']}  (ratio {h['ratio']})")
        for r in h["rows"]:
            print(f"    {_fmt_row(r)}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"key_collisions": groups, "name_hints": hints}, f,
                      indent=1, ensure_ascii=False)
        print(f"\n[OK] wrote {args.json}")
    print("\n[NOTE] Report only — nothing was written. Consolidate via the console's "
          "duplicate/reject actions, not SQL DELETE (deletes need tombstones).")


if __name__ == "__main__":
    main()

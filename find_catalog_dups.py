"""Report duplicate rows already inside the opportunities table. Free, READ-ONLY.

The 2026-08-23/24 review pass proved the live catalog still holds self-duplicates from
before url_dedupe existed: Conrad Challenge sat in the table twice as www/trailing-slash
variants of one URL, Girls Who Code twice under one URL with two names, The Concord
Review three times. Those were found by accident — a scraped twin's dup-candidates
happened to point at them — and consolidated by hand with SQL DELETEs. This script finds
the rest deliberately, as a report for the console/operator. It writes NOTHING: catalog
changes remain a human decision (prefer the console's duplicate/reject actions over
DELETE — see scraper_tombstones.json for why).

Three cuts, in decreasing confidence:

  1. match_key collisions — two rows whose URLs normalize identically. These are the
     same page. Same-name pairs are near-certain duplicates; different-name pairs may be
     a shared application portal (spicestanford.smapply.io hosts six real programs), so
     even these are REPORTED, never auto-actioned.
  2. same registrable domain + name similarity >= 0.90 (active rows only) — hints.
     url_dedupe's own measurements say name similarity is wrong often ('1-Week' vs
     '3-Week Medical Academy' scores 0.95), so this cut exists to be eyeballed, and its
     threshold is deliberately above the 0.82/0.88 hint thresholds used live.
  3. acronym<->expansion and token-set (Jaccard) overlap, CROSS-HOST allowed (active rows)
     — the misses cuts 1 and 2 cannot see: an acronym vs its spelled-out name ('NACLO' vs
     'North American Computational Linguistics ...'), a reworded name, or the same program
     at a second URL on a different domain. Deliberately fed to the SCAN/report only, never
     to url_dedupe.name_similarity, so the submission-time reject path cannot gain a false
     rejection from it. Weakest cut, purely a review aid.

All three are hints for a human. Nothing here writes.

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


# Cut 3 tuning. All HINTS — cut 3 pairs are never auto-actioned, they are flagged for a human,
# so the bar is "worth a look", not "certainly a duplicate".
_COMMON_TOKEN_DF = 30    # a token on more rows than this is a category word ('summer'), not an
                         # identity — skipped for CANDIDATE generation (still counts in Jaccard)
_JACCARD_MIN = 0.6       # token-set overlap needed to call two names near-matches
_MIN_SHARED = 2          # ...and they must share at least this many distinctive tokens
_ACRONYM_MIN, _ACRONYM_MAX = 3, 8
_MAX_EXTRA_PAIRS = 400


def _row_tokens(r):
    """Distinctive name tokens: connectors, 'program' and institution words dropped, len>=2.
    Reuses url_dedupe's own splitters so 'the finder' and 'the reject path' agree on what a
    name's identity words are."""
    return [t for t in url_dedupe._distinctive_tokens(r.get("name"))
            if t not in url_dedupe._INSTITUTION_WORDS and len(t) >= 2]


def _host_of(r):
    _, host, _, _ = url_dedupe.split_url(r.get("url") or "")
    return host


def extra_name_pairs(rows):
    """Cut 3: the pairs the strict cuts miss — ACRONYM↔expansion and token-set overlap, both
    allowed to cross domains. HINTS ONLY, fed to the scan for a human to keep or confirm; this
    never touches url_dedupe.name_similarity, so the submission-time REJECT path is unaffected
    and cannot gain a false rejection from it.

    Only active rows (a student-visible duplicate is the thing worth surfacing), and two names
    that are BOTH just a bare institution are suppressed — that is the exact noise the
    bare-institution guard exists for.
    """
    info = []
    for r in rows:
        if not r.get("is_active"):
            info.append(None)
            continue
        nm = url_dedupe.normalize_name(r.get("name"))
        if not nm or nm in url_dedupe.GENERIC_NAMES or len(nm) < 4:
            info.append(None)
            continue
        toks = _row_tokens(r)
        info.append({
            "row": r, "nm": nm, "toks": toks, "set": set(toks), "host": _host_of(r),
            "acr": "".join(t[0] for t in toks) if len(toks) >= 3 else "",
        })

    # Inverted index for candidate generation, minus category words that would pair everything.
    postings = defaultdict(list)
    for idx, it in enumerate(info):
        if it:
            for t in it["set"]:
                postings[t].append(idx)
    discriminative = {t: idxs for t, idxs in postings.items() if len(idxs) <= _COMMON_TOKEN_DF}

    seen, out = set(), []

    def emit(i, j, reason, conf):
        key = (i, j) if i < j else (j, i)
        if key in seen:
            return
        seen.add(key)
        a, b = info[i], info[j]
        if (url_dedupe._is_bare_institution(a["row"].get("name"), a["host"])
                and url_dedupe._is_bare_institution(b["row"].get("name"), b["host"])):
            return
        out.append({"rows": [a["row"], b["row"]], "reason": reason, "confidence": conf,
                    "ratio": conf == "strong" and 0.95 or 0.6})

    # Token-set (Jaccard) overlap. Candidates share >=2 discriminative tokens; only those pay
    # for the set math, which keeps this near-linear instead of O(n^2).
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
            union = it["set"] | sj
            inter = it["set"] & sj
            if len(inter) < _MIN_SHARED or not union:
                continue
            jac = len(inter) / len(union)
            if jac < _JACCARD_MIN:
                continue
            same_dom = (it["host"] and info[j]["host"]
                        and url_dedupe.registrable_domain(it["host"])
                        == url_dedupe.registrable_domain(info[j]["host"]))
            reason = f"name tokens {int(jac * 100)}% overlap" + ("" if same_dom else " (different site)")
            emit(i, j, reason, "strong" if same_dom and jac >= 0.75 else "weak")
        if len(out) >= _MAX_EXTRA_PAIRS:
            return out[:_MAX_EXTRA_PAIRS]

    # Acronym: one row's whole name is a short token that spells the initials of another's
    # distinctive words. Cross-host by nature — an acronym and its expansion rarely co-locate.
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
                emit(idx, j, f"acronym match: '{cand.upper()}' = initials of "
                             f"'{info[j]['row'].get('name')}'", "weak")
        if len(out) >= _MAX_EXTRA_PAIRS:
            break

    return out[:_MAX_EXTRA_PAIRS]


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

    extra = extra_name_pairs(rows)
    print(f"\n=== Cut 3: acronym / token-overlap, cross-host (active rows) — "
          f"{len(extra)} pair(s), hints ONLY ===")
    for p in extra:
        print(f"\n  [{p['confidence']}] {p['reason']}")
        for r in p["rows"]:
            print(f"    {_fmt_row(r)}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"key_collisions": groups, "name_hints": hints,
                       "extra_name_pairs": extra}, f, indent=1, ensure_ascii=False)
        print(f"\n[OK] wrote {args.json}")
    print("\n[NOTE] Report only — nothing was written. Consolidate via the console's "
          "duplicate/reject actions, not SQL DELETE (deletes need tombstones).")


if __name__ == "__main__":
    main()

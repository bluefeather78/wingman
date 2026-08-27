#!/usr/bin/env python3
"""Live-HTTP validation of Phase-2 URL truth against the frozen 2026-08-23 batch. FREE (no API).

For every row in the batch whose stored URL is a content mill or off its org's own domain, run
the REAL scrape_opportunities.resolve_url_truth and report whether it now yields an org-domain,
title-proven URL. This is the plan's Phase-2 criterion 1 (">=8 of the 11 offsite-rejected rows
come out with an org-domain, title-proven URL").

It hits real external sites, so the exact count depends on which 08-23 pages are still up today
— a rescue that fails only because the source listicle is now 404 is a fact about the internet,
not the code. The mechanism itself is pinned hermetically in tests/unit/test_url_truth.py.

"Approved rows are never worsened" is guaranteed by construction (resolve_url_truth only ever
swaps a URL to a title-PROVEN one and never suppresses), so this focuses on the offsite set.
Grounding URLs aren't in the snapshot, so only the on-page-primary-link rescue path is exercised
here; a live scrape also has the grounding-sibling path.
"""
import glob
import json
import os
import re

import scrape_opportunities as so
import url_dedupe
import url_validate as uv

REPO = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(REPO, "tests", "fixtures", "scraper_grading_20260823.json")
LOG_DIR = os.path.join(REPO, "agent_logs")
_LOG_RE = re.compile(r"scraper_(\d{8})(?:-\d{6})?_seed(\d+)\.json$")
_DATE_RE = re.compile(r"(\d{8})")


def _mk(url):
    try:
        return url_dedupe.match_key(url) if url else None
    except Exception:
        return None


def _load_grounding():
    """Reconstruct each seed's grounding source URLs from the per-seed run logs, so the grader
    can replay the SAME resolved_urls a live scrape would hand resolve_url_truth — without it
    only the weak on-page-link rescue path is exercised, never the primary grounding-sibling one.
    Returns (cand_key -> {(seed,date)}, (seed,date) -> {resolved urls})."""
    cand, seed_resolved = {}, {}
    for path in glob.glob(os.path.join(LOG_DIR, "scraper_*_seed*.json")):
        m = _LOG_RE.search(os.path.basename(path))
        if not m:
            continue
        date, sid = m.group(1), int(m.group(2))
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        for c in (data.get("candidates") or []):
            if isinstance(c, dict):
                k = _mk(c.get("url"))
                if k:
                    cand.setdefault(k, set()).add((sid, date))
        res = seed_resolved.setdefault((sid, date), set())
        for u in (data.get("resolved_urls") or []):
            if u:
                res.add(u)
    return cand, seed_resolved


def _grounding_for(row, cand, seed_resolved):
    """The resolved source URLs for the seed that produced this row (same-day URL match)."""
    dm = _DATE_RE.search(row.get("source") or "")
    date = dm.group(1) if dm else None
    key = _mk(row.get("url"))
    seeds = {sid for (sid, d) in cand.get(key, set()) if date and d == date} if key else set()
    urls = set()
    for sid in seeds:
        urls |= seed_resolved.get((sid, date), set())
    return list(urls)


def _load():
    with open(FIXTURE, encoding="utf-8") as f:
        fx = json.load(f)
    verdicts = fx["verdicts"]
    rows = []
    for fn in fx["snapshots"]:
        path = os.path.join(REPO, fn)
        if not os.path.exists(path):
            print(f"[WARN] snapshot missing: {fn}")
            continue
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for r in data.get("inserted", []):
            if r.get("id"):
                rows.append(r)
    return rows, verdicts


def main():
    rows, verdicts = _load()
    cand, seed_resolved = _load_grounding()
    print(f"[OK] {len(rows)} snapshot row(s), {len(verdicts)} verdict(s); grounding for "
          f"{len(seed_resolved)} seed-run(s).")

    offsite = []
    for r in rows:
        url, name, org = r.get("url"), r.get("name") or "", r.get("org") or ""
        if not url:
            continue
        if uv.is_content_mill(url) or not uv.domain_matches_org(url, org, name):
            offsite.append(r)

    print(f"[OK] {len(offsite)} row(s) start off-domain or on a content mill.\n")
    rescued = worsened_approved = 0
    for r in offsite:
        url, name, org = r["url"], r.get("name") or "", r.get("org") or ""
        verdict = (verdicts.get(r["id"]) or {}).get("verdict")
        grounding = _grounding_for(r, cand, seed_resolved)
        new_url, flags = so.resolve_url_truth({"name": name, "org": org}, url, [], grounding,
                                              uv.DEFAULT_TIMEOUT)
        ok = (new_url != url and not uv.is_content_mill(new_url)
              and uv.domain_matches_org(new_url, org, name))
        if ok:
            rescued += 1
        # An approved row must never be swapped to a title-proof-FAILING URL. resolve only ever
        # swaps to a proven URL, so this should always be 0 — checked, not assumed.
        if verdict == "approved" and new_url != url:
            v, _ = __import__("url_repair").title_proof_url(new_url, name, org)
            if v is False:
                worsened_approved += 1
        tag = "RESCUED " if ok else ("flagged" if flags else "kept   ")
        print(f"  [{tag}] {verdict or '?':8} {name[:44]:44}")
        print(f"            was: {url}")
        if new_url != url:
            print(f"            now: {new_url}")
        elif flags:
            print(f"            flags: {flags}")

    print(f"\n[RESULT] {rescued}/{len(offsite)} off-domain rows rescued to an org-domain, "
          f"title-proven URL. Approved rows given a proof-failing URL: {worsened_approved} "
          f"(must be 0).")


if __name__ == "__main__":
    main()

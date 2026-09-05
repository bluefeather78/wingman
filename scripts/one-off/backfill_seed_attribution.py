#!/usr/bin/env python3
"""Backfill opportunities.seed_id onto already-scraped rows, by URL match to the run logs.

Attribution (opportunities.seed_id) went live 2026-08-26, so every row scraped before then
has a NULL seed_id and cannot appear in the per-angle funnel. This one-off recovers what it
can: every scraper run wrote per-seed research logs to agent_logs/scraper_<stamp>_seed<id>.json,
each carrying that seed's numeric id (in the filename) and the URLs it produced. We match a
stored row's URL back to the seed that found it and stamp seed_id.

The match is necessarily FUZZY — the stored URL is the reconciled one, which can differ from
the model's candidate URL when grounding replaced it — so a row that cannot be matched
unambiguously is left NULL and counted in a `(no seed)` bucket, never guessed at. That mirrors
the plan: unattributable rows are reported, never dropped, and never mis-attributed.

FREE (no API calls). Writes to the live catalog only with --commit; --preview (default) and
--dry-run resolve the matches and print the plan without touching anything.

    python backfill_seed_attribution.py                 # preview (no writes)
    python backfill_seed_attribution.py --commit         # apply
    python backfill_seed_attribution.py --source-like scraper-national-20260823 --commit

Requires db/scraper_attribution_schema.sql to have been run (seed_id must exist); it says so
if the PATCH is rejected for a missing column.
"""
import argparse
import glob
import json
import os
import re

import sys
# This script lives under scripts/one-off/ but imports the repo-root shared libraries below by bare name
# (supabase_common, url_dedupe), the way every root script does.
# Running it as `python scripts/one-off/backfill_seed_attribution.py` puts its OWN directory on sys.path, not the
# repo root, so the root has to be added explicitly.
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import url_dedupe
from supabase_common import load_dotenv, supabase_get, supabase_patch

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(REPO_ROOT, "agent_logs")
# scraper_20260823-012518_seed14.json  and  scraper_20260819_seed5.json (date-only stamp).
_LOG_RE = re.compile(r"scraper_(\d{8})(?:-\d{6})?_seed(\d+)\.json$")
_DATE_RE = re.compile(r"(\d{8})")


def _mk(url):
    """Robust dedupe key for matching (host + case-folded path), or None."""
    if not url:
        return None
    try:
        return url_dedupe.match_key(url)
    except Exception:
        return None


def _source_date(source):
    """The YYYYMMDD run-date embedded in a row's `source` (e.g. scraper-national-20260823)."""
    m = _DATE_RE.search(source or "")
    return m.group(1) if m else None


def build_url_maps(log_dir=LOG_DIR):
    """Two match_key -> {(seed_id, run_date)} maps: candidate URLs and resolved source URLs.

    The date is what makes this safe. A per-seed log's `candidates` are the model's raw
    phase-2 output, BEFORE the dedupe/reject loop — so it includes URLs the seed merely
    re-encountered and had rejected as exact duplicates of rows some OTHER run created. Keyed
    by URL alone, a mined-out re-finder would be credited with rows it never made. Pairing
    each proposal with its run-date lets attribute() insist the row's own source-date matches,
    which — because every seed that ran is logged that run — collapses an intra-day collision
    to an ambiguous (creator + re-finder both present) skip and rules a cross-day re-find out
    entirely (the created row carries a different date).
    """
    cand, resolved = {}, {}
    files = sorted(glob.glob(os.path.join(log_dir, "scraper_*_seed*.json")))
    scanned = 0
    for path in files:
        m = _LOG_RE.search(os.path.basename(path))
        if not m:
            continue  # seedNone (fallback angle, no id), or an unparseable name
        date, seed_id = m.group(1), int(m.group(2))
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        scanned += 1
        for c in (data.get("candidates") or []):
            if isinstance(c, dict):
                k = _mk(c.get("url"))
                if k:
                    cand.setdefault(k, set()).add((seed_id, date))
        for u in (data.get("resolved_urls") or []):
            k = _mk(u)
            if k:
                resolved.setdefault(k, set()).add((seed_id, date))
    return cand, resolved, scanned, len(files)


def attribute(row, cand, resolved):
    """(seed_id, how) for a row, or (None, reason).

    A match only counts when the proposal came from the SAME run-date as the row's source, so
    a URL a seed re-found on another day never attributes. Candidate beats resolved; more than
    one same-day seed for a URL is ambiguous and refused rather than guessed.
    """
    k = _mk(row.get("url"))
    if not k:
        return None, "no-url"
    date = _source_date(row.get("source"))
    for pool, how in ((cand, "candidate"), (resolved, "resolved")):
        hits = pool.get(k)
        if not hits:
            continue
        same_day = {sid for (sid, d) in hits if date and d == date}
        if len(same_day) == 1:
            return next(iter(same_day)), how
        if len(same_day) > 1:
            return None, "ambiguous"
        # Hits exist but none on this row's run-date: a seed proposed the URL on another day
        # and did not create this row. Fall through to the next pool, then to (no seed).
    return None, "unmatched"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true",
                    help="Apply the seed_id writes. Without it this is a dry preview.")
    ap.add_argument("--dry-run", action="store_true", help="Alias for the default (no writes).")
    ap.add_argument("--source-like", default="scraper-",
                    help="Only rows whose `source` ILIKE this (default 'scraper-'). Narrow it "
                         "to a batch, e.g. 'scraper-national-20260823'.")
    ap.add_argument("--overwrite", action="store_true",
                    help="Also (re)stamp rows that already have a seed_id. Off by default — a "
                         "live run's stamp is authoritative and must not be clobbered.")
    args = ap.parse_args()
    commit = args.commit and not args.dry_run

    load_dotenv()
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    service_key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_ANON_KEY")
    if not supabase_url or not service_key:
        print("[ERROR] SUPABASE_URL and a key must be set in .env.")
        raise SystemExit(1)

    cand, resolved, scanned, total_logs = build_url_maps()
    print(f"[OK] Scanned {scanned}/{total_logs} per-seed log(s): "
          f"{len(cand)} candidate URL key(s), {len(resolved)} resolved URL key(s).")

    params = {"select": "id,name,url,source,seed_id", "source": f"ilike.{args.source_like}%",
              "order": "id.asc"}
    try:
        rows = supabase_get(supabase_url, "opportunities", params, service_key)
    except Exception as e:
        print(f"[ERROR] Could not read opportunities ({e}). "
              f"If this names seed_id, run db/scraper_attribution_schema.sql first.")
        raise SystemExit(1)

    todo, buckets = [], {"candidate": 0, "resolved": 0, "unmatched": 0,
                         "ambiguous": 0, "no-url": 0, "already": 0}
    for r in rows:
        if r.get("seed_id") is not None and not args.overwrite:
            buckets["already"] += 1
            continue
        sid, how = attribute(r, cand, resolved)
        if sid is not None:
            buckets[how] += 1
            todo.append((r, sid, how))
        else:
            buckets[how] += 1

    print(f"[PLAN] {len(rows)} '{args.source_like}%' row(s): "
          f"{buckets['candidate']} by candidate, {buckets['resolved']} by resolved-source, "
          f"already stamped {buckets['already']}; "
          f"(no seed) — unmatched {buckets['unmatched']}, ambiguous {buckets['ambiguous']}, "
          f"no-url {buckets['no-url']}.")

    by_seed = {}
    for _, sid, _ in todo:
        by_seed[sid] = by_seed.get(sid, 0) + 1
    if by_seed:
        print("[PLAN] attributable rows per seed: "
              + ", ".join(f"seed {sid}: {n}" for sid, n in sorted(by_seed.items())))

    if not commit:
        print(f"[PREVIEW] No writes. Re-run with --commit to stamp {len(todo)} row(s).")
        return

    ok = fail = 0
    for r, sid, _ in todo:
        try:
            supabase_patch(supabase_url, "opportunities", {"id": f"eq.{r['id']}"},
                           {"seed_id": sid}, service_key)
            ok += 1
        except Exception as e:
            fail += 1
            if fail <= 5:
                print(f"  [WARN] {r['id']}: {str(e)[:160]}")
    print(f"[OK] Stamped seed_id on {ok} row(s); {fail} failed. "
          f"{buckets['unmatched'] + buckets['ambiguous'] + buckets['no-url']} left in (no seed).")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Loading and scoring the scraper's search angles ("seeds").

A seed is a (category, angle) pair: the category drives the fallback `type` value and
provenance on any row the seed produces, and the angle is interpolated into the scraper's
system prompt as the specific thing that call should go looking for.

These used to be hardcoded Python lists in scrape_opportunities.py, which meant changing
what the scraper looks for required editing source, and there was no way to tell which
angles actually earn their keep — every angle costs a paid API call on every run, whether
it returns ten new opportunities or ten duplicates you already have.

They now live in a Supabase `scraper_seeds` table so the admin console can add, edit,
enable/disable and delete them, and so each one accumulates its own yield history.

    create table scraper_seeds (
        id           bigint generated always as identity primary key,
        mode         text not null,              -- 'national' | 'seattle'
        category     text not null,
        angle        text not null,
        is_enabled   boolean not null default true,
        sort_order   integer,
        total_runs   integer default 0,
        total_found  integer default 0,          -- raw candidates returned
        total_added  integer default 0,          -- rows actually inserted
        total_dupes  integer default 0,
        total_cost   numeric default 0,
        last_run_at  timestamptz,
        created_at   timestamptz default now()
    );

FALLBACK: the original literals stay in scrape_opportunities.py and are used if the table
is empty or unreachable. A Supabase outage should degrade the scraper to "runs the angles
it shipped with", not "silently has nothing to do". load_seeds() always logs which source
it used, because a run that quietly used stale fallback angles would otherwise look
identical to a normal run.

Totals are lifetime running counters PATCHed onto the seed row after each seed finishes,
rather than a per-run join table. That is exactly what the console displays and keeps the
scraper's loop simple; the tradeoff is no per-run seed history, only aggregates.
"""
import datetime

from supabase_common import supabase_get, supabase_patch

SEED_SELECT = "id,mode,category,angle,is_enabled,sort_order,total_runs,total_found,total_added,total_dupes,total_cost"


def load_seeds(supabase_url, service_key, mode, fallback=None, include_disabled=False):
    """Return the seed list for `mode` as dicts, newest schema shape.

    Falls back to `fallback` (a list of (category, angle) tuples) if the table is empty
    or the request fails. Fallback seeds carry id=None, which callers must treat as
    "cannot record yield for this one".
    """
    params = {"select": SEED_SELECT, "mode": f"eq.{mode}", "order": "sort_order.asc,id.asc"}
    if not include_disabled:
        params["is_enabled"] = "eq.true"
    rows = []
    try:
        rows = supabase_get(supabase_url, "scraper_seeds", params, service_key) or []
    except Exception as e:
        print(f"[WARN] Could not load scraper_seeds from Supabase: {e}")

    if rows:
        print(f"[OK] Loaded {len(rows)} enabled seed(s) for mode={mode} from scraper_seeds.")
        return rows

    if fallback:
        print(f"[WARN] scraper_seeds returned no rows for mode={mode} — falling back to the "
              f"{len(fallback)} hardcoded seed(s) in scrape_opportunities.py. Per-seed yield "
              f"tracking is DISABLED for this run (fallback seeds have no database id).")
        return [{"id": None, "mode": mode, "category": c, "angle": a,
                 "is_enabled": True, "sort_order": i}
                for i, (c, a) in enumerate(fallback)]

    print(f"[ERROR] No seeds available for mode={mode}.")
    return []


def select_seeds(seeds, seed_ids=None, seed_indices=None):
    """Narrow a loaded seed list to an explicit subset.

    `seed_ids` selects by the stable database id and is what the admin console sends.
    `seed_indices` selects by position in the list and is kept only for backwards
    compatibility with commands saved before seeds were editable — positions shift
    whenever a seed is added, deleted or reordered, so an index list written last month
    may well select different angles today. Prefer seed_ids.
    """
    if seed_ids:
        wanted = [int(x.strip()) for x in str(seed_ids).split(",") if x.strip()]
        by_id = {s["id"]: s for s in seeds if s.get("id") is not None}
        missing = [i for i in wanted if i not in by_id]
        if missing:
            print(f"[WARN] seed id(s) not found or not enabled for this mode: {missing}")
        chosen = [by_id[i] for i in wanted if i in by_id]
        print(f"[OK] Running {len(chosen)} of {len(seeds)} seed(s) by id: {[s['id'] for s in chosen]}")
        return chosen

    if seed_indices:
        idx = [int(x.strip()) for x in str(seed_indices).split(",") if x.strip()]
        valid = [i for i in idx if 0 <= i < len(seeds)]
        if len(valid) != len(idx):
            print(f"[WARN] ignoring out-of-range seed indices: {sorted(set(idx) - set(valid))}")
        print(f"[WARN] --seed-indices selects by POSITION, which changes whenever seeds are "
              f"added/deleted/reordered. Use --seed-ids for a stable reference.")
        print(f"[OK] Running {len(valid)} of {len(seeds)} seed(s): indices {valid}")
        return [seeds[i] for i in valid]

    return seeds


def record_seed_result(supabase_url, service_key, seed, found=0, added=0, dupes=0, cost=0.0):
    """PATCH one seed's lifetime yield totals after it finishes.

    No-ops for fallback seeds (id is None). Failures are logged and swallowed: losing a
    stats update must never abort a scrape that already spent money on real results.
    """
    seed_id = seed.get("id")
    if seed_id is None:
        return False
    patch = {
        "total_runs": (seed.get("total_runs") or 0) + 1,
        "total_found": (seed.get("total_found") or 0) + found,
        "total_added": (seed.get("total_added") or 0) + added,
        "total_dupes": (seed.get("total_dupes") or 0) + dupes,
        "total_cost": round(float(seed.get("total_cost") or 0) + cost, 4),
        "last_run_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    try:
        supabase_patch(supabase_url, "scraper_seeds", {"id": f"eq.{seed_id}"}, patch, service_key)
        return True
    except Exception as e:
        print(f"  [WARN] Could not update yield stats for seed {seed_id}: {e}")
        return False

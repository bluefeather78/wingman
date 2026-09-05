#!/usr/bin/env python3
"""The scraper's self-learning ledger: per-angle funnels and their diagnosis.

The catalog IS the ledger. Every scraped row carries the `seed_id` of the angle that
found it, and the reviewer's verdict lands on that same row (`moderation_status` +
`moderation_reason`). So "how is this angle doing" is a live GROUP BY over
`opportunities`, never a set of writeback counters that can drift out of step with the
verdicts — change a verdict in the console and the funnel changes on the next read, with
nothing to recompute.

This module is deliberately PURE: it takes already-fetched rows and returns dicts. The
I/O (which rows to fetch, and the auto-disable PATCH) lives in the two callers —
`ops/core.get_seed_yield()` for the console and `scrape_opportunities.auto_disable_mined_seeds()`
for the run-end sweep — so both share one implementation of the judgement and both are
unit-testable with no database. Keep it stdlib-only.

Two things this file gets right that a naive version gets wrong:

- **Pending rows are shown but never scored.** A row a human has not looked at yet is not
  evidence for or against the angle; folding it into the rates would make a fresh, unre-
  viewed run look like a failing angle. Rates use the *adjudicated* set only
  (approved + rejected + duplicate).
- **The waste rate counts EVERYTHING the angle found, not just what reached the queue**
  (operator correction #1, 2026-08-26). Internal discards (dupes skipped, invalid, no-URL)
  count against the angle exactly like a human reject does — otherwise a broad angle that
  re-buys 40 duplicates before the queue ever sees them reads as clean.
"""

# ---- reason codes (operator decision, 2026-08-26) --------------------------------------
# Rejects require a reason; approvals stay one-click. moderation_reason is stored as either
# a bare `code` or `code: free-text note`, so the code is everything before the first colon.
REASON_CODES = ("duplicate", "third-party-url", "wrong-page", "dead-link",
                "not-a-fit", "low-quality", "other")

# Each negative reason points at ONE failure mode. Note dead-link and the two URL-shape
# codes all mean "the angle found a real program, our pipeline mishandled the URL" — the
# fix is in Phase 2/4, never in retiring the angle, so they map to pipeline_limited.
_SIGNAL_GROUP = {
    "duplicate":       "mined_out",        # the angle keeps re-finding rows we already have
    "not-a-fit":       "mis_aimed",        # real programs, wrong audience/kind for this app
    "third-party-url": "pipeline_limited",  # listicle/blog URL — URL truth layer's job
    "wrong-page":      "pipeline_limited",  # right site, wrong page — URL truth layer's job
    "dead-link":       "pipeline_limited",  # real program, rotted link — refind's job
    "low-quality":     "thin",             # real, on-topic, but not worth a student's time
}

# Sample guard: an angle is only diagnosed once there is enough of it to trust the verdict
# mix. Below any of these it reads `insufficient_sample` and can never be auto-disabled.
MIN_FOUND = 10          # raw candidates the model returned across all runs
MIN_RUNS = 2            # ... spread over at least two runs (one lucky/unlucky run is noise)
MIN_ADJUDICATED = 5     # ... and at least this many human verdicts to read a mix from

# Above this approval rate the angle is healthy and is never picked apart by reason —
# a productive angle that also re-buys some dupes must not be retired for the dupes.
HEALTHY_APPROVAL = 0.5

# Only these two diagnoses ever auto-disable. mis_aimed and pipeline_limited are real
# problems whose fix is elsewhere (a better angle, or the URL/refind pipeline), so killing
# the angle would hide the problem without fixing it.
AUTO_DISABLE_DIAGNOSES = ("mined_out", "thin")

_PENDING_STATUSES = (None, "", "pending_review")
_NEGATIVE_STATUSES = ("rejected", "duplicate")


def reason_code(reason):
    """The code half of a `moderation_reason`, or None. `code` and `code: note` both → code."""
    if not reason:
        return None
    code = str(reason).split(":", 1)[0].strip().lower()
    return code or None


def _negative_code(status, reason):
    """The reason code to file a negative row under.

    A duplicate row whose reason was auto-filled from its survivor may still be bare — it is
    a duplicate by construction, so file it as one rather than as `other`. A reject with no
    code should not happen (reasons are required going forward) but legacy/backfill rows can
    have none; they fall to `other`, which signals nothing and is never auto-disabled on.
    """
    code = reason_code(reason)
    if code:
        return code
    return "duplicate" if status == "duplicate" else "other"


def _empty_funnel(seed_id, seed):
    seed = seed or {}
    return {
        "seed_id": seed_id,
        # live counts from the opportunities table (rows this angle minted)
        "queue_total": 0, "pending": 0, "approved": 0, "rejected": 0, "duplicate": 0,
        "active": 0, "reason_mix": {},
        # lifetime counters from the scraper_seeds row
        "found": int(seed.get("total_found") or 0),
        "added": int(seed.get("total_added") or 0),
        "dupes": int(seed.get("total_dupes") or 0),
        "runs": int(seed.get("total_runs") or 0),
        "cost": float(seed.get("total_cost") or 0.0),
    }


def build_seed_funnels(opp_rows, seed_rows):
    """Per-seed funnel dicts keyed by seed_id, from opportunities rows + scraper_seeds rows.

    `opp_rows`: dicts with at least seed_id, moderation_status, moderation_reason, is_active.
    `seed_rows`: scraper_seeds dicts with id and the total_* counters.

    Every seed in seed_rows gets a funnel (even one with zero attributed rows yet); an
    opp row whose seed_id is not among seed_rows is still counted under its own id, so a
    backfilled row can never be silently dropped.
    """
    seeds_by_id = {}
    for s in (seed_rows or []):
        sid = s.get("id")
        if sid is not None:
            seeds_by_id[sid] = s

    funnels = {}

    def _funnel(sid):
        if sid not in funnels:
            funnels[sid] = _empty_funnel(sid, seeds_by_id.get(sid))
        return funnels[sid]

    # Seed every known angle so the grid shows zero-row angles rather than hiding them.
    for sid in seeds_by_id:
        _funnel(sid)

    for row in (opp_rows or []):
        sid = row.get("seed_id")
        if sid is None:
            continue
        f = _funnel(sid)
        f["queue_total"] += 1
        if row.get("is_active"):
            f["active"] += 1
        status = row.get("moderation_status")
        if status in _PENDING_STATUSES:
            f["pending"] += 1
        elif status == "approved":
            f["approved"] += 1
        elif status in _NEGATIVE_STATUSES:
            f[status] += 1
            code = _negative_code(status, row.get("moderation_reason"))
            f["reason_mix"][code] = f["reason_mix"].get(code, 0) + 1
        else:
            # An unknown status is treated as pending: it is certainly not an approval, and
            # counting it as a negative would punish the angle for a state we don't model.
            f["pending"] += 1

    for f in funnels.values():
        _finalize(f)
    return funnels


def _finalize(f):
    """Fill in the derived rates and the diagnosis on a funnel dict, in place."""
    adjudicated = f["approved"] + f["rejected"] + f["duplicate"]
    f["adjudicated"] = adjudicated
    f["approval_rate"] = (f["approved"] / adjudicated) if adjudicated else None

    # Waste = (internal discards + human rejects + human dups) / found. Internal discards
    # are found candidates that never reached the queue (dupes skipped, invalid, no URL).
    # The seed's `total_found` counter can under-count the rows actually attributed to it
    # (a backfilled row minted before the counter existed), which would push waste past 100%;
    # clamp the denominator up to the queue count so the rate stays in [0, 1].
    found = f["found"]
    if found:
        reached_queue = f["queue_total"]
        effective_found = max(found, reached_queue)
        internal_discards = effective_found - reached_queue
        f["waste_rate"] = round(
            (internal_discards + f["rejected"] + f["duplicate"]) / effective_found, 3)
    else:
        f["waste_rate"] = None

    f["cost_per_approved"] = round(f["cost"] / f["approved"], 4) if f["approved"] else None
    f["diagnosis"] = diagnose(f)
    f["auto_disable"] = should_auto_disable(f)


def diagnose(funnel):
    """One of: healthy | mined_out | mis_aimed | pipeline_limited | thin | insufficient_sample.

    Order matters. The sample guard comes first (never diagnose a problem from too little
    data), then a healthy angle is exempt from reason analysis (don't retire a producer for
    also finding some dupes), and only then is the negative reason mix read for its plurality.
    An angle whose negatives carry no signal code falls to pipeline_limited — the conservative
    "the fix is elsewhere, don't punish the angle" bucket — because we would rather leave a
    genuinely-tired angle enabled than auto-disable one we cannot actually diagnose.
    """
    found = funnel.get("found") or 0
    runs = funnel.get("runs") or 0
    adjudicated = funnel.get("adjudicated") or 0
    if found < MIN_FOUND or runs < MIN_RUNS or adjudicated < MIN_ADJUDICATED:
        return "insufficient_sample"

    approval_rate = funnel.get("approval_rate")
    if approval_rate is not None and approval_rate >= HEALTHY_APPROVAL:
        return "healthy"

    # Tally negatives into their failure-mode groups, and count separately the ones that
    # carry no signal code (legacy/backfill rejects, or `other`) — those diagnose nothing.
    groups = {"mined_out": 0, "mis_aimed": 0, "pipeline_limited": 0, "thin": 0}
    unknown = 0
    for code, n in (funnel.get("reason_mix") or {}).items():
        group = _SIGNAL_GROUP.get(code)
        if group:
            groups[group] += n
        else:
            unknown += n

    if not any(groups.values()):
        # Negatives dominate but none carry a codable reason — can't tell why. Don't disable.
        return "pipeline_limited"

    # Plurality wins; ties break toward the least-punishing (never auto-disable on a tie).
    tie_order = {"pipeline_limited": 0, "mis_aimed": 1, "thin": 2, "mined_out": 3}
    best = max(groups, key=lambda g: (groups[g], -tie_order[g]))
    # The winning signal must also outweigh the UNCODEABLE pile, or its "plurality" is just a
    # handful of coded rows amid mostly-unlabelled rejects — the common state during the
    # backfill period, and exactly when auto-disabling on 3 coded dupes among 20 blanks would
    # be wrong. When it doesn't, decline to retire the angle.
    if groups[best] <= unknown:
        return "pipeline_limited"
    return best


def should_auto_disable(funnel):
    """True only for a mined_out/thin angle that also clears the sample guard.

    diagnose() already returns insufficient_sample below the guard, so the found/runs checks
    here are belt-and-braces — but they keep this function honest if it is ever called on a
    funnel whose diagnosis was computed elsewhere.
    """
    if (funnel.get("found") or 0) < MIN_FOUND or (funnel.get("runs") or 0) < MIN_RUNS:
        return False
    return funnel.get("diagnosis") in AUTO_DISABLE_DIAGNOSES


def disable_reason(funnel):
    """The human-readable `disabled_reason` string stamped when auto-disabling an angle."""
    return (f"auto: {funnel.get('diagnosis')} — {funnel.get('found', 0)} found, "
            f"{funnel.get('approved', 0)} approved, {funnel.get('duplicate', 0)} dup, "
            f"{funnel.get('rejected', 0)} rejected")

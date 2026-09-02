#!/usr/bin/env python3
"""One-stop database health check for the opportunity catalog. FREE, read-only.

Every other agent in this repo does one job to the catalog; this one only LOOKS. It answers,
in a single pass and without spending a cent, the questions you otherwise have to open five
console tabs and run three scripts to piece together:

  * how deep are the work queues?  — the review queue, the suspected-duplicate queue, the
    hub-mining and name-harvest lead queues, the mailing-list recipes awaiting review, and the
    metadata-refresh backlog (rows activated but never enriched);
  * how much of the catalog is covered? — how many ACTIVE rows have no dedupe embedding, no
    review verdict, no link check, no deadline check, no verified action items;
  * when did each maintenance pass last run? — reviews, link health, deadlines, tasks, the
    scraper, metadata refresh, mining/harvest, read straight from the authoritative agent_runs
    table (accurate even for runs triggered by cron or a bare CLI, not just this console);
  * and a short list of ALERTS — the handful of numbers that are out of their healthy band.

WHY A SEPARATE SCRIPT and not just SQL: some of what matters here does not live in Postgres.
The two lead queues are a repo-root JSONL (discovered_leads.jsonl). A health check that only
queried the database would silently report "0 leads" because it was looking in the wrong place.
This reads both sources — Supabase and the leads file — and reconciles them. (The dedupe
embeddings USED to be a repo-root JSONL too, which is exactly why a fresh checkout read "0%
covered"; they now live in opportunities.dedupe_vector, read from the catalog like every other
coverage column — see dedupe_vector_schema.sql.)

FREE and SAFE: it issues read-only PostgREST GETs and reads two local files. It makes no model
call, writes nothing, and is safe to run as often as you like. Unlike the six paid agents there
is no approval to seek before running it.

    python db_health_check.py            # print the report
    python db_health_check.py --json     # emit the same data as one JSON object (for tooling)

The admin console's **Health** tab renders exactly this data — ops/core.get_db_health() calls
collect_health() in-process, so the tab and the CLI can never disagree about the numbers.
"""
import argparse
import datetime
import json
import os
import urllib.error
import urllib.parse
import urllib.request

import discovered_leads
from supabase_common import load_dotenv, supabase_get

# ---------------------------------------------------------------------------------------------
# Constants mirrored from ops/core.py and app/services/mailing_list.py. Duplicated here on
# purpose: this script is stdlib-only offline plumbing (like every other agent at the repo root)
# and must not import ops.core, which pulls in FastAPI. If either source list changes, change
# this too — the same trade the three copies of normalize_url() already make.
# ---------------------------------------------------------------------------------------------
SIGNUPS_TABLE = "opportunity_signups"                       # app/services/mailing_list.SIGNUPS_TABLE
QUEUE_STATUSES = ("pending_review", "approved")             # awaiting a human (plus the NULL case)
ADJUDICATED_STATUSES = ("rejected", "duplicate")            # already decided away
FLAGGED_STATUS = "suspected_duplicate"                      # flag-in-place: still LIVE, needs a human

# Staleness thresholds. STALE_AFTER_DAYS matches check_reviews.py's own 30-day filter so this
# report and the agent agree on what "due" means. The queue/coverage thresholds below are the
# bands that turn a plain count into a green / warn / alert signal on the console.
STALE_AFTER_DAYS = 30
_WARN = "warn"
_ALERT = "alert"
_OK = "ok"

# The agent_runs.agent literals whose most-recent run this report surfaces, paired with the
# console-facing label. Read straight from the table, so a run from cron or a bare CLI counts.
CHECK_AGENTS = [
    ("review_checker", "Reviews"),
    ("link_checker", "Link health"),
    ("deadline_checker", "Deadlines"),
    ("action_item_generator", "Action items (tasks)"),
    ("metadata_refresher", "Metadata refresh"),
    ("scraper", "New-opportunity scrape"),
    ("hub_miner", "Hub mining"),
    ("name_harvester", "Name harvest"),
    ("mailing_list_finder", "Mailing-list finder"),
    ("contact_email_finder", "Contact-email backfill"),
]

# The active-row columns this report reads to compute coverage. Fetched in ONE paginated GET
# rather than a count query per metric. Several arrived in later migrations (link_* in
# link_health_schema.sql, action_items_checked_at in action_items_schema.sql), so a database
# migrated before them 400s the whole select — _fetch_active_rows() degrades to the base set and
# marks the missing coverage "unavailable" rather than failing the whole report.
_ACTIVE_FULL_SELECT = ("id,type,review_status,last_reviewed_at,link_status,link_checked_at,"
                       "dates_last_checked_at,action_items_source,action_items_checked_at,"
                       "dedupe_vector_hash,match_vector_hash")
_ACTIVE_BASE_SELECT = "id,type"


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _parse_iso(value):
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _age_days(iso):
    """Whole days since `iso`, or None if it can't be parsed."""
    dt = _parse_iso(iso)
    if not dt:
        return None
    return (_now() - dt).total_seconds() / 86400.0


# ---------------------------------------------------------------------------------------------
# Supabase reads. A count is a HEAD-style GET with Prefer: count=exact — the row bytes never
# travel, only the Content-Range header's total — so queue depths cost almost nothing to read.
# ---------------------------------------------------------------------------------------------

def _count(supabase_url, key, table, filters, timeout=20):
    """Exact row count for `filters`, via PostgREST's Content-Range header. Returns None on error
    (the caller renders that as "—", never as a real 0).

    Deliberately does NOT force a `select` column: with Range 0-0 no row bytes travel either way,
    and naming a column that a given table lacks (opportunity_signups has no `id`) 400s the whole
    count. A filter that names a missing column still 400s — that is the intended "unavailable".
    """
    params = dict(filters)
    url = f"{supabase_url}/rest/v1/{table}?{urllib.parse.urlencode(params)}" if params \
        else f"{supabase_url}/rest/v1/{table}"
    req = urllib.request.Request(url, headers={
        "apikey": key, "Authorization": f"Bearer {key}",
        "Range-Unit": "items", "Range": "0-0", "Prefer": "count=exact"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            cr = resp.headers.get("Content-Range", "")
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
        print(f"[WARN] count {table} {filters} failed: {e}")
        return None
    total = cr.rsplit("/", 1)[-1] if "/" in cr else ""
    return int(total) if total.isdigit() else None


def _fetch_active_rows(supabase_url, key):
    """Every active row's coverage columns, in one paginated GET. Returns (rows, columns_ok).

    columns_ok is False when the later-migration columns aren't there yet — the report still
    counts rows and breaks them down by type, it just can't speak to review/link/deadline/task
    freshness. Same one-step degradation list_pending_opportunities() uses for its own selects.
    """
    try:
        rows = supabase_get(supabase_url, "opportunities",
                            {"select": _ACTIVE_FULL_SELECT, "is_active": "eq.true"}, key)
        return rows, True
    except urllib.error.HTTPError as e:
        if e.code != 400:
            raise
    rows = supabase_get(supabase_url, "opportunities",
                        {"select": _ACTIVE_BASE_SELECT, "is_active": "eq.true"}, key)
    return rows, False


def _latest_runs(supabase_url, key, limit=400):
    """The most-recent agent_runs row per agent. One GET, newest first, first-seen wins.

    Mirrors ops/core._run_status: a row with errors is 'failed', one with a finished_at is
    'success', one with neither that is recent is 'running', and an old unfinished one is
    'interrupted' — it crashed before patching its totals, so its counts are understated.
    """
    try:
        rows = supabase_get(supabase_url, "agent_runs",
                            {"select": "agent,mode,started_at,finished_at,items_processed,"
                                       "items_added,items_updated,errors,cost_usd,notes",
                             "order": "started_at.desc", "limit": str(limit)}, key)
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
        print(f"[WARN] could not read agent_runs: {e}")
        return {}
    latest = {}
    for r in rows or []:
        agent = r.get("agent")
        if agent and agent not in latest:
            latest[agent] = r
    return latest


def _run_status(row):
    if not row:
        return "never"
    if row.get("errors"):
        return "failed"
    if row.get("finished_at"):
        return "success"
    started = _parse_iso(row.get("started_at"))
    if not started:
        return "interrupted"
    # 30 min is comfortably longer than any run except a full catalog pass; a batch pass
    # patches partial totals as it goes, so this only mislabels a genuinely wedged process.
    age = (_now() - started).total_seconds()
    return "running" if age < 1800 else "interrupted"


def _shape_run(row):
    """One agent_runs row as the report shows it — the numbers that say what the last pass did."""
    if not row:
        return None
    return {
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "status": _run_status(row),
        "mode": row.get("mode"),
        "items_processed": row.get("items_processed") or 0,
        "items_added": row.get("items_added") or 0,
        "items_updated": row.get("items_updated") or 0,
        "errors": row.get("errors") or 0,
        "cost_usd": row.get("cost_usd"),
        "age_days": _age_days(row.get("finished_at") or row.get("started_at")),
    }


# ---------------------------------------------------------------------------------------------
# The report.
# ---------------------------------------------------------------------------------------------

def _sev(count, warn, alert):
    """A severity band for a queue depth / gap count. None stays None (unreadable, not zero)."""
    if count is None:
        return _OK
    if alert is not None and count >= alert:
        return _ALERT
    if warn is not None and count >= warn:
        return _WARN
    return _OK


def collect_health(supabase_url=None, key=None):
    """Gather every health metric into one dict. FREE, read-only. Never raises — a subsystem it
    can't read is reported as unavailable inside the result, so a single dead source (a missing
    table, an absent leads file) degrades that section rather than the whole report.

    Args default to the environment; the console passes its configured service creds directly.
    """
    supabase_url = (supabase_url if supabase_url is not None
                    else os.environ.get("SUPABASE_URL", "")).rstrip("/")
    key = (key if key is not None
           else (os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_ANON_KEY", "")))

    out = {
        "ok": True,
        "generated_at": _now().isoformat(),
        "supabase_configured": bool(supabase_url and key),
        "errors": [],
    }
    if not out["supabase_configured"]:
        out["ok"] = False
        out["errors"].append("SUPABASE_URL and a key (service or anon) must be set in .env.")
        # The local-file sections still work with no database, so keep going.

    # --- Catalog overview + coverage (one fetch of active rows) --------------------------
    active_rows, columns_ok, active_ids = [], False, set()
    if out["supabase_configured"]:
        try:
            active_rows, columns_ok = _fetch_active_rows(supabase_url, key)
            active_ids = {r.get("id") for r in active_rows if r.get("id")}
        except Exception as e:                                  # noqa: BLE001 — report, don't crash
            out["errors"].append(f"Could not read active opportunities: {e}")

    total = _count(supabase_url, key, "opportunities", {}) if out["supabase_configured"] else None
    active_n = len(active_rows) if active_rows else (
        _count(supabase_url, key, "opportunities", {"is_active": "eq.true"})
        if out["supabase_configured"] else None)
    by_type = {}
    for r in active_rows:
        t = r.get("type") or "(untyped)"
        by_type[t] = by_type.get(t, 0) + 1
    out["catalog"] = {
        "total": total,
        "active": active_n,
        "inactive": (total - active_n) if (total is not None and active_n is not None) else None,
        "by_type": dict(sorted(by_type.items(), key=lambda kv: kv[1], reverse=True)),
        "coverage_columns_available": columns_ok,
    }

    # --- Work queues -----------------------------------------------------------------------
    queues = []
    if out["supabase_configured"]:
        # Review queue: is_active=false AND (moderation_status null OR in the queue statuses).
        # NULL is the scraper/pre-migration case and means "never adjudicated", so it must be
        # counted — NULL NOT IN (...) is NULL in SQL, which would drop every scraper row.
        review_q = _count(supabase_url, key, "opportunities", {
            "is_active": "eq.false",
            "or": (f"(moderation_status.is.null,moderation_status.in."
                   f"({','.join(QUEUE_STATUSES)}))")})
        rejected_q = _count(supabase_url, key, "opportunities", {
            "is_active": "eq.false",
            "moderation_status": f"in.({','.join(ADJUDICATED_STATUSES)})"})
        # Suspected duplicates are left LIVE (is_active=true) on purpose so students still see
        # them until a human releases or confirms the pair — the one queue of active rows.
        dup_q = _count(supabase_url, key, "opportunities", {
            "is_active": "eq.true", "moderation_status": f"eq.{FLAGGED_STATUS}"})
        signups_pending = _count(supabase_url, key, SIGNUPS_TABLE,
                                 {"status": "eq.pending_review"})
        signups_verified = _count(supabase_url, key, SIGNUPS_TABLE, {"status": "eq.verified"})

        queues += [
            {"key": "review", "label": "Review queue (awaiting a human)", "count": review_q,
             "severity": _sev(review_q, 100, 300),
             "note": "Inactive rows the scraper/users added; a person activates or rejects them."},
            {"key": "duplicates", "label": "Suspected-duplicate queue (live)", "count": dup_q,
             "severity": _sev(dup_q, 15, 60),
             "note": "Still shown to students until a human releases or confirms the pair."},
            {"key": "signups", "label": "Mailing-list recipes to verify", "count": signups_pending,
             "severity": _sev(signups_pending, 30, 100),
             "note": f"{_fmt(signups_verified)} already verified. Only verified recipes are replayed."},
            {"key": "rejected", "label": "Rejected (kept for reference)", "count": rejected_q,
             "severity": _OK,
             "note": "Not deleted — the URL keeps blocking re-submission and a mistake is reversible."},
        ]

    # Lead queues live in a repo-root JSONL, not Supabase — this is why a SQL-only check misses them.
    try:
        lead_counts = discovered_leads.summarize(discovered_leads.load_leads())
        hub_n = lead_counts.get(discovered_leads.KIND_HUB, 0)
        names_n = lead_counts.get(discovered_leads.KIND_NAMES, 0)
        queues += [
            {"key": "hub_leads", "label": "Hub-mining leads queued", "count": hub_n,
             "severity": _sev(hub_n, 40, None),
             "note": "Pages that LINK many programs. Drain with Mine Hub Pages (PAID). "
                     "Nothing drains them automatically."},
            {"key": "name_leads", "label": "Name-harvest leads queued", "count": names_n,
             "severity": _sev(names_n, 40, None),
             "note": "Pages that NAME programs without linking them. Drain with Harvest Names (PAID)."},
        ]
    except Exception as e:                                      # noqa: BLE001
        out["errors"].append(f"Could not read discovered_leads.jsonl: {e}")

    # Metadata-refresh backlog: rows ACTIVATED but not yet enriched. A row is queued while its
    # activation_refresh_queued_at is non-null; the refresher nulls it on a successful page read
    # (same column ops.core.metadata_refresh_queue reads). Absent on a DB migrated before
    # activation_refresh_schema.sql — _count returns None then, which reads as "—" not a false 0.
    if out["supabase_configured"]:
        refresh_q = _count(supabase_url, key, "opportunities",
                           {"is_active": "eq.true", "activation_refresh_queued_at": "not.is.null"})
        if refresh_q is not None:
            queues.append(
                {"key": "metadata", "label": "Activated rows awaiting metadata refresh",
                 "count": refresh_q, "severity": _sev(refresh_q, 200, 600),
                 "note": "Activated rows the Update Opportunity agent has not enriched yet."})
    out["queues"] = queues

    # --- Embedding coverage: two separate vectors on `opportunities`, same coverage shape --------
    # * dedupe_vector  — the scraper's duplicate-detection embedding (dedupe_vector_schema.sql)
    # * match_vector   — the student-facing semantic RECALL embedding (match_vector_schema.sql)
    # Coverage = active rows carrying the corresponding _hash (written together with the vector),
    # read from the same active-rows fetch above so no extra query and no megabytes of float arrays
    # are pulled. `columns_available` False means the migration has not run (or the FULL select
    # degraded) — coverage is then UNKNOWABLE, not zero, so it must not alarm. This is the fix for
    # the old false "0% covered" a missing per-checkout JSONL sidecar used to produce.
    def _embedding_coverage(hash_field):
        cov = {"columns_available": columns_ok}
        if columns_ok and active_rows:
            indexed = sum(1 for r in active_rows if r.get(hash_field))
            total = len(active_rows)
            pct = round(100.0 * indexed / total, 1) if total else None
            cov.update({"active_rows": total, "indexed": indexed, "missing": total - indexed,
                        "coverage_pct": pct})
            cov["severity"] = (_ALERT if pct is not None and pct < 80
                               else _WARN if pct is not None and pct < 95 else _OK)
        else:
            # No column yet, or no active rows to speak of: report nothing rather than a scary zero.
            cov.update({"active_rows": active_n, "indexed": None, "missing": None,
                        "coverage_pct": None, "severity": _OK})
        return cov

    emb = out["embeddings"] = _embedding_coverage("dedupe_vector_hash")
    sem = out["semantic_embeddings"] = _embedding_coverage("match_vector_hash")

    # --- Freshness of each maintenance pass -----------------------------------------------
    latest = _latest_runs(supabase_url, key) if out["supabase_configured"] else {}
    checks = []
    for agent, label in CHECK_AGENTS:
        checks.append({"key": agent, "label": label, "last_run": _shape_run(latest.get(agent))})
    out["checks"] = checks

    # Coverage gaps computed from the active rows we already fetched — how many active rows have
    # never been through each pass, and how stale the oldest touched one is. Only meaningful when
    # the later-migration columns exist; otherwise the section says so.
    coverage = {"columns_available": columns_ok}
    if columns_ok and active_rows:
        cutoff = (_now() - datetime.timedelta(days=STALE_AFTER_DAYS)).isoformat()

        def _gap(never_pred, stale_field):
            never = sum(1 for r in active_rows if never_pred(r))
            stale = sum(1 for r in active_rows
                        if r.get(stale_field) and str(r.get(stale_field)) < cutoff)
            newest = max((r.get(stale_field) for r in active_rows if r.get(stale_field)),
                         default=None)
            return {"never": never, "stale": stale, "newest": newest, "total": len(active_rows)}

        coverage["reviews"] = _gap(lambda r: not r.get("last_reviewed_at"), "last_reviewed_at")
        coverage["links"] = _gap(lambda r: not r.get("link_checked_at"), "link_checked_at")
        coverage["deadlines"] = _gap(lambda r: not r.get("dates_last_checked_at"),
                                     "dates_last_checked_at")
        coverage["tasks"] = _gap(lambda r: not r.get("action_items_checked_at"),
                                 "action_items_checked_at")
        # Dead links that are STILL active: check_links deactivates a dead link, so a live row
        # marked dead is an anomaly worth surfacing (a flag-only pass, or a row edited back live).
        coverage["dead_links_active"] = sum(
            1 for r in active_rows if str(r.get("link_status") or "").lower() == "dead")
    out["coverage"] = coverage

    # --- Alerts: the handful of numbers out of band, so the eye goes there first -----------
    alerts = []
    for q in queues:
        if q["severity"] == _ALERT:
            alerts.append({"level": _ALERT, "message": f"{q['label']}: {_fmt(q['count'])}"})
        elif q["severity"] == _WARN:
            alerts.append({"level": _WARN, "message": f"{q['label']}: {_fmt(q['count'])}"})
    if emb.get("severity") in (_WARN, _ALERT) and emb.get("missing"):
        alerts.append({"level": emb["severity"],
                       "message": f"{_fmt(emb['missing'])} active rows have no dedupe embedding "
                                  f"({emb.get('coverage_pct')}% covered)"})
    if sem.get("severity") in (_WARN, _ALERT) and sem.get("missing"):
        alerts.append({"level": sem["severity"],
                       "message": f"{_fmt(sem['missing'])} active rows have no semantic (recall) "
                                  f"embedding ({sem.get('coverage_pct')}% covered)"})
    if coverage.get("dead_links_active"):
        alerts.append({"level": _WARN,
                       "message": f"{coverage['dead_links_active']} active rows have a dead link"})
    for agent, label in CHECK_AGENTS:
        run = latest.get(agent)
        st = _run_status(run)
        if st in ("failed", "interrupted"):
            alerts.append({"level": _WARN, "message": f"{label}'s last run {st}"})
    # Order alerts most-severe first, stable within a level.
    alerts.sort(key=lambda a: 0 if a["level"] == _ALERT else 1)
    out["alerts"] = alerts
    return out


# ---------------------------------------------------------------------------------------------
# CLI rendering.
# ---------------------------------------------------------------------------------------------

def _fmt(n):
    return "—" if n is None else f"{n:,}"


def _sev_mark(sev):
    return {"alert": "!!", "warn": " !", "ok": "  "}.get(sev, "  ")


def _print_report(h):
    line = "=" * 78
    print(line)
    print("  DATABASE HEALTH CHECK")
    print(f"  generated {h['generated_at']}")
    print(line)

    if h.get("alerts"):
        print("\nALERTS")
        for a in h["alerts"]:
            print(f"  [{a['level'].upper():5}] {a['message']}")
    else:
        print("\nALERTS\n  none — everything is inside its healthy band.")

    c = h.get("catalog", {})
    print("\nCATALOG")
    print(f"  total rows        {_fmt(c.get('total'))}")
    print(f"  active            {_fmt(c.get('active'))}")
    print(f"  inactive          {_fmt(c.get('inactive'))}")
    if c.get("by_type"):
        types = "  ".join(f"{k}:{v}" for k, v in c["by_type"].items())
        print(f"  active by type    {types}")

    print("\nWORK QUEUES")
    for q in h.get("queues", []):
        print(f"  {_sev_mark(q['severity'])} {q['label']:<38} {_fmt(q['count']):>8}")
        print(f"       {q['note']}")

    def _print_embedding(section, label, missing_col, missing_sql):
        print(f"\n{label}")
        if not section.get("columns_available"):
            print(f"  {_sev_mark(section.get('severity'))} {missing_col} column not present — run "
                  f"{missing_sql} (coverage unknown, not zero)")
        else:
            print(f"  {_sev_mark(section.get('severity'))} indexed {_fmt(section.get('indexed'))} of "
                  f"{_fmt(section.get('active_rows'))} active   "
                  f"missing {_fmt(section.get('missing'))}   coverage "
                  f"{section.get('coverage_pct') if section.get('coverage_pct') is not None else '—'}%")

    _print_embedding(h.get("embeddings", {}), "DEDUPE EMBEDDINGS",
                     "dedupe_vector", "dedupe_vector_schema.sql")
    _print_embedding(h.get("semantic_embeddings", {}), "SEMANTIC EMBEDDINGS (recall match_vector)",
                     "match_vector", "match_vector_schema.sql")

    print("\nLAST RUN OF EACH MAINTENANCE PASS")
    for chk in h.get("checks", []):
        r = chk.get("last_run")
        if not r:
            print(f"  {chk['label']:<26} never run (no agent_runs row)")
            continue
        age = f"{r['age_days']:.1f}d ago" if r.get("age_days") is not None else "?"
        cost = f" ${r['cost_usd']:.4f}" if r.get("cost_usd") not in (None, 0) else ""
        print(f"  {chk['label']:<26} {r['status']:<11} {age:<10} "
              f"processed={r['items_processed']} updated={r['items_updated']} "
              f"added={r['items_added']} errors={r['errors']}{cost}")

    cov = h.get("coverage", {})
    if cov.get("columns_available"):
        print("\nCOVERAGE GAPS (active rows never through each pass / stale >%dd)"
              % STALE_AFTER_DAYS)
        for key in ("reviews", "links", "deadlines", "tasks"):
            g = cov.get(key)
            if g:
                print(f"  {key:<12} never={g['never']:<5} stale={g['stale']:<5} "
                      f"of {g['total']}   newest {g.get('newest') or '—'}")
        if cov.get("dead_links_active") is not None:
            print(f"  dead links still active: {cov['dead_links_active']}")
    else:
        print("\nCOVERAGE GAPS\n  unavailable — the link/review/task timestamp columns are not "
              "migrated in yet (link_health_schema.sql / action_items_schema.sql).")

    if h.get("errors"):
        print("\nREAD ERRORS (sections above degrade rather than fail)")
        for err in h["errors"]:
            print(f"  - {err}")
    print("\n" + line)


def main():
    ap = argparse.ArgumentParser(description="Read-only health check of the opportunity catalog.")
    ap.add_argument("--json", action="store_true", help="Emit the report as one JSON object.")
    args = ap.parse_args()

    # The report uses em dashes / ellipses; on a Windows console defaulting to cp1252 those
    # print as garble. Force UTF-8 where the stream supports it (Python 3.7+); harmless elsewhere.
    try:
        import sys
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    load_dotenv()
    health = collect_health()
    if args.json:
        print(json.dumps(health, indent=2, default=str))
    else:
        _print_report(health)
    # A non-zero exit when the database can't be reached at all, so a cron wrapper can notice.
    raise SystemExit(0 if health.get("supabase_configured") else 1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Commit a dry-run snapshot to Supabase, instead of paying to run the agent again.

A `--dry-run` skips database writes but **still calls the paid API at full cost** — it is
the single most expensive kind of run in this repo relative to what it produces, because
historically the only way to act on its results was to run the whole thing again live and
pay twice. Every agent already dumps exactly what it *would* have written to a local JSON
snapshot. This module replays one of those snapshots into the database.

Nothing here calls an API or spends money: it is pure file-read plus Supabase writes.

The snapshots, and what committing one does:

  metadata  refresh_opportunities_dry_run_<stamp>.json  PATCH each entry's `changes`
  reviews   review_check_dry_run_<stamp>.json           PATCH review_status/summary/sources
  deadline  deadline_check_dry_run_<stamp>.json         PATCH status/important_dates/...
  scraper   scrape_review_<mode>_<stamp>.json           INSERT new rows, is_active=false

<stamp> is `YYYYMMDD-HHMMSS` (agent_common.snapshot_stamp). It was date-only until
2026-08-22, which meant a second run on the same day silently overwrote the first one's
file — work that a dry run had already paid the API for in full. Both shapes are still
read; files written before that date keep their names and remain committable.

**The scraper's snapshot is the one to be careful with**: unlike the other three it is
written on live runs too, not just dry runs, so it can name rows that already exist. Every
insert is therefore deduped by normalized URL against the live table first — committing the
same scraper snapshot twice inserts nothing the second time.

Committed rows always land with `is_active = false`, exactly like a live scrape. Activating
them is a separate, deliberate step (see activate_opportunities in server.py) and is never
implied by a commit.
"""

import datetime
import fnmatch
import glob
import json
import os
import re

REPO_DIR = os.path.dirname(os.path.abspath(__file__))

# agent key -> how to find its snapshots and what committing one means.
#   glob:     filename pattern in the repo root
#   kind:     "patch" (update existing rows) or "insert" (new rows)
#   dry_only: False for the scraper, whose snapshot is written on live runs too
SNAPSHOT_SPECS = {
    "metadata": {
        "glob": "refresh_opportunities_dry_run_*.json",
        "kind": "patch",
        "dry_only": True,
        "label": "Metadata refresh",
    },
    "reviews": {
        "glob": "review_check_dry_run_*.json",
        "kind": "patch",
        "dry_only": True,
        "label": "Review check",
    },
    "deadline": {
        "glob": "deadline_check_dry_run_*.json",
        "kind": "patch",
        "dry_only": True,
        "label": "Deadline check",
    },
    "contact_email": {
        "glob": "find_contact_emails_dry_run_*.json",
        "kind": "patch",
        "dry_only": True,
        "label": "Contact email finder",
    },
    "scraper": {
        "glob": "scrape_review_*.json",
        "kind": "insert",
        "dry_only": False,
        "label": "New opportunities",
    },
}

STALE_DAYS = 7  # older than this and the underlying rows have probably moved on


def normalize_url(url):
    """Same normalization scrape_opportunities.py dedupes with — kept identical on purpose."""
    return (url or "").strip().rstrip("/").lower()


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# --------------------------------------------------------------------------- reading

def _load(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else []


def _pending_count(agent, entries):
    """How many entries would actually change something, as opposed to being no-ops.

    Worth separating from the raw entry count: a metadata snapshot records every row it
    looked at, including the ones the model returned no changes for, so "412 entries" and
    "6 rows would change" are very different numbers to put in front of somebody.
    """
    if agent in ("metadata", "contact_email"):
        return sum(1 for e in entries if isinstance(e, dict) and e.get("changes"))
    if agent == "reviews":
        return sum(1 for e in entries if isinstance(e, dict)
                   and e.get("review_status") != e.get("previous_review_status"))
    if agent == "deadline":
        return sum(1 for e in entries if isinstance(e, dict) and e.get("changed"))
    return sum(1 for e in entries if isinstance(e, dict) and e.get("url"))


def list_snapshots():
    """Every snapshot on disk, newest first, with enough detail to choose between them."""
    out = []
    for agent, spec in SNAPSHOT_SPECS.items():
        for path in glob.glob(os.path.join(REPO_DIR, spec["glob"])):
            name = os.path.basename(path)
            try:
                entries = _load(path)
            except Exception as e:
                out.append({"agent": agent, "file": name, "error": f"Unreadable: {e}",
                            "entries": 0, "pending": 0, "label": spec["label"]})
                continue
            mtime = datetime.datetime.fromtimestamp(os.path.getmtime(path),
                                                    datetime.timezone.utc)
            ran_at = _run_date(name) or mtime
            age_days = (datetime.datetime.now(datetime.timezone.utc) - ran_at).days
            out.append({
                "agent": agent,
                "label": spec["label"],
                "kind": spec["kind"],
                "file": name,
                "modified_at": mtime.isoformat(),
                "run_date": ran_at.date().isoformat(),
                # Full instant when the filename carries one, so two snapshots from the
                # same day are distinguishable in the console rather than looking identical.
                "ran_at": ran_at.isoformat(),
                "has_time": bool(re.search(r"\d{8}-\d{6}", name)),
                "age_days": age_days,
                "stale": age_days >= STALE_DAYS,
                "entries": len(entries),
                "pending": _pending_count(agent, entries),
                "dry_only": spec["dry_only"],
                "mode": _scraper_mode(name) if agent == "scraper" else None,
            })
    # By full instant, not date — otherwise same-day snapshots order by filename, which
    # sorts the agents alphabetically rather than putting the newest run first.
    out.sort(key=lambda s: (s.get("ran_at") or "", s.get("file")), reverse=True)
    return out


def _scraper_mode(filename):
    m = re.match(r"scrape_review_([a-z]+)_", filename)
    return m.group(1) if m else None


def _run_date(filename):
    """When the run that produced this file happened, from its filename stamp.

    Preferred over the file's mtime for judging staleness: mtime is whenever the file last
    touched this disk (a fresh git checkout rewrites every one of them to "today"), while
    the stamp is when the run that produced it actually happened.

    Two shapes are read, because both exist on disk. Snapshots written from 2026-08-22 on
    carry `YYYYMMDD-HHMMSS` (see agent_common.snapshot_stamp); everything written before
    that carries the date alone, and those files are still committable. A date-only name
    resolves to midnight — the same instant the old code returned — so nothing about
    staleness or ordering changes for them.
    """
    m = re.search(r"(\d{8})(?:-(\d{6}))?", filename)
    if not m:
        return None
    try:
        stamp = m.group(1) + (m.group(2) or "")
        fmt = "%Y%m%d%H%M%S" if m.group(2) else "%Y%m%d"
        # Parsed as UTC to keep every snapshot comparable, even though the writer stamps
        # local time. The gap only matters for sub-day ordering of files from two different
        # machines, which is not a case this repo has.
        return datetime.datetime.strptime(stamp, fmt).replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        return None


def resolve(file_name):
    """Map a bare snapshot filename back to (agent, absolute path).

    Rejects anything that isn't a plain basename matching a known pattern — this is reached
    from an HTTP handler, and the value names a file to open.
    """
    # Reject anything with a path separator before touching the filesystem: only a bare
    # basename matching one of the known globs can name a snapshot.
    if not file_name or os.path.basename(file_name) != file_name:
        return None, None
    for agent, spec in SNAPSHOT_SPECS.items():
        if not fnmatch.fnmatch(file_name, spec["glob"]):
            continue
        path = os.path.join(REPO_DIR, file_name)
        if os.path.isfile(path):
            return agent, path
    return None, None


# --------------------------------------------------------------------------- writing

def _patch_updates(agent, entry):
    """The exact column set the live (non-dry) branch of that agent would have written.

    Kept deliberately parallel to those branches — if an agent's live PATCH changes, this
    has to change with it or a committed snapshot will write a different shape than a live
    run of the same agent.
    """
    now = _now_iso()
    if agent in ("metadata", "contact_email"):
        changes = entry.get("changes") or {}
        if not changes:
            return None
        return {**changes, "updated_at": now}
    if agent == "reviews":
        if entry.get("review_status") == entry.get("previous_review_status"):
            return None
        return {
            "review_status": entry.get("review_status"),
            "review_summary": entry.get("review_summary"),
            "review_sources": entry.get("review_sources") or [],
            "last_reviewed_at": now,
            "updated_at": now,
        }
    if agent == "deadline":
        if not entry.get("changed"):
            return None
        return {
            "status": entry.get("status"),
            "important_dates": entry.get("important_dates") or [],
            "was_estimated": bool(entry.get("was_estimated")),
            "important_date_note": entry.get("important_date_note"),
            "dates_last_checked_at": now,
            "updated_at": now,
        }
    return None


def commit_snapshot(file_name, patch_fn, insert_fn, existing_urls_fn, dry=False):
    """Apply one snapshot. Returns a result dict describing exactly what happened.

    The three callables are injected rather than imported so this module stays free of
    server.py's Supabase plumbing (and so the whole thing is testable without a database):
      patch_fn(opp_id, updates)  -> None, raises on failure
      insert_fn(rows)            -> None, raises on failure
      existing_urls_fn()         -> set of normalized URLs already in `opportunities`

    `dry=True` resolves and counts everything without writing — the preview the console
    shows before the operator confirms.
    """
    agent, path = resolve(file_name)
    if not agent:
        return {"ok": False, "error": f"Not a recognised snapshot file: {file_name}"}
    try:
        entries = _load(path)
    except Exception as e:
        return {"ok": False, "error": f"Could not read {file_name}: {e}"}

    spec = SNAPSHOT_SPECS[agent]
    result = {"ok": True, "agent": agent, "label": spec["label"], "file": file_name,
              "kind": spec["kind"], "dry": dry, "entries": len(entries),
              "applied": 0, "skipped_no_change": 0, "skipped_duplicate": 0,
              "errors": 0, "error_details": []}

    if spec["kind"] == "insert":
        existing = existing_urls_fn()
        seen = set()
        rows = []
        for e in entries:
            if not isinstance(e, dict) or not e.get("url"):
                result["skipped_no_change"] += 1
                continue
            key = normalize_url(e.get("url"))
            # Dedupe against the live table AND within the snapshot itself. The scraper
            # writes this file on live runs too, so re-committing one must be a no-op.
            if key in existing or key in seen:
                result["skipped_duplicate"] += 1
                continue
            seen.add(key)
            # Never trust a snapshot to decide visibility: new rows are always inactive
            # and go through the activation queue like any other scraped row.
            rows.append({**e, "is_active": False})
        result["would_insert"] = len(rows)
        if not dry and rows:
            try:
                insert_fn(rows)
                result["applied"] = len(rows)
            except Exception as ex:
                result["errors"] += 1
                result["error_details"].append(str(ex)[:200])
                result["ok"] = False
        elif dry:
            result["applied"] = len(rows)
        return result

    for e in entries:
        if not isinstance(e, dict) or not e.get("id"):
            result["skipped_no_change"] += 1
            continue
        updates = _patch_updates(agent, e)
        if not updates:
            result["skipped_no_change"] += 1
            continue
        if dry:
            result["applied"] += 1
            continue
        try:
            patch_fn(e["id"], updates)
            result["applied"] += 1
        except Exception as ex:
            result["errors"] += 1
            if len(result["error_details"]) < 5:
                result["error_details"].append(f"{e['id']}: {str(ex)[:160]}")
    return result

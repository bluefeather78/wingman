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

PHASE 4 — SNAPSHOTS ARE MIRRORED TO `agent_snapshots` so they are not stranded on one laptop
(agents_report 4.20). A snapshot is money already spent: a dry run pays the API in full and
writes nothing, and this module is how that money is redeemed. Leaving the only copy in a
gitignored file on one machine meant a second checkout could not redeem any of it.

The FILE stays the format and stays what the agents write — no agent changed. The table is a
mirror: publish_snapshot() uploads one, and resolve() materialises a remote snapshot back to a
byte-identical local file before returning its path. Everything downstream — the URL dedupe
key, the run time read off the FILENAME rather than `now`, the STALE_DAYS refusal, the two
families registered as not-committable — is therefore untouched, which is deliberate: Phase 3
item 4.5 is precisely about a commit writing something different from what the live run would
have written, and a mirror that re-derived anything would reintroduce it.
"""

import datetime
import fnmatch
import glob
import json
import os
import re
import socket
import time
from wingman import url_dedupe
from wingman import REPO_ROOT   # the repo root, defined once (see wingman/__init__.py)

REPO_DIR = REPO_ROOT

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
    "action_items": {
        "glob": "action_items_dry_run_*.json",
        "kind": "patch",
        "dry_only": True,
        "label": "Action items",
    },
    # The two other INSERTING agents. Their snapshots are the same
    # {"inserted": [...], "rejected": [...]} shape the scraper writes (all three call
    # build_row and insert_rows), so they commit through the identical path — they were
    # simply never registered, which is why a hub-mining or name-harvest dry run produced a
    # file nothing could apply.
    "hub_miner": {
        "glob": "hub_review_*.json",
        "kind": "insert",
        "dry_only": False,
        "label": "Hub-mined opportunities",
    },
    "name_harvester": {
        "glob": "names_review_*.json",
        "kind": "insert",
        "dry_only": False,
        "label": "Name-harvested opportunities",
    },
    # LISTED BUT NOT COMMITTABLE, each for its own reason. They are registered so the console
    # shows them (an unregistered family is invisible, which is how these two were lost), and
    # commit_snapshot refuses them with the reason rather than silently doing nothing.
    "links": {
        "glob": "link_check_dry_run_*.json",
        "kind": "none",
        "dry_only": True,
        "label": "Link check",
        # build_update() derives link_status / link_dead_since / link_review_status /
        # quality_flags from the LIVE row as well as the check result: "first seen dead wins",
        # a flag merge, and a review status that must never overturn a human verdict. A
        # snapshot carries none of that, so replaying one would write a DIFFERENT result than
        # the live run did — the exact defect this finding is about. And it is the one agent
        # that costs nothing to run, so re-running it is strictly better than replaying it.
        "not_committable": ("re-run the agent instead — it is free, and a replay would write "
                            "stale link-health state derived from the row as it was then"),
    },
    "mailing_list": {
        "glob": "mailing_list_dry_run_*.json",
        "kind": "none",
        "dry_only": True,
        "label": "Mailing lists",
        # Writes `opportunity_signups`, not `opportunities`. commit_snapshot's injected
        # patch_fn is bound to the catalog table, so committing this would need a second
        # writer. Listed so it is visible; not committable until that exists.
        "not_committable": ("writes opportunity_signups, not opportunities — commit_snapshot "
                            "has no writer for that table"),
    },
}

STALE_DAYS = 7  # older than this and the underlying rows have probably moved on


def normalize_url(url):
    """The dedupe key for a snapshot commit — url_dedupe.match_key, the SAME key the scraper's
    own insert path uses (audit finding 4.5).

    The docstring here used to say "same normalization scrape_opportunities dedupes with, kept
    identical on purpose", and it had stopped being true: the scraper dedupes through
    url_dedupe.find_duplicates (hence match_key), while this stayed on strip/rstrip/lower.
    match_key additionally collapses the scheme, `www.`, default ports, index files, the
    fragment and tracking params — so `http://www.x.org/program/index.html?utm_source=x` and
    `https://x.org/program` are one row to the scraper and were TWO to a commit. Committing a
    snapshot could therefore insert a duplicate of a row the scraper had already decided
    against, which is exactly what "a snapshot commit inserts 0 dupes" has to rule out.

    Kept as a named wrapper rather than a raw call because ops/core.py builds the existing-URL
    set through this same function, and both sides must key identically or the comparison is
    meaningless.
    """
    return url_dedupe.match_key(url or "")


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# --------------------------------------------------------------------------- reading

def _load(path):
    """The committable entries in a snapshot file. Two shapes exist and BOTH are read.

    Snapshots written before 2026-08-23 are a bare JSON list. From that date
    agents/scrape_opportunities.py writes {"inserted": [...], "rejected": [...]} instead, so that
    every candidate it declined is preserved with the reason — previously they vanished and
    a run's discards could not be audited. Only `inserted` is committable; `rejected` rows
    were deliberately not written to the catalog.

    Returning [] for an unrecognised shape (the previous behaviour for any non-list) is a
    silent failure mode: a snapshot would list as "0 entries" and commit nothing, with no
    error to explain why. Anything dict-shaped without `inserted` therefore still yields the
    empty list, but the two known shapes must both keep working.
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        entries = data.get("inserted")
        return entries if isinstance(entries, list) else []
    return []


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


# --------------------------------------------------------------------------- the mirror
#
# PHASE 4 (agents_report 4.20). Snapshots are money already spent — a dry run pays the API in
# full — and they lived in gitignored files on one machine with no copy. `agent_snapshots`
# mirrors them so a second checkout can list and commit them.
#
# STRICTLY ADDITIVE. Local files are still the primary source and still work with no table at
# all; nothing here can make a local snapshot invisible or uncommittable.
SNAPSHOT_TABLE = "agent_snapshots"

# How often a process re-uploads whatever is on disk but not yet in the table. list_snapshots()
# is called on every console poll, so an unthrottled sync would re-read every snapshot file
# from disk several times a minute.
SYNC_INTERVAL_SECONDS = 300

# How long the remote LISTING is reused. Separate from, and much shorter than, the upload
# throttle: the console polls list_snapshots() every few seconds, so an uncached remote read
# would be ~20 Supabase round trips a minute to answer a question whose answer changes when
# somebody finishes an agent run. 30s means another machine's snapshot appears within half a
# minute while the poll costs nothing.
REMOTE_CACHE_TTL = 30

_mirror_available = True
_mirror_warned = False
_last_sync = 0.0
_remote_cache = {"at": 0.0, "rows": []}
_MISSING_CODES = ("PGRST205", "42P01", "42703", "PGRST204")


def _mirror_creds():
    """(url, service_key) or (None, None). Soft: no Supabase means local-only, not an error."""
    from wingman import supabase_common
    supabase_common.load_dotenv()
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    return (url, key) if url and key else (None, None)


def _mirror_on():
    return _mirror_available and all(_mirror_creds())


def _mirror_off(reason):
    global _mirror_available, _mirror_warned
    _mirror_available = False
    if not _mirror_warned:
        _mirror_warned = True
        print(f"[WARN] agent_snapshots unavailable ({reason}) — dry-run snapshots are LOCAL "
              f"FILES only, so another machine cannot commit them. Run "
              f"db/agent_snapshots_schema.sql in the Supabase SQL editor.")


def _mirror_missing(exc):
    try:
        body = exc.read().decode("utf-8", "replace")
    except Exception:                                                 # noqa: BLE001
        body = str(exc)
    return any(code in body for code in _MISSING_CODES)


def _remote_snapshots(fresh=False):
    """Rows in the mirror, or [] when there is no mirror. Cached, and never raises.

    Deliberately does NOT select `payload`: the whole point of listing separately from fetching
    is that a console poll must not download every snapshot that exists.
    """
    if not _mirror_on():
        return []
    now = time.time()
    if not fresh and now - _remote_cache["at"] < REMOTE_CACHE_TTL:
        return _remote_cache["rows"]
    from wingman import supabase_common
    url, key = _mirror_creds()
    try:
        rows = supabase_common.supabase_get(
            url, SNAPSHOT_TABLE, {"select": "file,agent,ran_at,host,created_at"}, key)
    except Exception as e:                                            # noqa: BLE001
        if _mirror_missing(e):
            _mirror_off(str(e)[:80])
        else:
            print(f"[WARN] could not list remote snapshots: {e}")
        return []
    _remote_cache["at"] = now
    _remote_cache["rows"] = rows
    return rows


def publish_snapshot(file_name):
    """Upload one local snapshot to the mirror. True if it is now there.

    Idempotent on `file`, which is a snapshot's real identity — it carries the agent and the
    run stamp, and agent_common.snapshot_stamp has included seconds since 2026-08-22 precisely
    so two runs on the same day cannot collide.
    """
    if not _mirror_on():
        return False
    agent, path = _resolve_local(file_name)
    if not agent:
        return False
    from wingman import supabase_common
    url, key = _mirror_creds()
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as e:                                            # noqa: BLE001
        print(f"[WARN] could not read {file_name} to publish it: {e}")
        return False
    ran_at = _run_date(file_name)
    row = {
        "file": file_name,
        "agent": agent,
        "ran_at": ran_at.isoformat() if ran_at else None,
        "payload": payload,
        "host": socket.gethostname(),
    }
    try:
        supabase_common.supabase_post(url, SNAPSHOT_TABLE, [row], key, on_conflict="file")
        return True
    except Exception as e:                                            # noqa: BLE001
        if _mirror_missing(e):
            _mirror_off(str(e)[:80])
        else:
            print(f"[WARN] could not publish {file_name}: {e}")
        return False


def fetch_snapshot(file_name):
    """Write a mirrored snapshot to its local path. Returns the path, or None.

    Byte-for-byte is not attempted and is not needed — what must survive is the JSON and the
    FILENAME, because the filename is where the run's timestamp comes from (audit 4.5) and the
    JSON is what _load() reads. Refuses to overwrite an existing local file: the local copy is
    the original, and clobbering it with a re-serialised one would be a silent edit to
    evidence.
    """
    if not _mirror_on() or not file_name or os.path.basename(file_name) != file_name:
        return None
    if not any(fnmatch.fnmatch(file_name, spec["glob"]) for spec in SNAPSHOT_SPECS.values()):
        return None
    path = os.path.join(REPO_DIR, file_name)
    if os.path.isfile(path):
        return path
    from wingman import supabase_common
    url, key = _mirror_creds()
    try:
        rows = supabase_common.supabase_get(
            url, SNAPSHOT_TABLE, {"select": "payload", "file": f"eq.{file_name}"}, key)
    except Exception as e:                                            # noqa: BLE001
        if _mirror_missing(e):
            _mirror_off(str(e)[:80])
        else:
            print(f"[WARN] could not fetch {file_name}: {e}")
        return None
    if not rows:
        return None
    tmp = path + ".part"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rows[0].get("payload"), f, ensure_ascii=False)
        # Rename last, so an interrupted download cannot leave a half-written file that
        # list_snapshots would then report as "Unreadable" — or worse, that _load() would
        # read as a short snapshot and commit.
        os.replace(tmp, path)
    except Exception as e:                                            # noqa: BLE001
        print(f"[WARN] could not write {file_name}: {e}")
        try:
            os.remove(tmp)
        except OSError:
            pass
        return None
    return path


def sync_snapshots(force=False):
    """Publish any local snapshot the mirror does not have yet. Returns how many were sent.

    Throttled, because list_snapshots() is called on every console poll and this reads every
    snapshot file off disk. Best-effort throughout: a mirror that cannot be reached must never
    stop an operator committing a snapshot that is sitting right there on their own disk.
    """
    global _last_sync
    if not _mirror_on():
        return 0
    now = time.time()
    if not force and now - _last_sync < SYNC_INTERVAL_SECONDS:
        return 0
    _last_sync = now
    remote = {r.get("file") for r in _remote_snapshots(fresh=True)}
    sent = 0
    for agent, spec in SNAPSHOT_SPECS.items():
        for path in glob.glob(os.path.join(REPO_DIR, spec["glob"])):
            name = os.path.basename(path)
            if name in remote:
                continue
            if publish_snapshot(name):
                sent += 1
    if sent:
        _remote_cache["at"] = 0.0        # what was just published must show up immediately
    return sent


def mirror_backend():
    """"supabase" or "local" — whether snapshots are shared right now. The console prints it."""
    return "supabase" if _mirror_on() else "local"


def _reset_mirror_for_tests():
    global _mirror_available, _mirror_warned, _last_sync
    _mirror_available = True
    _mirror_warned = False
    _last_sync = 0.0
    _remote_cache["at"] = 0.0
    _remote_cache["rows"] = []


def list_snapshots():
    """Every snapshot available, newest first, with enough detail to choose between them.

    "Available" is local files PLUS anything another machine published to the mirror. A remote
    one carries `remote: True` and is fetched on demand when it is actually committed — listing
    must not download every snapshot on every console poll.
    """
    sync_snapshots()
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
    # Snapshots another machine published and this one has never had. Entry/pending counts
    # need the payload, which is deliberately NOT downloaded here — listing must not pull every
    # snapshot on every console poll — so they are reported as None rather than as 0, which
    # would read as "this snapshot is empty".
    local = {s["file"] for s in out}
    for row in _remote_snapshots():
        name = row.get("file")
        agent = row.get("agent")
        if not name or name in local or agent not in SNAPSHOT_SPECS:
            continue
        spec = SNAPSHOT_SPECS[agent]
        ran_at = _run_date(name) or _parse_iso(row.get("ran_at"))
        age_days = ((datetime.datetime.now(datetime.timezone.utc) - ran_at).days
                    if ran_at else 0)
        out.append({
            "agent": agent,
            "label": spec["label"],
            "kind": spec["kind"],
            "file": name,
            "modified_at": row.get("created_at"),
            "run_date": ran_at.date().isoformat() if ran_at else None,
            "ran_at": ran_at.isoformat() if ran_at else None,
            "has_time": bool(re.search(r"\d{8}-\d{6}", name)),
            "age_days": age_days,
            "stale": age_days >= STALE_DAYS,
            "entries": None,
            "pending": None,
            "dry_only": spec["dry_only"],
            "mode": _scraper_mode(name) if agent == "scraper" else None,
            "remote": True,
            "host": row.get("host"),
        })
    # By full instant, not date — otherwise same-day snapshots order by filename, which
    # sorts the agents alphabetically rather than putting the newest run first.
    out.sort(key=lambda s: (s.get("ran_at") or "", s.get("file")), reverse=True)
    return out


def _parse_iso(value):
    if not value:
        return None
    try:
        when = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=datetime.timezone.utc)


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


def _resolve_local(file_name):
    """Map a bare snapshot filename back to (agent, absolute path), local files only.

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


def resolve(file_name):
    """(agent, absolute path) for a snapshot, materialising a mirrored one if it is not local.

    The local file always wins. Only when there is no local copy is the mirror consulted, and
    then the snapshot is written to its own filename first — so everything downstream sees an
    ordinary local snapshot and behaves identically. That is the point: the run-time stamp
    comes from the FILENAME (audit 4.5), the STALE_DAYS refusal is computed from it, and the
    commit path must write exactly what the live run would have.
    """
    agent, path = _resolve_local(file_name)
    if agent:
        return agent, path
    if not fetch_snapshot(file_name):
        return None, None
    return _resolve_local(file_name)


# --------------------------------------------------------------------------- writing

def _patch_updates(agent, entry, checked_at=None):
    """The exact column set the live (non-dry) branch of that agent would have written.

    Kept deliberately parallel to those branches — if an agent's live PATCH changes, this
    has to change with it or a committed snapshot will write a different shape than a live
    run of the same agent.

    `checked_at` IS THE FRESHNESS STAMP, AND IT IS THE RUN'S TIME, NOT NOW (audit 4.5).
    `dates_last_checked_at` and `last_reviewed_at` are staleness clocks: the deadline one
    suppresses any re-check for 7 days, the review one for 30. Stamping them with `now` on
    commit asserted that a check made days ago had just happened, re-arming the whole TTL on
    stale data and overwriting any interactive check made in between. The correct value is
    when the check ACTUALLY ran, which is what the snapshot's filename stamp records — so a
    day-old snapshot's rows come due a day sooner, exactly as they should.

    `updated_at` still moves to now: that is a row-touched timestamp, not a freshness clock,
    and the row really is being touched now.
    """
    now = _now_iso()
    checked = checked_at or now
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
            "last_reviewed_at": checked,   # when the check ran, not now — see the docstring
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
            "dates_last_checked_at": checked,   # when the check ran, not now
            "updated_at": now,
        }
    if agent == "action_items":
        # Mirrors generate_action_items.main()'s live PATCH exactly: the items, their source,
        # and the checked-at stamp ONLY when that run decided to stamp (decision.stamp, which
        # the snapshot records as `would_stamp`). A generic, unverified answer deliberately
        # does not stamp, so the row stays due — replaying one that stamps anyway would cache
        # the weakest possible answer for a full cycle.
        if not entry.get("action_items"):
            return None
        patch = {
            "action_items": entry.get("action_items"),
            "action_items_source": entry.get("action_items_source"),
            "updated_at": now,
        }
        if entry.get("would_stamp"):
            patch["action_items_checked_at"] = checked
        return patch
    return None


def commit_snapshot(file_name, patch_fn, insert_fn, existing_urls_fn, dry=False,
                    allow_stale=False):
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

    spec = SNAPSHOT_SPECS[agent]
    # Registered so the console can LIST it, but there is a reason it cannot be applied.
    # Refusing with that reason beats the old behaviour for these two, which was to be
    # invisible entirely (unregistered globs resolved to "not a recognised snapshot file").
    if spec.get("not_committable"):
        return {"ok": False, "agent": agent, "label": spec["label"], "file": file_name,
                "error": f"{spec['label']} snapshots cannot be committed: "
                         f"{spec['not_committable']}."}

    try:
        entries = _load(path)
    except Exception as e:
        return {"ok": False, "error": f"Could not read {file_name}: {e}"}

    # The freshness stamp a patch will write: when the run ACTUALLY happened, read off the
    # filename, never `now` (audit 4.5). Falls back to now only for a name carrying no stamp,
    # which no agent has written since 2026-08-22.
    run_at = _run_date(file_name)
    checked_at = run_at.isoformat() if run_at else _now_iso()
    age_days = ((datetime.datetime.now(datetime.timezone.utc) - run_at).days
                if run_at else 0)
    result = {"ok": True, "agent": agent, "label": spec["label"], "file": file_name,
              "kind": spec["kind"], "dry": dry, "entries": len(entries),
              "applied": 0, "skipped_no_change": 0, "skipped_duplicate": 0,
              "errors": 0, "error_details": [],
              "checked_at": checked_at, "age_days": age_days, "stale": age_days >= STALE_DAYS}

    # STALE_DAYS used to be display-only: list_snapshots() showed a "stale" badge and
    # commit_snapshot applied a month-old file exactly as readily as a fresh one. A patch
    # snapshot replays field values that were true when the run happened, so an old one
    # overwrites whatever has been learned since. Inserts are exempt — a new row is a new row
    # however old the file is, and the URL dedupe already suppresses anything since added.
    if spec["kind"] == "patch" and age_days >= STALE_DAYS and not allow_stale:
        result.update({"ok": False, "error": (
            f"This snapshot is {age_days} days old (limit {STALE_DAYS}). Its values were true "
            f"when the run happened and may have been superseded since — re-run the agent, or "
            f"commit again with allow_stale to apply it anyway.")})
        return result

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
        updates = _patch_updates(agent, e, checked_at)
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

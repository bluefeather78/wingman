"""The cross-process mutex that stops two catalog agents running at once, plus id minting.

Audit finding 4.2 / PRODUCTION_READINESS_PLAN.md Phase 3. Four agents INSERT into
`opportunities` — scrape_opportunities, mine_hub_pages, harvest_names, refind_dead_links —
and each mints ids as `ec<max+1>` from a snapshot taken at run start. Two overlapping runs
mint the SAME ids and the second POST dies on the primary key.

Nothing stopped the overlap. `gemini_common`'s `.gemini_web_search.lock` is (a) local to one
machine and (b) taken at the first SEARCH call, so mine_hub_pages — which never searches —
could run alongside the scraper without either noticing. `ops/core.running_gemini_search_agent`
only refuses CONSOLE launches of agents flagged `uses_gemini_search`; a hand-run
`python -m agents.mine_hub_pages` was never checked at all.

TWO BACKENDS, and which one you get is not a detail:

  DB (db/agent_locks_schema.sql applied)  the real thing. Acquisition is an INSERT against a
      primary key, so the loser is rejected by Postgres with no read-then-write window, and
      the lock is visible to every checkout and every machine.

  FILE (that table missing)  a local fallback, warned about loudly on every acquisition. It
      protects ONE machine. Kept because a fresh checkout must still be able to run an agent
      before somebody has opened the Supabase SQL editor, and because even one-machine
      mutual exclusion is strictly more than the pipeline had before Phase 3.

WHY NOT JUST FIX THE MINTER. Because the collision is the symptom. Two scrapers running
together also double-charge the same seeds, race `record_seed_result`'s counters (4.12), and
page the same table one of them is concurrently PATCHing (4.14). `mint_ids` does use the
`next_opportunity_id` sequence when it exists — nextval is atomic and never rolls back, so
ids stay unique even if a lock is somehow bypassed — but the lock is the actual fix.
"""
import datetime
import json
import os
import re
import secrets
import socket
import sys
import threading
import urllib.error

from wingman import REPO_ROOT
from wingman.supabase_common import (supabase_delete, supabase_get, supabase_post,
                                     supabase_patch, supabase_rpc)

LOCK_TABLE = "agent_locks"

# The one resource every inserting agent contends for: the right to mint ids and write rows
# into `opportunities`. A single name (rather than one per agent) is the point — the agents
# collide with EACH OTHER, not only with themselves.
CATALOG_INSERT = "catalog_insert"

# How long a lock stays valid without a heartbeat. Long enough that a slow model call cannot
# lose it, short enough that a crashed run does not block the console until someone notices.
# The scraper's own --timeout defaults to 280s, so a run makes progress well inside this.
LEASE_SECONDS = 900

# Heartbeat cadence. A third of the lease: two consecutive misses still leave a margin.
HEARTBEAT_SECONDS = 300

_MISSING_TABLE_CODES = ("42P01", "PGRST205", "42703", "PGRST204")
_UNIQUE_VIOLATION_CODES = ("23505",)


class LockBusy(RuntimeError):
    """Another run holds the lock. Carries the holder string for the operator's message."""

    def __init__(self, name, holder, expires_at=None):
        self.name = name
        self.holder = holder
        self.expires_at = expires_at
        super().__init__(
            f"another run holds the '{name}' lock: {holder}"
            + (f" (lease expires {expires_at})" if expires_at else ""))


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(dt):
    return dt.isoformat()


def _error_code(exc):
    if not isinstance(exc, urllib.error.HTTPError):
        return None
    try:
        return (json.loads(exc.read().decode("utf-8", errors="replace")) or {}).get("code")
    except Exception:
        return None


def describe_holder(agent):
    """The string an operator reads when their launch is refused. Names the machine on
    purpose — with a DB lock the blocking run may not be on the box they are sitting at."""
    return f"{agent} pid {os.getpid()} on {socket.gethostname()}"


def _pid_is_alive(pid):
    """Best-effort liveness check for a PID recorded in a lock. Stdlib-only, POSIX + Windows,
    and AMBIGUITY DEFAULTS TO ALIVE — a false "dead" verdict would let two inserting agents
    race, which is the exact thing this lock exists to prevent. Same rule and same shape as
    gemini_common._pid_is_alive, deliberately: two liveness checks that disagree would be
    worse than one that is merely conservative.
    """
    try:
        if sys.platform == "win32":
            import subprocess
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                                 capture_output=True, text=True, timeout=5)
            return str(pid) in out.stdout
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except Exception:
        return True      # unknown state -> assume alive, fail safe


def _holder_is_dead_here(holder):
    """True only when `holder` names a pid on THIS machine that is definitely gone.

    A lease exists so a crashed run cannot wedge the pipeline — but 900 seconds is a long time
    to stare at a lock left by a process you just watched die. When the holder is on this host
    and its pid is gone, the lock is provably stale and can be taken over at once. A holder on
    ANOTHER machine is never judged this way: its pid means nothing here, so it waits for the
    lease, which is what the lease is for.
    """
    if not holder:
        return False
    m = re.search(r"pid (\d+) on (.+)$", str(holder))
    if not m:
        return False
    pid, host = m.group(1), m.group(2).strip()
    if host != socket.gethostname():
        return False
    return not _pid_is_alive(pid)


def _file_lock_path(name):
    return os.path.join(REPO_ROOT, f".agent_{name}.lock")


# --------------------------------------------------------------------------------------
# The file fallback
# --------------------------------------------------------------------------------------

def _file_acquire(name, holder):
    """O_EXCL create — atomic on every platform this runs on. Steals an expired lease."""
    path = _file_lock_path(name)
    payload = json.dumps({"holder": holder, "expires_at": _iso(
        _now() + datetime.timedelta(seconds=LEASE_SECONDS))})
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        existing = _file_read(path)
        if (not _expired(existing.get("expires_at"))
                and not _holder_is_dead_here(existing.get("holder"))):
            raise LockBusy(name, existing.get("holder") or "an earlier run",
                           existing.get("expires_at"))
        # Expired: nobody is heartbeating it, so taking it over is safe.
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        return _file_acquire(name, holder)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(payload)
    return "file"


def _file_read(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        # An unreadable/empty lock file is a crashed run mid-write, not a live holder.
        return {}


def _file_heartbeat(name, _token):
    path = _file_lock_path(name)
    data = _file_read(path)
    data["expires_at"] = _iso(_now() + datetime.timedelta(seconds=LEASE_SECONDS))
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass


def _file_release(name, _token):
    try:
        os.unlink(_file_lock_path(name))
    except FileNotFoundError:
        pass


def _expired(expires_at):
    if not expires_at:
        return True
    try:
        dt = datetime.datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
    except ValueError:
        return True
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt <= _now()


# --------------------------------------------------------------------------------------
# The DB backend
# --------------------------------------------------------------------------------------

def _db_current(url, key, name):
    rows = supabase_get(url, LOCK_TABLE, {"select": "name,holder,expires_at",
                                          "name": f"eq.{name}"}, key)
    return (rows or [None])[0]


def _db_acquire(url, key, name, holder, token):
    row = {"name": name, "holder": holder, "token": token,
           "acquired_at": _iso(_now()),
           "expires_at": _iso(_now() + datetime.timedelta(seconds=LEASE_SECONDS))}
    try:
        supabase_post(url, LOCK_TABLE, [row], key)
        return "db"
    except Exception as e:
        code = _error_code(e)
        if code in _MISSING_TABLE_CODES:
            raise _TableMissing()
        if code not in _UNIQUE_VIOLATION_CODES:
            raise
    # Someone holds it. Only an EXPIRED lease may be taken over.
    current = _db_current(url, key, name)
    if (current and not _expired(current.get("expires_at"))
            and not _holder_is_dead_here(current.get("holder"))):
        raise LockBusy(name, current.get("holder") or "an earlier run",
                       current.get("expires_at"))
    # Delete guarded on the expiry we just read, so we cannot stomp a lock that was
    # heartbeated between the read and this delete.
    if _holder_is_dead_here((current or {}).get("holder")):
        # Provably dead on this host: delete by name, since its lease has not expired.
        supabase_delete(url, LOCK_TABLE, {"name": f"eq.{name}"}, key)
    else:
        supabase_delete(url, LOCK_TABLE, {
            "name": f"eq.{name}",
            "expires_at": f"lt.{_iso(_now())}"}, key)
    try:
        supabase_post(url, LOCK_TABLE, [row], key)
        return "db"
    except Exception as e:
        if _error_code(e) in _UNIQUE_VIOLATION_CODES:
            current = _db_current(url, key, name)
            raise LockBusy(name, (current or {}).get("holder") or "an earlier run",
                           (current or {}).get("expires_at"))
        raise


class _TableMissing(Exception):
    """db/agent_locks_schema.sql has not been run in this project."""


def _db_heartbeat(url, key, name, token):
    try:
        supabase_patch(url, LOCK_TABLE, {"name": f"eq.{name}", "token": f"eq.{token}"},
                       {"expires_at": _iso(_now() + datetime.timedelta(seconds=LEASE_SECONDS))},
                       key)
    except Exception:
        # A missed heartbeat is not fatal on its own — the lease still has two thirds of its
        # life left. Losing the lock entirely surfaces at release/insert time.
        pass


def _db_release(url, key, name, token):
    # Guarded on the token: a run that lost its lease to a takeover must not delete the lock
    # its successor now holds.
    try:
        supabase_delete(url, LOCK_TABLE, {"name": f"eq.{name}", "token": f"eq.{token}"}, key)
    except Exception:
        pass


# --------------------------------------------------------------------------------------
# The public API
# --------------------------------------------------------------------------------------

class RunLock:
    """Context manager. Raises LockBusy if another run holds `name`.

        with RunLock(CATALOG_INSERT, "scraper", url, key):
            ...

    `backend` reads 'db' or 'file' once acquired, so a caller can say which one protected it.
    """

    def __init__(self, name, agent, supabase_url=None, service_key=None, enabled=True):
        self.name = name
        self.agent = agent
        self.url = supabase_url
        self.key = service_key
        self.enabled = enabled
        self.token = secrets.token_hex(16)
        self.holder = describe_holder(agent)
        self.backend = None
        self._timer = None
        self._stop = threading.Event()

    def acquire(self):
        if not self.enabled:
            self.backend = "disabled"
            return self
        if self.url and self.key:
            try:
                self.backend = _db_acquire(self.url, self.key, self.name, self.holder,
                                           self.token)
            except _TableMissing:
                print(f"[WARN] {LOCK_TABLE} not found — falling back to a LOCAL file lock. "
                      f"Run db/agent_locks_schema.sql so a run on another machine or checkout "
                      f"is also excluded.")
                self.backend = _file_acquire(self.name, self.holder)
        else:
            self.backend = _file_acquire(self.name, self.holder)
        self._start_heartbeat()
        return self

    def _start_heartbeat(self):
        def beat():
            while not self._stop.wait(HEARTBEAT_SECONDS):
                if self.backend == "db":
                    _db_heartbeat(self.url, self.key, self.name, self.token)
                elif self.backend == "file":
                    _file_heartbeat(self.name, self.token)

        self._timer = threading.Thread(target=beat, daemon=True, name=f"runlock-{self.name}")
        self._timer.start()

    def release(self):
        self._stop.set()
        if self.backend == "db":
            _db_release(self.url, self.key, self.name, self.token)
        elif self.backend == "file":
            _file_release(self.name, self.token)
        self.backend = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()
        return False


def current_holder(supabase_url=None, service_key=None, name=CATALOG_INSERT):
    """Who holds `name` right now, or None. For the console's pre-launch check — read-only,
    never acquires.

    THE DB READ AND THE FILE READ ARE SEPARATE ATTEMPTS, and that separation is the whole
    correctness of this function. A single try/except around both meant that when
    db/agent_locks_schema.sql had not been run — which is the state of every checkout until
    somebody opens the Supabase SQL editor — the 42P01 from the DB read jumped straight past
    the file fallback to `return None`, and the console reported the lock free while a run was
    holding the file lock a metre away. Caught by a live smoke test, not by the unit tests,
    which all mocked the table as present.

    Still returns None on an unexpected error: a status probe must never be the thing that
    stops a run. But "the DB has no lock table" is not unexpected, and it is not "no holder".
    """
    if supabase_url and service_key:
        try:
            row = _db_current(supabase_url, service_key, name)
            if row:
                if _expired(row.get("expires_at")) or _holder_is_dead_here(row.get("holder")):
                    return None
                return row.get("holder")
            return None      # table exists and holds no row: authoritative, nobody holds it
        except Exception:
            pass             # missing table / unreachable — fall through to the file lock
    try:
        data = _file_read(_file_lock_path(name))
        if (data and not _expired(data.get("expires_at"))
                and not _holder_is_dead_here(data.get("holder"))):
            return data.get("holder")
    except Exception:
        return None
    return None


def mint_ids(supabase_url, service_key, count, fallback_generator):
    """`count` fresh `ec<n>` ids, from the DB sequence when db/agent_locks_schema.sql has run.

    The sequence is the durable fix for 4.2: nextval is atomic and does not roll back, so two
    callers can never receive the same number even without the lock. `fallback_generator` is
    scrape_opportunities.next_id_generator's `ec<max+1>` — correct only while a lock guarantees
    one writer, which is why the lock is not optional just because this exists.
    """
    if supabase_url and service_key and count > 0:
        try:
            got = supabase_rpc(supabase_url, "next_opportunity_id", {"n": int(count)},
                               service_key)
            ids = [r if isinstance(r, str) else r.get("next_opportunity_id")
                   for r in (got or [])]
            ids = [i for i in ids if i]
            if len(ids) == count:
                return ids
        except Exception:
            pass  # function absent (or unreachable) — fall through to the in-memory minter
    return [next(fallback_generator) for _ in range(count)]


def guard_catalog_writes(agent, fn, argv=None):
    """Run `fn` holding the catalog-insert lock. THE entry point the agents use.

    Wrapped at each agent's `if __name__ == "__main__"` guard, which covers both real entry
    points: a hand-run `python -m agents.<name>` and the console, which launches exactly that
    command as a subprocess. Wrapping there rather than inside main() keeps the diff in the
    agents to two lines and leaves their argument parsing untouched.

    Refusing is a clean exit 2 with a one-line reason, not a traceback — an operator who
    launched a second run wants to be told, not debugged at. The console reads the same state
    through current_holder() and refuses before spawning anything at all.

    Two escapes, both deliberate:
      --preview  never takes the lock. It is free, read-only and finishes in seconds; making
                 it block (or be blocked by) a real run would be pure friction.
      WINGMAN_NO_RUN_LOCK=1  skips the lock entirely. For the case where a lock is wedged and
                 the operator KNOWS nothing else is running. It prints that it did so.
    """
    argv = sys.argv[1:] if argv is None else argv
    if "--preview" in argv:
        return fn()
    if os.environ.get("WINGMAN_NO_RUN_LOCK"):
        print("[WARN] WINGMAN_NO_RUN_LOCK set — running WITHOUT the catalog lock. Two "
              "inserting agents at once mint colliding ids (audit 4.2).")
        return fn()

    url = key = None
    try:
        from wingman.supabase_common import load_dotenv
        load_dotenv()
        url = os.environ.get("SUPABASE_URL", "").rstrip("/")
        key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    except Exception:
        pass

    lock = RunLock(CATALOG_INSERT, agent, url or None, key or None)
    try:
        lock.acquire()
    except LockBusy as e:
        print(f"[REFUSED] {e}. Two agents writing the catalog at once mint colliding ids "
              f"(audit 4.2). Wait for it to finish, or set WINGMAN_NO_RUN_LOCK=1 if you are "
              f"certain nothing else is running.")
        raise SystemExit(2)
    try:
        return fn()
    finally:
        lock.release()

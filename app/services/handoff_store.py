"""Short-lived, single-use nonces that survive a second worker.

PRODUCTION_READINESS_PLAN.md Phase 4, perf_report finding 11. The Google OAuth flows all have
the same shape: one request mints a nonce, a LATER and SEPARATE request spends it exactly once.
Those nonces lived in module-level dicts in app/services/google_oauth.py, which is correct for
one uvicorn worker and broken for two — the second request lands on the other worker about half
the time, finds nothing, and tells the student their sign-in link expired. It does not degrade
under load; it breaks sign-in intermittently, in a way that looks like a Google fault.

TWO BACKENDS, and which one you get is not a detail:

  DB (db/auth_handoffs_schema.sql applied)  the real thing. Every worker and every instance
      sees the same nonce, and single-use is enforced by Postgres: consumption is one DELETE
      returning its row, so of two concurrent spenders exactly one gets the payload.

  MEMORY (that table missing)  the in-process dicts, which is precisely today's behaviour.
      Warned about once, naming the .sql file. Correct on one worker, and it keeps a fresh
      checkout able to sign people in before anybody has opened the Supabase SQL editor.

THE NONCE IS HASHED BEFORE IT IS STORED. For the few minutes it lives, a nonce here is a live
credential — the only kind of credential this schema would hold at rest. It is only ever
compared for equality, so storing sha256(nonce) costs nothing and means a database dump or a
stray console query cannot hand anybody a working sign-in link. The plaintext never leaves the
process that minted it.

WHY NOT A STATELESS SIGNED TOKEN. The perf report offered an HMAC token as the alternative, and
it would need no storage at all — but it is REPLAYABLE until it expires, and single-use is the
entire point of these nonces. S1-3 made the calendar handoff single-use so that a URL sitting in
browser history, or leaking through a Referer header, is inert on the second click. A table
keeps that property. The signed-token shape would have quietly traded it for convenience, which
is why the unsubscribe link (genuinely long-lived, genuinely replay-safe) uses HMAC and this
does not.
"""
import hashlib
import json
import threading
import time
import urllib.error

from app.core import _supabase_request_strict, _error_body, _missing_table_error
from app.config import SUPABASE_URL, SUPABASE_SERVICE_KEY

TABLE = "auth_handoffs"

# How often a process bothers to sweep expired rows. Minting already costs an INSERT; a DELETE
# on every mint would double that for a table whose rows expire on their own schedule anyway.
# Once a minute per process is plenty — expiry is enforced on READ regardless of what is swept.
PRUNE_INTERVAL_SECONDS = 60

_UNIQUE_VIOLATION = "23505"

# Flipped permanently the first time PostgREST says the table is not there. Permanent on
# purpose: retrying a missing table on every sign-in would add a round trip to each one to keep
# rediscovering the same setup step. `python server.py` prints the warning once and moves on.
_db_available = True
_warned = False

_memory = {}                       # (kind, token_hash) -> {"payload": ..., "expires_at": float}
_memory_lock = threading.Lock()
_last_prune = 0.0
_prune_lock = threading.Lock()


def _hash(token):
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def _fall_back(reason):
    """Latch onto the memory backend and say so, once."""
    global _db_available, _warned
    _db_available = False
    if not _warned:
        _warned = True
        print(f"[WARN] auth_handoffs unavailable ({reason}) — Google sign-in nonces are "
              f"in-process only, so this service must run ONE worker. Run "
              f"db/auth_handoffs_schema.sql in the Supabase SQL editor.")


def _db_on():
    return _db_available and bool(SUPABASE_URL) and bool(SUPABASE_SERVICE_KEY)


def _maybe_prune():
    """Best-effort sweep of expired rows, at most once per PRUNE_INTERVAL_SECONDS per process.

    Expiry is enforced on read whatever this does, so a failed or skipped sweep is a tidiness
    problem, never a correctness one.
    """
    global _last_prune
    now = time.time()
    with _prune_lock:
        if now - _last_prune < PRUNE_INTERVAL_SECONDS:
            return
        _last_prune = now
    if not _db_on():
        with _memory_lock:
            for key in [k for k, v in _memory.items() if v["expires_at"] < now]:
                del _memory[key]
        return
    try:
        import datetime
        cutoff = datetime.datetime.now(datetime.timezone.utc).isoformat()
        _supabase_request_strict(TABLE, "DELETE", params={"expires_at": f"lt.{cutoff}"})
    except Exception:
        pass                      # tidiness only — see the docstring


def put(kind, token, payload, ttl_seconds):
    """Store `payload` under (kind, token) for ttl_seconds. Returns True if it was stored.

    A False here means the caller minted a nonce nothing will be able to spend, so it is
    reported rather than swallowed — the route turns it into an honest failure instead of a
    sign-in that dead-ends one redirect later.
    """
    _maybe_prune()
    expires_at = time.time() + ttl_seconds
    if not _db_on():
        with _memory_lock:
            _memory[(kind, _hash(token))] = {"payload": dict(payload or {}),
                                             "expires_at": expires_at}
        return True
    import datetime
    row = {
        "kind": kind,
        "token_hash": _hash(token),
        "payload": dict(payload or {}),
        "expires_at": datetime.datetime.fromtimestamp(
            expires_at, datetime.timezone.utc).isoformat(),
    }
    try:
        _supabase_request_strict(TABLE, "POST", data=row)
        return True
    except urllib.error.HTTPError as e:
        # _error_body consumes the response stream exactly once, so classify from the parsed
        # body rather than calling _missing_table_error(e) afterwards (the same trap
        # app/services/email.py documents at its own claim site).
        body = _error_body(e) or {}
        code = body.get("code")
        if code == _UNIQUE_VIOLATION:
            # secrets.token_urlsafe(32) colliding is not a thing that happens; if it somehow
            # does, refusing is right — overwriting would let one flow steal another's nonce.
            print(f"[WARN] auth_handoffs: {kind} nonce collided, refusing to overwrite.")
            return False
        if code in ("PGRST205", "42P01", "PGRST204", "42703"):
            _fall_back(f"HTTP {e.code} {code}")
            return put(kind, token, payload, ttl_seconds)
        print(f"[WARN] auth_handoffs put failed: {body.get('message') or e}")
        return False
    except Exception as e:
        print(f"[WARN] auth_handoffs put failed: {e}")
        return False


def take(kind, token):
    """The payload stored under (kind, token), consuming it. None if unknown or expired.

    Look-up and delete are ONE statement, which is what makes this single-use across workers:
    Postgres serialises two concurrent deletes and hands the row to exactly one of them. A
    read-then-delete would leave a window in which both spenders see the nonce.
    """
    if not token:
        return None
    token_hash = _hash(token)
    if not _db_on():
        with _memory_lock:
            entry = _memory.pop((kind, token_hash), None)
        if not entry or entry["expires_at"] < time.time():
            return None
        return entry["payload"]
    try:
        rows = _supabase_request_strict(
            TABLE, "DELETE",
            params={"kind": f"eq.{kind}", "token_hash": f"eq.{token_hash}"},
            extra_headers={"Prefer": "return=representation"}) or []
    except urllib.error.HTTPError as e:
        if _missing_table_error(e):
            _fall_back(f"HTTP {e.code}")
            return take(kind, token)
        print(f"[WARN] auth_handoffs take failed: {e}")
        return None
    except Exception as e:
        print(f"[WARN] auth_handoffs take failed: {e}")
        return None
    if not rows:
        return None
    row = rows[0]
    # Expiry is checked HERE and not left to the prune sweep: a row the sweep has not reached
    # yet is still expired, and honouring it would extend every nonce's real lifetime to
    # "until somebody tidies up".
    if _expired(row.get("expires_at")):
        return None
    payload = row.get("payload")
    if isinstance(payload, str):                 # PostgREST returns jsonb decoded; belt and braces
        try:
            payload = json.loads(payload)
        except ValueError:
            payload = {}
    return payload if isinstance(payload, dict) else {}


def _expired(iso):
    if not iso:
        return False
    import datetime
    try:
        when = datetime.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    return when < datetime.datetime.now(datetime.timezone.utc)


def _reset_for_tests():
    """Put the module back to a clean slate. Tests only."""
    global _db_available, _warned, _last_prune
    _db_available = True
    _warned = False
    _last_prune = 0.0
    with _memory_lock:
        _memory.clear()

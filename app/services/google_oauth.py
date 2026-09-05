"""Google Sign-In / Calendar in-process token stores and pruning helpers.
Extracted verbatim from server.py (docs/archive/PLAN_1_decompose.md). These are process-local
dicts (one uvicorn worker); the OAuth request/redirect glue lives in
app.routes.google_oauth, which also holds the calendar token-refresh helpers.
"""
import datetime
import json
import time
import secrets
import urllib.error
import urllib.parse
import urllib.request

from app.config import *  # noqa: F401,F403
from app.core import get_user, select_user, _users_request


# One-time-use handoff tokens bridging the OAuth redirect back to the SPA, which has no
# cookie/session concept of its own (see handle_login: login is just a POST that returns
# user JSON, cached client-side). Minted in handle_google_callback, consumed exactly once
# by handle_google_session. In-process only, like _opportunities_cache — fine for a
# single-process dev/prod server, and these are short-lived by design.
_google_session_tokens = {}


def _prune_google_tokens():
    now = time.time()
    expired = [t for t, entry in _google_session_tokens.items() if entry["expires_at"] < now]
    for t in expired:
        del _google_session_tokens[t]


def _mint_google_token(payload):
    _prune_google_tokens()
    token = secrets.token_urlsafe(32)
    _google_session_tokens[token] = {
        **payload,
        "expires_at": time.time() + GOOGLE_TOKEN_TTL_SECONDS,
    }
    return token


def _take_google_token(token):
    """Look up and delete a token in one step — single-use, so a replayed or leaked
    URL (browser history, a referrer header) can't be reused to resolve a session twice."""
    _prune_google_tokens()
    return _google_session_tokens.pop(token, None)

# ---------- Calendar handoff nonces (S1-3, finding M3) ----------
#
# /api/auth/google/calendar/start is a top-level browser navigation, so it cannot carry an
# Authorization header. It used to take the full 45-minute access JWT in the query string —
# which lands in Render's access logs, the browser history, the Referer of anything the
# OAuth flow touches, and any school or corporate proxy log between the student and here.
# A bearer token in a URL is a bearer token in a logfile.
#
# The nonce is the same shape as _mint_google_token above, with two differences that matter:
# it carries only a userid (never a credential), and its TTL is 60 seconds rather than five
# minutes, because the only gap it has to survive is one POST followed immediately by one
# navigation. Single-use on top of that, so a replayed URL out of history is inert.
CALENDAR_HANDOFF_TTL_SECONDS = 60

_google_calendar_handoffs = {}


def _prune_calendar_handoffs():
    now = time.time()
    for nonce in [n for n, e in _google_calendar_handoffs.items() if e["expires_at"] < now]:
        del _google_calendar_handoffs[nonce]


def mint_calendar_handoff(userid):
    """A single-use nonce standing in for `userid` for the next 60 seconds."""
    _prune_calendar_handoffs()
    nonce = secrets.token_urlsafe(32)
    _google_calendar_handoffs[nonce] = {
        "userid": userid,
        "expires_at": time.time() + CALENDAR_HANDOFF_TTL_SECONDS,
    }
    return nonce


def take_calendar_handoff(nonce):
    """The userid this nonce stands for, consuming it. None if unknown or expired.

    Look-up and delete in one step, like _take_google_token: a URL that reaches browser
    history or a Referer header must not resolve twice.
    """
    _prune_calendar_handoffs()
    entry = _google_calendar_handoffs.pop(nonce, None)
    if not entry or entry["expires_at"] < time.time():
        return None
    return entry["userid"]


# state -> {"userid": ..., "expires_at": ...}. Mirrors _google_session_tokens: in-process,
# short-lived, fine for a single-process server. Keyed separately from the sign-in state
# cookie so a stale calendar-connect attempt can't be replayed against the sign-in flow
# or vice versa.
_google_calendar_states = {}


def _prune_google_calendar_states():
    now = time.time()
    expired = [s for s, entry in _google_calendar_states.items() if entry["expires_at"] < now]
    for s in expired:
        del _google_calendar_states[s]


# state -> {"app_redirect": ..., "expires_at": ...}. Phase 3 (docs/archive/PLAN_3_rn.md): the sign-in
# redirect flow historically ended at the SPA served from the backend root ("/"). The Expo
# app is a SEPARATE origin (web) or a native app (custom scheme), so it passes its own
# redirect URI to /start; the callback sends the one-time google_token there instead of to
# "/". Keyed by the OAuth state so it can't be set for someone else's handshake, and the
# target is allowlist-checked at /start before it is ever stored here.
_google_login_redirects = {}


def _prune_google_login_redirects():
    now = time.time()
    expired = [s for s, entry in _google_login_redirects.items() if entry["expires_at"] < now]
    for s in expired:
        del _google_login_redirects[s]


# ---------- Google Calendar token refresh + dedicated-calendar helpers ----------
# Converted from Handler methods in server.py (docs/archive/PLAN_1_decompose.md). The redirect-uri
# derivation stays in the route (it needs the request Host header); these need only the
# userid/record and are pure service logic.

def get_google_calendar_access_token(userid):
    """Returns a valid access token for this user's Calendar grant, refreshing it
    first if expired. Returns None if the user has never connected Calendar, and
    raises on a Supabase/Google failure so the caller can distinguish the two."""
    # Four columns, not the whole row (S1-15, L10). select_user falls back to `*` if
    # db/google_calendar_schema.sql has not run, so an un-migrated database still answers
    # "not connected" instead of 400ing the read.
    record = select_user(userid, "userid,google_calendar_refresh_token,"
                                 "google_calendar_access_token,"
                                 "google_calendar_token_expires_at")
    if not record or not record.get("google_calendar_refresh_token"):
        return None
    expires_at = record.get("google_calendar_token_expires_at")
    access_token = record.get("google_calendar_access_token")
    still_valid = False
    if expires_at and access_token:
        try:
            exp = datetime.datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            still_valid = exp > datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=60)
        except ValueError:
            still_valid = False
    if still_valid:
        return access_token

    token_req = urllib.request.Request(
        GOOGLE_TOKEN_URL,
        data=urllib.parse.urlencode({
            "refresh_token": record["google_calendar_refresh_token"],
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "grant_type": "refresh_token",
        }).encode(),
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(token_req, timeout=10) as resp:
        tokens = json.loads(resp.read())
    access_token = tokens.get("access_token")
    expires_in = tokens.get("expires_in") or 3600
    if not access_token:
        return None
    expires_at = (datetime.datetime.now(datetime.timezone.utc)
                  + datetime.timedelta(seconds=expires_in)).isoformat()
    query_patch = "?" + urllib.parse.urlencode({"userid": f"eq.{userid}"})
    _users_request("PATCH", query_patch, data={
        "google_calendar_access_token": access_token,
        "google_calendar_token_expires_at": expires_at,
    })
    return access_token


def ensure_wingman_calendar(access_token, userid, record):
    """Returns the id of this user's dedicated "Highschool Wingman" calendar,
    creating it on first use. calendar.app.created only grants access to events on
    calendars the app itself created, so events can never land on the user's primary
    calendar or any other existing one — this is what makes that true."""
    calendar_id = record.get("google_calendar_id")
    if calendar_id:
        check_req = urllib.request.Request(
            f"{GOOGLE_CALENDAR_API_BASE}/calendars/{urllib.parse.quote(calendar_id)}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        try:
            with urllib.request.urlopen(check_req, timeout=10):
                return calendar_id
        except urllib.error.HTTPError as e:
            if e.code not in (404, 403):
                raise
            # Calendar was deleted on Google's side (or predates this grant) — fall
            # through and create a fresh one.

    create_req = urllib.request.Request(
        f"{GOOGLE_CALENDAR_API_BASE}/calendars",
        data=json.dumps({"summary": WINGMAN_CALENDAR_NAME}).encode(),
        method="POST",
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(create_req, timeout=10) as resp:
        created = json.loads(resp.read())
    calendar_id = created["id"]
    query_patch = "?" + urllib.parse.urlencode({"userid": f"eq.{userid}"})
    _users_request("PATCH", query_patch, data={"google_calendar_id": calendar_id})
    return calendar_id

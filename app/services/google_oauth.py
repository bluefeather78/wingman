"""Google Sign-In / Calendar handoff nonces and the OAuth state they are keyed by.
Extracted from server.py (docs/archive/PLAN_1_decompose.md); the OAuth request/redirect glue
lives in app.routes.google_oauth, which also holds the calendar token-refresh helpers.

PHASE 4: THESE ARE NO LONGER PROCESS-LOCAL DICTS. All four stores below now sit on
app.services.handoff_store, which keeps them in the `auth_handoffs` table when
db/auth_handoffs_schema.sql has been run and falls back to in-process dicts (today's exact
behaviour, warned about once) when it has not.

The reason is perf_report finding 11 and it is worth stating plainly, because every one of
these looks harmless on a laptop: each store is WRITTEN by one request and READ by a later,
separate one. On two uvicorn workers the second request lands on the other worker about half
the time, finds nothing, and tells the student their sign-in link expired — on a link that is
perfectly valid. It does not degrade under load, it breaks sign-in intermittently, and it is
the single hardest blocker on running `--workers > 1`.

Single-use is preserved, not traded away: consumption is one DELETE returning its row, so of
two concurrent spenders exactly one gets the payload. See handoff_store's module docstring for
why that matters more than the stateless signed token the perf report offered as an
alternative.
"""
import datetime
import json
import secrets
import urllib.error
import urllib.parse
import urllib.request

from app.config import *  # noqa: F401,F403
from app.core import get_user, select_user, _users_request
from app.services import handoff_store


# One-time-use handoff tokens bridging the OAuth redirect back to the SPA, which has no
# cookie/session concept of its own (see handle_login: login is just a POST that returns
# user JSON, cached client-side). Minted in handle_google_callback, consumed exactly once
# by handle_google_session. Shared across workers since Phase 4 (see the module docstring).
KIND_SESSION = "google_session"


def _mint_google_token(payload):
    """A single-use token standing for `payload` for GOOGLE_TOKEN_TTL_SECONDS.

    Returns None when the store refuses it. The caller must treat that as a failed sign-in
    rather than redirecting with a token nothing can spend — a nonce that cannot be stored is
    a dead end one redirect later, and an honest error there is far easier to diagnose than
    "this sign-in link has expired" on a link minted two seconds ago.
    """
    token = secrets.token_urlsafe(32)
    if not handoff_store.put(KIND_SESSION, token, payload, GOOGLE_TOKEN_TTL_SECONDS):
        return None
    return token


def _take_google_token(token):
    """Look up and delete a token in one step — single-use, so a replayed or leaked
    URL (browser history, a referrer header) can't be reused to resolve a session twice."""
    return handoff_store.take(KIND_SESSION, token)


def _peek_google_token(token):
    """Read the sign-in token WITHOUT consuming it. The resolve step (handle_google_session)
    uses this so a PENDING signup's token survives for the consent POST (handle_google_finish),
    which is what actually spends it. See handoff_store.peek."""
    return handoff_store.peek(KIND_SESSION, token)

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

KIND_CALENDAR_HANDOFF = "calendar_handoff"


def mint_calendar_handoff(userid):
    """A single-use nonce standing in for `userid` for the next 60 seconds.

    None when the store refuses it, for the same reason _mint_google_token can be None.
    """
    nonce = secrets.token_urlsafe(32)
    if not handoff_store.put(KIND_CALENDAR_HANDOFF, nonce, {"userid": userid},
                             CALENDAR_HANDOFF_TTL_SECONDS):
        return None
    return nonce


def take_calendar_handoff(nonce):
    """The userid this nonce stands for, consuming it. None if unknown or expired.

    Look-up and delete in one step, like _take_google_token: a URL that reaches browser
    history or a Referer header must not resolve twice.
    """
    entry = handoff_store.take(KIND_CALENDAR_HANDOFF, nonce)
    return (entry or {}).get("userid")


# The calendar grant's OAuth `state` -> {"userid", "app_redirect"}. Keyed separately from the
# sign-in state cookie (a different `kind`, so a different primary key) so a stale
# calendar-connect attempt can't be replayed against the sign-in flow or vice versa.
KIND_CALENDAR_STATE = "calendar_state"


def remember_calendar_state(state, userid, app_redirect=""):
    """Record what a calendar-grant handshake is for. False if it could not be stored."""
    return handoff_store.put(KIND_CALENDAR_STATE, state,
                             {"userid": userid, "app_redirect": app_redirect or ""},
                             GOOGLE_TOKEN_TTL_SECONDS)


def take_calendar_state(state):
    """The handshake this state belongs to, consuming it. None if unknown or expired."""
    return handoff_store.take(KIND_CALENDAR_STATE, state)


# ---- Delete-account re-auth (DATA_DELETION_EXPORT_PLAN.md P3, Google-only accounts) ----
# A passwordless (Google) account proves current control of its Google account before it can
# delete, exactly the way calendar-connect proves control: a bearer POST mints a nonce, the
# nonce drives a Google round-trip, and the callback verifies the SAME google_id came back
# before minting a single-use PROOF that the delete route consumes in place of a password.
# Its own `kind`s throughout, so a nonce from one flow can never be replayed against another.
DELETE_REAUTH_HANDOFF_TTL_SECONDS = 120
DELETE_REAUTH_PROOF_TTL_SECONDS = 120
KIND_DELETE_REAUTH_HANDOFF = "delete_reauth_handoff"
KIND_DELETE_REAUTH_STATE = "delete_reauth_state"
KIND_DELETE_REAUTH_PROOF = "delete_reauth_proof"


def mint_delete_reauth_handoff(userid):
    """A single-use nonce standing in for `userid` while the Google round-trip runs. None if
    the store refuses it (same reason mint_calendar_handoff can be None)."""
    nonce = secrets.token_urlsafe(32)
    if not handoff_store.put(KIND_DELETE_REAUTH_HANDOFF, nonce, {"userid": userid},
                             DELETE_REAUTH_HANDOFF_TTL_SECONDS):
        return None
    return nonce


def take_delete_reauth_handoff(nonce):
    """The userid this nonce stands for, consuming it. None if unknown or expired."""
    entry = handoff_store.take(KIND_DELETE_REAUTH_HANDOFF, nonce)
    return (entry or {}).get("userid")


def remember_delete_reauth_state(state, userid, app_redirect=""):
    """Record what a delete-reauth handshake is for. False if it could not be stored."""
    return handoff_store.put(KIND_DELETE_REAUTH_STATE, state,
                             {"userid": userid, "app_redirect": app_redirect or ""},
                             GOOGLE_TOKEN_TTL_SECONDS)


def take_delete_reauth_state(state):
    """The handshake this state belongs to, consuming it. None if unknown or expired."""
    return handoff_store.take(KIND_DELETE_REAUTH_STATE, state)


def mint_delete_reauth_proof(userid):
    """A single-use PROOF that `userid` just re-authenticated via Google. The delete route
    consumes it in place of a password. None if the store refuses it."""
    token = secrets.token_urlsafe(32)
    if not handoff_store.put(KIND_DELETE_REAUTH_PROOF, token, {"userid": userid},
                             DELETE_REAUTH_PROOF_TTL_SECONDS):
        return None
    return token


def take_delete_reauth_proof(token):
    """The userid this proof stands for, consuming it. None if unknown or expired. Single-use:
    a proof that reaches browser history or a Referer header is inert on the second read."""
    entry = handoff_store.take(KIND_DELETE_REAUTH_PROOF, token)
    return (entry or {}).get("userid")


# The sign-in handshake's OAuth `state` -> {"app_redirect"}. Phase 3
# (docs/archive/PLAN_3_rn.md): the sign-in redirect flow historically ended at the SPA served
# from the backend root ("/"). The Expo app is a SEPARATE origin (web) or a native app (custom
# scheme), so it passes its own redirect URI to /start; the callback sends the one-time
# google_token there instead of to "/". Keyed by the OAuth state so it can't be set for
# someone else's handshake, and the target is allowlist-checked at /start before it is ever
# stored here.
KIND_LOGIN_REDIRECT = "login_redirect"


def remember_login_redirect(state, app_redirect):
    """Record where this handshake's token should be sent back to."""
    return handoff_store.put(KIND_LOGIN_REDIRECT, state, {"app_redirect": app_redirect},
                             GOOGLE_TOKEN_TTL_SECONDS)


def take_login_redirect(state):
    """The app redirect registered for this handshake, consuming it. "" if there was none."""
    entry = handoff_store.take(KIND_LOGIN_REDIRECT, state) if state else None
    return (entry or {}).get("app_redirect") or ""


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


def purge_google_calendar(userid, record=None):
    """Best-effort: delete this user's dedicated Wingman calendar and revoke the Google
    grant, for account deletion (DATA_DELETION_EXPORT_PLAN.md §3.1).

    NEVER raises — a leftover calendar is a nuisance, not a billing or privacy-critical
    failure, and the tokens become useless the moment the users row is deleted. Returns a
    small status dict for the deletion report. Does NOT touch the DB columns: the whole row
    is about to be deleted, so nulling them would be redundant.

    The calendar.app.created scope (MARQUEE M7) permits deleting a calendar the app itself
    created, so DELETE /calendars/{id} removes the whole "Highschool Wingman" calendar and
    every event on it in one call.
    """
    if record is None:
        record = select_user(
            userid, "userid,google_calendar_id,google_calendar_refresh_token") or {}
    calendar_id = record.get("google_calendar_id")
    refresh_token = record.get("google_calendar_refresh_token")
    if not calendar_id and not refresh_token:
        return {"status": "none"}

    result = {"status": "attempted", "calendar_deleted": False, "token_revoked": False}
    try:
        access_token = get_google_calendar_access_token(userid)
    except Exception as e:                                     # noqa: BLE001
        access_token = None
        print(f"[delete] could not obtain Google token for calendar purge: {type(e).__name__}")

    if access_token and calendar_id:
        try:
            del_req = urllib.request.Request(
                f"{GOOGLE_CALENDAR_API_BASE}/calendars/{urllib.parse.quote(calendar_id)}",
                method="DELETE",
                headers={"Authorization": f"Bearer {access_token}"})
            with urllib.request.urlopen(del_req, timeout=10):
                result["calendar_deleted"] = True
        except urllib.error.HTTPError as e:
            # Already gone counts as deleted; anything else is logged and left.
            result["calendar_deleted"] = e.code in (404, 410)
            if e.code not in (404, 410):
                print(f"[delete] calendar delete failed: HTTP {e.code}")
        except Exception as e:                                 # noqa: BLE001
            print(f"[delete] calendar delete error: {type(e).__name__}")

    if refresh_token:
        try:
            rev_req = urllib.request.Request(
                GOOGLE_REVOKE_URL,
                data=urllib.parse.urlencode({"token": refresh_token}).encode(),
                method="POST",
                headers={"Content-Type": "application/x-www-form-urlencoded"})
            with urllib.request.urlopen(rev_req, timeout=10):
                result["token_revoked"] = True
        except urllib.error.HTTPError as e:
            # Google answers 400 for an already-invalid/expired token — that is still revoked.
            result["token_revoked"] = e.code == 400
            if e.code != 400:
                print(f"[delete] token revoke failed: HTTP {e.code}")
        except Exception as e:                                 # noqa: BLE001
            print(f"[delete] token revoke error: {type(e).__name__}")
    return result


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

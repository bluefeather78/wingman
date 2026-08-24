"""Google Sign-In and Google Calendar routes. Translated from server.py's
handle_google_* / handle_calendar_sync (PLAN_1_decompose.md). The four-step redirect
flow (start -> Google -> callback -> session|finish) and the calendar connect/sync are
preserved exactly; redirect URIs are still derived from the request Host header.
"""
import datetime
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

from fastapi import APIRouter, Request, Depends
from fastapi.responses import RedirectResponse

from app.config import (
    PORT, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_AUTH_URL, GOOGLE_TOKEN_URL,
    GOOGLE_USERINFO_URL, GOOGLE_TOKEN_TTL_SECONDS, GOOGLE_CALENDAR_SCOPE,
    GOOGLE_CALENDAR_API_BASE, WINGMAN_CALENDAR_NAME,
)
from app.core import (
    get_user, get_user_by_email, get_user_by_google_id, create_user, ensure_trial_started,
    _check_signup_consent, _unique_userid_from_email, _users_request,
    _is_missing_column_error, MissingUserColumns, DuplicateEmail,
)
from app.services.email import send_lifecycle_email_async
from app.deps import (json_body, json_response, json_error, login_response,
                      require_subscription)
from app.auth import AuthedUser
from app.auth.tokens import verify_access_token, AuthError, AuthConfigError
from app.services import google_oauth as g

router = APIRouter()


def _host(request):
    return request.headers.get("Host", f"localhost:{PORT}")


def _redirect_uri(request, path):
    host = _host(request)
    scheme = "http" if host.startswith("localhost") or host.startswith("127.0.0.1") else "https"
    return f"{scheme}://{host}{path}"


# Google treats 127.0.0.1 and localhost as DIFFERENT redirect URIs and matches them exactly,
# so a client registered with http://localhost:8000/... rejects the same server reached as
# 127.0.0.1 with Error 400: redirect_uri_mismatch. The dev command in CLAUDE.md sets
# EXPO_PUBLIC_API_BASE=http://127.0.0.1:8000, which is precisely that case.
#
# Rewriting only the redirect_uri would not work: the state cookie is host-scoped, so the
# callback would arrive on localhost with no cookie and fail the CSRF check. Instead the
# whole handshake is moved onto the canonical host BEFORE it starts — one 302, after which
# the cookie, Google's redirect and the callback all share an origin.
_LOOPBACK_ALIASES = ("127.0.0.1", "[::1]", "::1")


def _canonicalize_loopback(request):
    """Returns a redirect onto localhost when reached via a loopback IP, else None."""
    host = _host(request)
    hostname = host.split(":")[0] if not host.startswith("[") else host.rsplit(":", 1)[0]
    if hostname not in _LOOPBACK_ALIASES:
        return None
    port = host.rsplit(":", 1)[-1] if ":" in host and not host.endswith("]") else str(PORT)
    target = str(request.url.replace(netloc=f"localhost:{port}"))
    return RedirectResponse(target, status_code=302)


def _redirect_home(query_suffix=""):
    return RedirectResponse(f"/{query_suffix}", status_code=302)


# Phase 3: where the Expo app (web origin or native scheme) may receive the one-time
# google_token. An allowlist prevents this from becoming an open redirect — the callback
# will send a signed sign-in handoff to whatever this points at, so it must only ever be
# our own app. Native scheme + local dev origins by default; override/extend in production
# with GOOGLE_APP_REDIRECTS (comma-separated origin/scheme prefixes) for the Render static
# site's origin.
_DEFAULT_APP_REDIRECTS = [
    "wingman://", "exp://",
    "http://localhost:8081", "http://localhost:8082",
    "http://127.0.0.1:8081", "http://127.0.0.1:8082",
]
_ALLOWED_APP_REDIRECTS = [
    p.strip() for p in os.environ.get("GOOGLE_APP_REDIRECTS", "").split(",") if p.strip()
] or _DEFAULT_APP_REDIRECTS


def _is_allowed_app_redirect(uri: str) -> bool:
    return bool(uri) and any(uri.startswith(prefix) for prefix in _ALLOWED_APP_REDIRECTS)


@router.get("/api/auth/google/start")
def handle_google_start(request: Request):
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return json_error(503, "Google Sign-In is not configured: set "
                               "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env.")
    canonical = _canonicalize_loopback(request)
    if canonical:
        return canonical
    state = secrets.token_urlsafe(24)
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": _redirect_uri(request, "/api/auth/google/callback"),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    }
    # Phase 3: if the caller is the Expo app (separate origin / native scheme), remember an
    # allowlisted redirect keyed by this handshake's state, so the callback can hand the
    # sign-in token back to the app instead of to the backend-root SPA.
    app_redirect = request.query_params.get("app_redirect") or ""
    if app_redirect and _is_allowed_app_redirect(app_redirect):
        g._prune_google_login_redirects()
        g._google_login_redirects[state] = {
            "app_redirect": app_redirect,
            "expires_at": time.time() + GOOGLE_TOKEN_TTL_SECONDS,
        }
    resp = RedirectResponse(f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}", status_code=302)
    # Short-lived, HttpOnly CSRF protection for the handshake only (not an app session).
    resp.set_cookie("google_oauth_state", state, max_age=GOOGLE_TOKEN_TTL_SECONDS,
                    httponly=True, path="/")
    return resp


@router.get("/api/auth/google/callback")
def handle_google_callback(request: Request):
    query = request.query_params
    cookie_state = request.cookies.get("google_oauth_state")
    req_state = query.get("state") or ""
    code = query.get("code") or ""
    if not code or not req_state or not cookie_state or req_state != cookie_state:
        return json_error(400, "Google sign-in failed: invalid or expired "
                               "request. Please try again.")
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return json_error(503, "Google Sign-In is not configured.")

    try:
        token_req = urllib.request.Request(
            GOOGLE_TOKEN_URL,
            data=urllib.parse.urlencode({
                "code": code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": _redirect_uri(request, "/api/auth/google/callback"),
                "grant_type": "authorization_code",
            }).encode(),
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(token_req, timeout=10) as resp:
            tokens = json.loads(resp.read())
        userinfo_req = urllib.request.Request(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        with urllib.request.urlopen(userinfo_req, timeout=10) as resp:
            profile = json.loads(resp.read())
    except Exception as e:
        print(f"[WARN] Google OAuth exchange failed: {e}")
        return json_error(502, "Could not verify your Google account. Please try again.")

    google_id = profile.get("sub")
    email = profile.get("email") or ""
    first_name = profile.get("given_name") or ""
    last_name = profile.get("family_name") or ""
    if not google_id or not email:
        return json_error(502, "Google did not return a usable profile.")

    try:
        record = get_user_by_google_id(google_id)
        if not record:
            by_email = get_user_by_email(email)
            if by_email:
                # Google has verified this address, so link it to the existing account.
                record = get_user(by_email["userid"])
                query_patch = "?" + urllib.parse.urlencode({"userid": f"eq.{record['userid']}"})
                _users_request("PATCH", query_patch, data={"google_id": google_id})
                record["google_id"] = google_id
    except Exception as e:
        return json_error(502, f"Could not reach Supabase: {e}")

    if record:
        token = g._mint_google_token({"kind": "login", "userid": record["userid"]})
    else:
        token = g._mint_google_token({
            "kind": "pending",
            "google_id": google_id,
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
        })
    # Phase 3: if the app registered a redirect for this handshake, send the one-time token
    # there (the Expo app captures it); otherwise fall back to the backend-root SPA.
    g._prune_google_login_redirects()
    redirect_entry = g._google_login_redirects.pop(req_state, None)
    if redirect_entry:
        dest = redirect_entry["app_redirect"]
        sep = "&" if "?" in dest else "?"
        return RedirectResponse(f"{dest}{sep}google_token={urllib.parse.quote(token)}",
                                status_code=302)
    return _redirect_home(f"?google_token={urllib.parse.quote(token)}")


@router.get("/api/auth/google/session")
def handle_google_session(request: Request):
    token = request.query_params.get("token") or ""
    entry = g._take_google_token(token)
    if not entry:
        return json_error(400, "This sign-in link has expired. Please try "
                               "signing in with Google again.")
    if entry["kind"] == "pending":
        return json_response(200, {
            "ok": True,
            "pending": True,
            "firstName": entry["first_name"],
            "lastName": entry["last_name"],
            "email": entry["email"],
        })
    try:
        record = get_user(entry["userid"])
    except Exception as e:
        return json_error(502, f"Could not reach Supabase: {e}")
    if not record:
        return json_error(404, "No account found.")
    record = ensure_trial_started(record["userid"], record)
    try:
        return json_response(200, login_response(record))
    except AuthConfigError as e:
        return json_error(503, str(e))


@router.post("/api/auth/google/finish")
def handle_google_finish(request: Request, body: dict = Depends(json_body)):
    token = body.get("token") or ""
    entry = g._take_google_token(token)
    if not entry or entry.get("kind") != "pending":
        return json_error(400, "This sign-in link has expired. Please try "
                               "signing in with Google again.")

    location = (body.get("location") or "").strip()
    is_adult = bool(body.get("isAdult"))
    parental_consent = bool(body.get("parentalConsent"))
    accepted_terms = bool(body.get("acceptedTerms"))
    consent_error = _check_signup_consent(is_adult, parental_consent, accepted_terms)
    if consent_error:
        return json_error(400, consent_error)
    if not location:
        return json_error(400, "Missing required fields.")

    try:
        if get_user_by_google_id(entry["google_id"]) or get_user_by_email(entry["email"]):
            return json_error(409, "An account for this Google profile "
                                   "already exists. Please sign in again.")
    except Exception as e:
        return json_error(502, f"Could not reach Supabase: {e}")

    userid = _unique_userid_from_email(entry["email"])
    try:
        create_user(userid, entry["first_name"], entry["last_name"], entry["email"],
                    None, location, is_adult=is_adult,
                    parental_consent=parental_consent, google_id=entry["google_id"])
    except MissingUserColumns:
        return json_error(503, "Accounts are temporarily unavailable: the "
                               "database is missing required columns. Run "
                               "subscription_schema.sql and google_auth_schema.sql "
                               "in the Supabase SQL editor, then try again.")
    except DuplicateEmail:
        return json_error(409, "An account already exists with that email "
                               "address. Please sign in instead.")
    except Exception as e:
        return json_error(502, f"Could not reach Supabase: {e}")

    record = get_user(userid)

    # A Google signup is still a signup — it gets the same welcome email as the form path.
    # Easy to miss precisely because create_user() is called from two places; the
    # email_sends claim means adding it here can never double up with account.py's.
    send_lifecycle_email_async(userid, "welcome", record=record)
    try:
        return json_response(200, login_response(record))
    except AuthConfigError as e:
        return json_error(503, str(e))


@router.get("/api/auth/google/calendar/start")
def handle_google_calendar_start(request: Request):
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return json_error(503, "Google Sign-In is not configured: set "
                               "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env.")
    # This is a top-level browser navigation (location.href), so it cannot carry an
    # Authorization header — the access token rides in the query string instead, and the
    # userid is derived from it, never taken from the URL directly. Same trust model as the
    # rest of the app: identity comes from the signed token.
    canonical = _canonicalize_loopback(request)
    if canonical:
        return canonical
    token = request.query_params.get("token") or ""
    try:
        userid = verify_access_token(token)
    except AuthConfigError:
        return json_error(503, "Authentication is temporarily unavailable.")
    except AuthError:
        return json_error(401, "Please sign in to connect Google Calendar.")
    try:
        if not get_user(userid):
            return json_error(404, "No account found.")
    except Exception as e:
        return json_error(502, f"Could not reach Supabase: {e}")

    g._prune_google_calendar_states()
    state = secrets.token_urlsafe(24)
    # In dev the app and the API are two origins (Metro :8081 -> API :8000), so returning to
    # the API's own root would land the student on a 404 rather than back in the Quest Log.
    # Same allowlist Google Sign-In already uses, so this can't become an open redirect.
    app_redirect = request.query_params.get("app_redirect") or ""
    g._google_calendar_states[state] = {
        "userid": userid,
        "app_redirect": app_redirect if _is_allowed_app_redirect(app_redirect) else "",
        "expires_at": time.time() + GOOGLE_TOKEN_TTL_SECONDS,
    }

    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": _redirect_uri(request, "/api/auth/google/calendar/callback"),
        "response_type": "code",
        "scope": GOOGLE_CALENDAR_SCOPE,
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
    }
    resp = RedirectResponse(f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}", status_code=302)
    resp.set_cookie("google_calendar_oauth_state", state, max_age=GOOGLE_TOKEN_TTL_SECONDS,
                    httponly=True, path="/")
    return resp


@router.get("/api/auth/google/calendar/callback")
def handle_google_calendar_callback(request: Request):
    query = request.query_params
    cookie_state = request.cookies.get("google_calendar_oauth_state")
    req_state = query.get("state") or ""
    code = query.get("code") or ""
    g._prune_google_calendar_states()
    entry = g._google_calendar_states.pop(req_state, None) if req_state else None
    if not code or not req_state or not cookie_state or req_state != cookie_state or not entry:
        return json_error(400, "Google Calendar connection failed: invalid "
                               "or expired request. Please try again.")
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return json_error(503, "Google Sign-In is not configured.")

    try:
        token_req = urllib.request.Request(
            GOOGLE_TOKEN_URL,
            data=urllib.parse.urlencode({
                "code": code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": _redirect_uri(request, "/api/auth/google/calendar/callback"),
                "grant_type": "authorization_code",
            }).encode(),
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(token_req, timeout=10) as resp:
            tokens = json.loads(resp.read())
    except Exception as e:
        print(f"[WARN] Google Calendar OAuth exchange failed: {e}")
        return json_error(502, "Could not connect Google Calendar. Please try again.")

    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")
    expires_in = tokens.get("expires_in") or 3600
    if not access_token:
        return json_error(502, "Google did not return a usable token.")

    now = datetime.datetime.now(datetime.timezone.utc)
    expires_at = (now + datetime.timedelta(seconds=expires_in)).isoformat()
    patch = {
        "google_calendar_access_token": access_token,
        "google_calendar_token_expires_at": expires_at,
        "google_calendar_connected_at": now.isoformat(),
    }
    if refresh_token:
        patch["google_calendar_refresh_token"] = refresh_token
    try:
        query_patch = "?" + urllib.parse.urlencode({"userid": f"eq.{entry['userid']}"})
        _users_request("PATCH", query_patch, data=patch)
    except urllib.error.HTTPError as e:
        if _is_missing_column_error(e):
            return json_error(503, "Google Calendar sync is temporarily "
                                   "unavailable: run google_calendar_schema.sql "
                                   "in the Supabase SQL editor, then try again.")
        return json_error(502, f"Could not reach Supabase: {e}")
    except Exception as e:
        return json_error(502, f"Could not reach Supabase: {e}")

    # Back to the Quest Log, where the Sync to Calendar button lives — the student pressed
    # it there, so landing on Home Base would leave them to find their way back. In
    # production the app and API share an origin, so the relative path is the right default.
    app_redirect = (entry or {}).get("app_redirect") or ""
    if app_redirect:
        sep = "&" if "?" in app_redirect else "?"
        return RedirectResponse(f"{app_redirect}{sep}calendar_connected=1", status_code=302)
    return RedirectResponse("/tracker?calendar_connected=1", status_code=302)


# Identifies WHICH tracked date an event belongs to, as `${itemId}::${dateIndex}`. Its job is
# to let a sync PATCH the event it already wrote instead of creating a second one; it is no
# longer what decides whether an event may be deleted.
#
# It used to be that gate: the sweep only removed events carrying this marker, so that a
# Wingman calendar the student had also added their own entries to survived a sync intact.
# That protected a case nobody hit and broke the case everybody hits — the marker only landed
# from 2026-08-22, so every event written before it was permanently unsweepable. Measured on
# the first real account: 45 events on the calendar for 17 tracked dates, 22 of them orphans
# that no sync could ever clean up, including deadlines for opportunities deleted from the
# app weeks earlier. The calendar is now a MIRROR of the Quest Log (see _sweep_stale_events).
WINGMAN_EVENT_PROP = "wingmanId"


def _calendar_request(method, url, access_token, payload=None):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode() if payload is not None else None,
        method=method,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = resp.read()
    return json.loads(body) if body else {}


def _list_wingman_events(access_token, calendar_path):
    """Every event on the Wingman calendar that WE wrote, as {wingmanId: [event ids]}.

    A marker can legitimately map to more than one event only when something has already
    gone wrong (see _existing_event_map), so the list is ordered oldest-first and callers
    keep the first.
    """
    by_marker = {}
    page_token = None
    while True:
        params = {"maxResults": "2500", "showDeleted": "false"}
        if page_token:
            params["pageToken"] = page_token
        page = _calendar_request(
            "GET",
            f"{GOOGLE_CALENDAR_API_BASE}/{calendar_path}/events?{urllib.parse.urlencode(params)}",
            access_token,
        )
        for ev in page.get("items") or []:
            marker = ((ev.get("extendedProperties") or {}).get("private") or {}).get(
                WINGMAN_EVENT_PROP)
            if marker and ev.get("id"):
                by_marker.setdefault(marker, []).append(ev["id"])
        page_token = page.get("nextPageToken")
        if not page_token:
            break
    return by_marker


# NOTE: forcing the Wingman calendar visible in the student's sidebar is NOT possible and
# should not be re-attempted. PATCH /users/me/calendarList/<id> returns 401 under the
# calendar.app.created scope (verified 2026-08-24 with a token that reads and writes events
# on that very calendar) — the scope covers events on app-created calendars, not the user's
# calendar list. Discovery is handled instead by NAMING the calendar in the sync result and
# returning a link straight into it; see handle_calendar_sync.


def _existing_event_map(access_token, calendar_path):
    """(marker -> the event id to reuse, [extra event ids that duplicate one]).

    More than one event under a marker means a duplicate we created earlier: the marker is
    `${itemId}::${dateIndex}`, which addresses exactly one date, so a second event under it
    is never a distinct deadline. The oldest is kept because it is the one whose id any
    client may still be holding.
    """
    by_marker = _list_wingman_events(access_token, calendar_path)
    keep = {marker: ids[0] for marker, ids in by_marker.items() if ids}
    extras = [eid for ids in by_marker.values() for eid in ids[1:]]
    return keep, extras


def _delete_events(access_token, calendar_path, event_ids):
    """Delete the given events. Returns (deleted, errors); never raises."""
    deleted, errors = 0, []
    for event_id in event_ids:
        if not event_id:
            continue
        try:
            _calendar_request(
                "DELETE",
                f"{GOOGLE_CALENDAR_API_BASE}/{calendar_path}/events/{urllib.parse.quote(event_id)}",
                access_token,
            )
            deleted += 1
        except urllib.error.HTTPError as e:
            # Already gone on Google's side is the outcome we wanted anyway.
            if e.code in (404, 410):
                deleted += 1
            else:
                errors.append(f"Google API error {e.code} deleting an event")
        except Exception as e:
            errors.append(str(e))
    return deleted, errors


def _sweep_stale_events(access_token, calendar_path, keep_ids):
    """Make the Wingman calendar MIRROR the Quest Log: delete every event on it that is not
    one of the currently-tracked deadlines. This is what removes a deadline after the student
    takes the opportunity out of the Quest Log — nothing client-side has to remember the
    Google event id, and it self-heals across devices and across removals made while offline.

    An event with NO marker is now deleted too. That is a deliberate widening (2026-08-24):
    the marker only started being written on 2026-08-22, so restricting deletion to marked
    events left every older one stranded on the calendar forever. This calendar is created by
    the app, for the app, under the calendar.app.created scope — it is not a calendar the
    student keeps their own life in, and the whole point of the feature is that it reflects
    what the app says. The cost of the widening is real and worth stating: anything the
    student adds to THIS calendar by hand will be removed on the next sync.

    Returns (deleted_count, errors). Errors never fail the sync — the upserts already
    landed, and a half-swept calendar is better than a sync that reports failure."""
    deleted = 0
    errors = []
    page_token = None
    stale = []
    while True:
        params = {"maxResults": "2500", "showDeleted": "false"}
        if page_token:
            params["pageToken"] = page_token
        try:
            page = _calendar_request(
                "GET",
                f"{GOOGLE_CALENDAR_API_BASE}/{calendar_path}/events?{urllib.parse.urlencode(params)}",
                access_token,
            )
        except Exception as e:
            return deleted, [f"Could not list calendar events: {e}"]
        for ev in page.get("items") or []:
            props = ((ev.get("extendedProperties") or {}).get("private") or {})
            marker = props.get(WINGMAN_EVENT_PROP)
            # No marker => written before markers existed, or not ours. Either way it is not
            # a currently-tracked deadline, and this calendar mirrors the Quest Log.
            if marker and marker in keep_ids:
                continue
            stale.append(ev.get("id"))
        page_token = page.get("nextPageToken")
        if not page_token:
            break

    got, errs = _delete_events(access_token, calendar_path, stale)
    return deleted + got, errors + errs


@router.post("/api/calendar/sync")
def handle_calendar_sync(body: dict = Depends(json_body),
                         user: AuthedUser = Depends(require_subscription)):
    # Writing to a student's real calendar is using the app, so it is gated on standing as
    # well as identity. Events already on the calendar are left alone — a lapsed account
    # stops syncing, it does not get its calendar wiped.
    userid = user.id
    events = body.get("events") or []
    # `sweep` asks the server to also remove events for deadlines that are no longer
    # tracked. That makes an EMPTY list meaningful — "nothing is tracked any more, clear
    # the calendar" — so the not-empty guard only applies when no sweep was requested.
    sweep = bool(body.get("sweep"))
    if not isinstance(events, list) or (not events and not sweep):
        return json_error(400, "No events to sync.")

    try:
        access_token = g.get_google_calendar_access_token(userid)
    except Exception as e:
        return json_error(502, f"Could not refresh Google Calendar access: {e}")
    if not access_token:
        return json_error(409, "Google Calendar is not connected for this "
                               "account. Connect it first.")

    try:
        calendar_id = g.ensure_wingman_calendar(access_token, userid, get_user(userid))
    except urllib.error.HTTPError as e:
        if _is_missing_column_error(e):
            return json_error(503, "Google Calendar sync is temporarily "
                                   "unavailable: run google_calendar_schema.sql "
                                   "in the Supabase SQL editor, then try again.")
        return json_error(502, f"Could not prepare your {WINGMAN_CALENDAR_NAME} calendar: {e}")
    except Exception as e:
        return json_error(502, f"Could not prepare your {WINGMAN_CALENDAR_NAME} calendar: {e}")

    # Marker -> existing event id, so a client that has lost its googleEventId PATCHes the
    # event it already wrote instead of creating a second one. Without this, anything that
    # rebuilds the stored dates (notably "Check for updates") produced a duplicate on the
    # student's real calendar on every sync — and the sweep could not clean them up, because
    # the old event's marker is still in the tracked set, so it is not stale by definition.
    # Belt and braces with the client-side fix that stopped dropping the id in the first
    # place: this also covers two devices syncing at once, and an item removed and re-added.
    existing_by_marker, duplicate_event_ids = {}, []
    if events:
        try:
            existing_by_marker, duplicate_event_ids = _existing_event_map(
                access_token, f"calendars/{urllib.parse.quote(calendar_id)}")
        except Exception as e:
            # Non-fatal: fall back to the old create-if-no-id behaviour rather than failing
            # a sync over a listing hiccup.
            print(f"[WARN] Could not map existing calendar events: {e}")

    results = []
    calendar_html_link = ""
    for event in events:
        item_id = event.get("id")
        title = (event.get("title") or "").strip()
        date_iso = (event.get("dateISO") or "").strip()
        description = event.get("description") or ""
        google_event_id = event.get("googleEventId")
        if not item_id or not title or not date_iso:
            results.append({"id": item_id, "status": "error", "error": "Missing id, title, or dateISO."})
            continue

        year, month, day = date_iso.split("-")
        end_obj = (datetime.date(int(year), int(month), int(day)) + datetime.timedelta(days=1))
        body_payload = {
            "summary": title,
            "description": description,
            "start": {"date": date_iso},
            "end": {"date": end_obj.isoformat()},
            # Stamped so the sweep below can tell an event WE wrote from one the student
            # added to this calendar by hand. Without it a sweep is "delete everything
            # not currently tracked", which would eat their own entries.
            "extendedProperties": {"private": {WINGMAN_EVENT_PROP: item_id}},
        }
        # Trust the client's id when it has one; otherwise adopt whatever we already wrote
        # under this marker rather than creating a parallel event.
        if not google_event_id:
            google_event_id = existing_by_marker.get(item_id)

        try:
            calendar_path = f"calendars/{urllib.parse.quote(calendar_id)}"
            if google_event_id:
                url = f"{GOOGLE_CALENDAR_API_BASE}/{calendar_path}/events/{google_event_id}"
                method = "PATCH"
            else:
                url = f"{GOOGLE_CALENDAR_API_BASE}/{calendar_path}/events"
                method = "POST"
            req = urllib.request.Request(
                url,
                data=json.dumps(body_payload).encode(),
                method=method,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                created = json.loads(resp.read())
            if not calendar_html_link:
                # An event's htmlLink opens Google Calendar focused on THIS calendar, which
                # is a far more reliable "show me" link than trying to build a calendar URL
                # out of a secondary calendar's id.
                calendar_html_link = created.get("htmlLink") or ""
            results.append({"id": item_id, "status": "ok", "googleEventId": created.get("id")})
        except urllib.error.HTTPError as e:
            # A previously-synced event deleted on Google's side 404s on PATCH — fall
            # back to creating a fresh one rather than failing the sync.
            if google_event_id and e.code == 404:
                try:
                    req = urllib.request.Request(
                        f"{GOOGLE_CALENDAR_API_BASE}/{calendar_path}/events",
                        data=json.dumps(body_payload).encode(),
                        method="POST",
                        headers={
                            "Authorization": f"Bearer {access_token}",
                            "Content-Type": "application/json",
                        },
                    )
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        created = json.loads(resp.read())
                    results.append({"id": item_id, "status": "ok", "googleEventId": created.get("id")})
                    continue
                except Exception as e2:
                    results.append({"id": item_id, "status": "error", "error": str(e2)})
                    continue
            results.append({"id": item_id, "status": "error", "error": f"Google API error {e.code}"})
        except Exception as e:
            results.append({"id": item_id, "status": "error", "error": str(e)})

    # Remove duplicates left behind by earlier syncs. Bounded to events carrying OUR marker
    # where the same marker appears twice, i.e. two calendar entries for one date slot, and
    # reported rather than done silently. The sweep cannot do this itself: those markers are
    # still tracked, so by its definition they are not stale.
    deduped, dedupe_errors = 0, []
    if duplicate_event_ids:
        deduped, dedupe_errors = _delete_events(
            access_token, f"calendars/{urllib.parse.quote(calendar_id)}", duplicate_event_ids)

    deleted = 0
    sweep_errors = []
    if sweep:
        # Keep only what the client just told us is tracked. An event whose upsert errored
        # is kept too — its marker is still what the client sent, and deleting a deadline
        # because a transient write failed is the wrong direction to fail in.
        keep_ids = {
            (e.get("id") or "").strip()
            for e in events
            if isinstance(e, dict) and (e.get("id") or "").strip()
        }
        deleted, sweep_errors = _sweep_stale_events(
            access_token, f"calendars/{urllib.parse.quote(calendar_id)}", keep_ids
        )

    # Tell the client WHERE the events went. Without this the app says "synced", the student
    # looks at their PRIMARY calendar, sees nothing, and reasonably concludes it is broken —
    # which is exactly what happened. Events can only ever land on this app-created calendar.
    return json_response(200, {
        "ok": True,
        "results": results,
        "deleted": deleted,
        "deduped": deduped,
        "sweepErrors": sweep_errors + dedupe_errors,
        "calendarName": WINGMAN_CALENDAR_NAME,
        "calendarLink": calendar_html_link,
    })

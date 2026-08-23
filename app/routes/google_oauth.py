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
from app.deps import read_json_body, json_response, json_error, login_response
from app.auth import get_current_user, AuthedUser
from app.auth.tokens import verify_access_token, AuthError, AuthConfigError
from app.services import google_oauth as g

router = APIRouter()


def _host(request):
    return request.headers.get("Host", f"localhost:{PORT}")


def _redirect_uri(request, path):
    host = _host(request)
    scheme = "http" if host.startswith("localhost") or host.startswith("127.0.0.1") else "https"
    return f"{scheme}://{host}{path}"


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
async def handle_google_finish(request: Request):
    body = await read_json_body(request)
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
    g._google_calendar_states[state] = {"userid": userid, "expires_at": time.time() + GOOGLE_TOKEN_TTL_SECONDS}

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

    return _redirect_home("?calendar_connected=1")


@router.post("/api/calendar/sync")
async def handle_calendar_sync(request: Request, user: AuthedUser = Depends(get_current_user)):
    body = await read_json_body(request)
    userid = user.id
    events = body.get("events") or []
    if not isinstance(events, list) or not events:
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

    results = []
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
        }
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

    return json_response(200, {"ok": True, "results": results})

"""FastAPI application for the Highschool Wingman web service.

Phase 1 of the rearchitecture (PLAN_1_decompose.md): this replaces the hand-rolled
http.server monolith (server.py) with domain routers, while serving the existing
static SPA unchanged. Run locally or on Render with:

    uvicorn app.main:app --host 0.0.0.0 --port 8000

The admin/ops console (/admin, /api/agents/*, /api/seeds) is NOT part of this app: it
is mounted only when WINGMAN_ENABLE_OPS is set (local dev), so the shipped service
exposes no agent/seed/admin route. Importing app.config (via the routers) loads .env.
"""
import os
import re

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import GEMINI_API_KEY, ANTHROPIC_API_KEY
from app.core import record_api_error
from app.routes import (
    ai, opportunities, account, user_data, google_oauth, mailing_list,
    subscription, resume, auth, email, events, matching,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

app = FastAPI(title="Highschool Wingman", docs_url=None, redoc_url=None, openapi_url=None)

# ---------------- CORS ----------------
# Phase 3 (PLAN_3_rn.md): the Expo/RN-web client is a SEPARATE ORIGIN from this API — in dev
# (Metro on :8081 -> API on :8000) and in prod (Render Static Site -> Render Web Service) —
# so the browser needs CORS to call it. Auth is a Bearer header, not a cookie, so credentials
# mode is off and a "*" origin is safe (nothing rides on the ambient session). Set
# CORS_ALLOW_ORIGINS (comma-separated) to lock this to the static-site origin in production.
_cors_origins = os.environ.get("CORS_ALLOW_ORIGINS", "*")
_allow_origins = ["*"] if _cors_origins.strip() == "*" else [
    o.strip() for o in _cors_origins.split(",") if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# `expo export -p web` content-hashes every file it writes under these two prefixes
# (`SpaceGrotesk_700Bold.52e5e29a....ttf`, `entry-<hash>.js`), so their URL changes
# whenever their bytes do and they can be cached forever. The hash is required, not
# assumed: `_expo/.routes.json` lives under the same root without one, and handing a
# year-long cache to an unhashed file is unrecoverable. Both separators are real — assets
# use `.<hash>.` and the JS bundle uses `-<hash>.` — and the trailing class admits `@2x`.
_IMMUTABLE_PREFIXES = ("/assets/", "/_expo/static/")
_CONTENT_HASH = re.compile(r"[.\-][0-9a-f]{32}[.@]")


def _is_immutable_asset(path: str) -> bool:
    return path.startswith(_IMMUTABLE_PREFIXES) and bool(_CONTENT_HASH.search(path))


@app.middleware("http")
async def no_cache(request: Request, call_next):
    """No HTTP caching on API JSON or on any HTML shell, matching the old
    Handler.end_headers — without it Chrome can silently serve a stale app shell, and
    the shell is what names the current bundle hash.

    Content-hashed build output is the deliberate exception, and it is not merely an
    optimisation: `no-store` forbids the browser from KEEPING the response, so the
    `<link rel="preload">` tags expo writes for the seven webfonts were paying for a
    full download that could not be reused — the @font-face fetch started over from
    scratch once the 1.9MB bundle had evaluated. Measured on production 2026-08-24:
    fonts preloaded by 566ms, re-downloaded 1234ms->1466ms, i.e. the app painted its
    first text in a fallback face and swapped ~400ms later, on EVERY visit, because
    nothing was ever cached across loads either. That flash is what this fixes."""
    response = await call_next(request)
    if _is_immutable_asset(request.url.path):
        # Only Cache-Control is set here: nothing downstream emits Pragma/Expires, and
        # Starlette's MutableHeaders has no .pop, so there is nothing to unset either.
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    else:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.middleware("http")
async def capture_api_errors(request: Request, call_next):
    """Record server-side failures to api_errors so the admin console's API Errors tab can show
    what the live service is breaking on — see app.core.record_api_error / api_errors_schema.sql.

    Two things are captured, and nothing else (a 4xx is a client mistake, not a service fault):
      * an UNHANDLED exception — recorded with its type and full traceback, which is what makes
        a crash actionable, then turned into the app's {"error": ...} 5xx shape instead of the
        framework's bare "Internal Server Error" text;
      * any 5xx RESPONSE a route returned deliberately (json_error(502) when Supabase is down,
        a raised HTTPException(503) the exception handler below already converted to a response)
        — recorded with method/path/status. Its body is not read back: reading a streamed
        response body in middleware is fragile, and for these the endpoint + status is the
        signal. Handled 5xx keep their normal flow (and their CORS headers); only the recorder
        is added.

    Recording is fail-open: record_api_error never raises, so a wedged log can never be the
    reason a request fails. This middleware is registered last, so it is OUTERMOST and sees
    exceptions from every inner layer."""
    method = request.method
    path = request.url.path  # query string dropped on purpose: it can carry PII
    try:
        response = await call_next(request)
    except Exception as exc:  # noqa: BLE001 - truly unhandled: log it, don't let it escape silently
        import traceback as _tb
        record_api_error(method, path, 500, type(exc).__name__, str(exc),
                         traceback_text=_tb.format_exc())
        return JSONResponse(status_code=500, content={"error": "Internal server error."},
                            headers={"Cache-Control": "no-store"})
    # A route that already recorded its own failure (the AI proxies, with the provider's real
    # status and error message) tags the response so it is not ALSO logged generically here.
    already_logged = "x-wingman-error-logged" in response.headers
    if already_logged:
        del response.headers["x-wingman-error-logged"]        # internal marker, never shipped
    if response.status_code >= 500 and not already_logged:
        record_api_error(method, path, response.status_code, "server_error")
    return response


@app.exception_handler(StarletteHTTPException)
async def http_exception_as_error(request: Request, exc: StarletteHTTPException):
    """Render HTTPException as {"error": ...} to match the app's wire format (json_error).

    The auth dependencies raise HTTPException(401/503); without this they'd come back as
    FastAPI's default {"detail": ...}, and the client parses errors as `data.error`. The
    static 404 below also flows through here, which is fine — its body is never read."""
    detail = exc.detail if isinstance(exc.detail, str) else "Request failed."
    return JSONResponse(status_code=exc.status_code, content={"error": detail},
                        headers=getattr(exc, "headers", None))


# ---------------- Public API routers ----------------
for module in (ai, opportunities, account, user_data, google_oauth, mailing_list,
               subscription, resume, auth, email, events, matching):
    app.include_router(module.router)


# ---------------- Local-only ops console (never shipped) ----------------
if os.environ.get("WINGMAN_ENABLE_OPS"):
    # Imported lazily so the shipped web service never even imports ops/ (which pulls in
    # agent-orchestration + subprocess code). Every route inside is localhost-gated too.
    from ops.admin import router as ops_router
    app.include_router(ops_router)
    print("[ops] Admin console ENABLED at /admin and /api/agents/* (localhost only)")


# ---------------- Static pages (repo root) + optional web-app bundle ----------------
# The old vanilla-JS SPA was retired at tag `workingwithauth` (Phase 3 cutover): the web
# frontend is now the Expo app in frontend/. The repo-root route survives ONLY to serve
# the static pages the app still links to on this host — terms.html / privacy.html /
# about.html plus the styles.css + favicon.svg they use — with the same deny-list so the
# service can't hand out source, secrets, or logs.
#
# SERVE_WEB_DIST=1 (set on Render, never locally) additionally serves the exported Expo
# web bundle from frontend/dist at the root, so the custom domain keeps serving the app
# from the same origin as the API. It is deliberately opt-in: a stale local dist/ served
# silently at :8000 would shadow the Metro dev server and confuse every local repro.
_DENY_EXT = {".py", ".pyc", ".pyo", ".log", ".sql", ".ps1", ".md", ".txt", ".sh"}
_DENY_NAMES = {".env", "agent_settings.json"}

# Where the web app lives when this service does NOT serve it itself. When set (e.g. a
# separate Static Site origin), a browser hitting the root is redirected there; otherwise
# a plain JSON status answers. Ignored when SERVE_WEB_DIST is on.
WEB_APP_URL = os.environ.get("WEB_APP_URL", "").strip()

SERVE_WEB_DIST = bool(os.environ.get("SERVE_WEB_DIST"))
WEB_DIST_ROOT = os.path.join(REPO_ROOT, "frontend", "dist")


def _resolve_dist(rel: str):
    """Map a request path to a file in frontend/dist (expo export -p web output).

    Matches the exact file, or the route's exported HTML (expo-router writes
    tracker.html, finder.html, ...). Returns None otherwise — the SPA index.html
    fallback is applied by serve_static AFTER the repo-root pages get their chance,
    so terms/privacy/about are never shadowed by the app shell. Same traversal
    guards as the repo-root resolver; dist holds only build output, so no
    extension deny-list is needed.
    """
    if not SERVE_WEB_DIST or not os.path.isdir(WEB_DIST_ROOT):
        return None
    rel = rel.strip("/")
    if not rel:
        return _dist_index()
    parts = [p for p in rel.split("/") if p]
    if any(p.startswith("..") for p in parts):
        return None
    candidate = os.path.normpath(os.path.join(WEB_DIST_ROOT, *parts))
    if candidate != WEB_DIST_ROOT and not candidate.startswith(WEB_DIST_ROOT + os.sep):
        return None
    if os.path.isfile(candidate):
        return candidate
    if os.path.isfile(candidate + ".html"):
        return candidate + ".html"
    return None


def _dist_index():
    if not SERVE_WEB_DIST:
        return None
    index = os.path.join(WEB_DIST_ROOT, "index.html")
    return index if os.path.isfile(index) else None


def _resolve_static(rel: str):
    rel = rel.strip("/")
    if not rel:
        return None  # the root is handled by serve_static, not by a file
    # Reject dotfiles/dotdirs (.env, .git, ...), agent logs, and traversal.
    parts = rel.split("/")
    if any(p.startswith(".") for p in parts) or "agent_logs" in parts:
        return None
    candidate = os.path.normpath(os.path.join(REPO_ROOT, rel))
    if candidate != REPO_ROOT and not candidate.startswith(REPO_ROOT + os.sep):
        return None
    base = os.path.basename(candidate).lower()
    _, ext = os.path.splitext(base)
    if ext in _DENY_EXT or base in _DENY_NAMES:
        return None
    return candidate if os.path.isfile(candidate) else None


# The walkthrough film's player persists its playhead in localStorage ('animstage-v3:t')
# and the composition is authored to play exactly once — so a reload restores
# time === duration and holds the final frame instead of playing. The landing page
# clears that key from the parent before mounting the iframe, but only when the two are
# same-origin (production); in dev Metro (:8081) and this API (:8000) differ, so the
# clear silently no-ops and "See how it works" replays nothing. Injecting the clear into
# the served document runs it in the film's OWN origin, synchronously before the bundle
# script builds its initial state, so every mount starts at 0:00 regardless of who the
# parent is (or whether there is one — the native handoff opens this URL directly). The
# vendored file on disk is never edited: it is a re-export-only artifact (see CLAUDE.md).
_WALKTHROUGH_PLAYHEAD_RESET = (
    b"<head>\n  <script>try{localStorage.removeItem('animstage-v3:t')}catch(e){}</script>"
)


@app.get("/{full_path:path}")
def serve_static(full_path: str):
    # 1) The web-app bundle (exact file or exported route html), when enabled.
    resolved = _resolve_dist(full_path)
    # 2) The surviving repo-root pages (terms/privacy/about + their assets).
    if resolved is None:
        resolved = _resolve_static(full_path)
    # 3) SPA fallback for client-side routes / deep links, mirroring the Static
    #    Site's rewrite-everything-to-index behavior.
    if resolved is None:
        resolved = _dist_index()
    if resolved is not None:
        if os.path.basename(resolved).lower() == "walkthrough.html":
            with open(resolved, "rb") as f:
                html = f.read()
            return HTMLResponse(html.replace(b"<head>", _WALKTHROUGH_PLAYHEAD_RESET, 1))
        return FileResponse(resolved)
    if not full_path.strip("/"):
        if WEB_APP_URL:
            return RedirectResponse(WEB_APP_URL, status_code=307)
        return JSONResponse({"ok": True, "service": "wingman-api",
                             "note": "The web app is served separately; set WEB_APP_URL to redirect here."})
    raise HTTPException(status_code=404)


if __name__ != "__main__":
    _g = "LIVE" if GEMINI_API_KEY else "MOCK"
    _c = "LIVE" if ANTHROPIC_API_KEY else "MOCK"
    print(f"[app] Highschool Wingman FastAPI ready [messages: {_g}] [messages-claude: {_c}]")

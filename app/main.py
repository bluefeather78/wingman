"""FastAPI application for the Highschool Wingman web service.

Phase 1 of the rearchitecture (docs/archive/PLAN_1_decompose.md): this replaces the hand-rolled
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
# Phase 3 (docs/archive/PLAN_3_rn.md): the Expo/RN-web client is a SEPARATE ORIGIN from this API — in dev
# (Metro on :8081 -> API on :8000) and in prod (Render Static Site -> Render Web Service) —
# so the browser needs CORS to call it. Auth is a Bearer header, not a cookie, so credentials
# mode is off and nothing rides on the ambient session.
#
# S1-5, finding M11: the default was "*" and render.yaml never set CORS_ALLOW_ORIGINS, so
# production shipped wide open. Not exploitable on its own — with allow_credentials=False a
# cross-origin read still needs the caller's own bearer — but there is no reason for it in
# production, where the app and the API share one origin and the browser sends no preflight
# at all. So: on Render, "*" means the exact app origins; everywhere else it still means "*",
# because a dev machine's origins are not knowable here and a CORS rule that breaks
# `expo start` gets deleted rather than fixed.
# RENDER_EXTERNAL_URL is set by Render to this service's own public URL. Including it means
# the allow-list configures itself for whatever the service is actually reachable at — the
# `*.onrender.com` hostname before a custom domain is pointed at it, a preview deploy, a
# renamed service. Without it, hard-coding the custom domain would silently break a
# cross-origin caller the day the URL differs from what was guessed here, and CORS failures
# read as "the app is broken" rather than as a config problem.
_DEFAULT_PROD_ORIGINS = ["https://highschoolwingman.com",
                         "https://www.highschoolwingman.com"]
_render_url = (os.environ.get("RENDER_EXTERNAL_URL") or "").strip().rstrip("/")
if _render_url:
    _DEFAULT_PROD_ORIGINS.append(_render_url)

_cors_origins = os.environ.get("CORS_ALLOW_ORIGINS", "").strip()
if _cors_origins:
    _allow_origins = ["*"] if _cors_origins == "*" else [
        o.strip() for o in _cors_origins.split(",") if o.strip()
    ]
elif os.environ.get("RENDER"):
    _allow_origins = _DEFAULT_PROD_ORIGINS
else:
    _allow_origins = ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------- Security headers (S1-5, finding M11) ----------------
#
# There were none. The only middleware on this app added cache-control.
#
# PURE ASGI, not BaseHTTPMiddleware: the perf report already flags the existing
# BaseHTTPMiddleware-based one (each adds a task group and re-wraps the response stream),
# and adding a second of the same kind compounds it. This one mutates the header list in
# the http.response.start message and touches nothing else.
_SECURITY_HEADERS = [
    # 2 years, the value HSTS preload requires. Only ever sent over https — sending it on a
    # plain-http dev response would pin localhost to https in the developer's browser, which
    # is both wrong and irritatingly persistent.
    (b"strict-transport-security", b"max-age=63072000; includeSubDomains"),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    # Nothing here is meant to be framed. walkthrough.html is the exception and is handled
    # below — the landing page iframes it.
    (b"x-frame-options", b"DENY"),
    # No API in this app needs a camera, a microphone, or a location.
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=()"),
]

# The landing page iframes public/walkthrough.html, so it gets 'self' rather than DENY —
# a blanket DENY would blank the film on the landing page.
_FRAMEABLE_PATHS = ("/walkthrough.html",)

# CSP, REPORT-ONLY to start with. `expo export` inlines @font-face rules and preload tags
# into the document head, and the exported bundle is what has to be measured against — so
# this ships observing rather than enforcing, exactly as the plan asks. Read the reports off
# a real exported bundle, then flip CSP_ENFORCE=1.
#
#   'unsafe-inline' for style: expo's inlined @font-face and the RN-web style injector.
#   'unsafe-inline' for script: the walkthrough's playhead-reset script this file injects.
#   data: for img/font: the design system's inlined SVG icons.
#   connect-src 'self' https: — the API is same-origin in production, but dev runs Metro on
#     :8081 against the API on :8000, and a policy that breaks dev gets deleted.
_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' data: https://fonts.gstatic.com; "
    "img-src 'self' data: blob: https:; "
    "connect-src 'self' https:; "
    "media-src 'self' data: blob:; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'self'"
)
_CSP_ENFORCE = (os.environ.get("CSP_ENFORCE", "").strip().lower()
                in ("1", "true", "yes"))
_CSP_HEADER = (b"content-security-policy" if _CSP_ENFORCE
               else b"content-security-policy-report-only")


class SecurityHeaders:
    """Pure-ASGI middleware adding the response headers above."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        # request.url.scheme is the INTERNAL hop's scheme behind Render's proxy, so
        # x-forwarded-proto is what actually says whether the student is on https.
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        proto = (headers.get(b"x-forwarded-proto") or b"").decode().split(",")[0].strip()
        is_https = proto == "https" or scope.get("scheme") == "https"

        async def _send(message):
            if message["type"] == "http.response.start":
                out = message.setdefault("headers", [])
                existing = {k.lower() for k, _ in out}
                for name, value in _SECURITY_HEADERS:
                    if name in existing:
                        continue
                    if name == b"strict-transport-security" and not is_https:
                        continue
                    if name == b"x-frame-options" and path in _FRAMEABLE_PATHS:
                        out.append((name, b"SAMEORIGIN"))
                        continue
                    out.append((name, value))
                if _CSP_HEADER not in existing and b"content-security-policy" not in existing:
                    out.append((_CSP_HEADER, _CSP.encode()))
            await send(message)

        await self.app(scope, receive, _send)


app.add_middleware(SecurityHeaders)


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


# S0-7 asks for this explicitly: "verify empirically by logging client_ip() once from a real
# request before relying on it". --forwarded-allow-ips is set in render.yaml but Render's
# proxy behaviour cannot be confirmed from here — so the first real request prints what the
# app resolved and what the header actually said, once, and the operator reads it off the
# Render log. A resolved address that is still 10.x means the trusted list does not cover
# Render's LB and the rate limiters are still sharing one bucket.
_client_ip_logged = False


@app.middleware("http")
async def log_first_client_ip(request: Request, call_next):
    global _client_ip_logged
    if not _client_ip_logged:
        _client_ip_logged = True
        peer = request.client.host if request.client else "(none)"
        xff = request.headers.get("x-forwarded-for") or "(absent)"
        print(f"[client-ip] resolved={peer} x-forwarded-for={xff!r} path={request.url.path}")
    return await call_next(request)


@app.middleware("http")
async def capture_api_errors(request: Request, call_next):
    """Record server-side failures to api_errors so the admin console's API Errors tab can show
    what the live service is breaking on — see app.core.record_api_error / db/api_errors_schema.sql.

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
# The RENDER check is a HARD refusal, not a convention (S1-8). server.py already declines to
# SET WINGMAN_ENABLE_OPS there, but "the shim does not turn it on" is not the same guarantee
# as "it cannot come up": a stray env var in the Render dashboard, or a start command that
# ever changes, would be enough. What is behind these routes is subprocess launches that
# spend real money, a roster with the names and emails of minors, catalog activation, and
# test email sends to arbitrary addresses — so the mount itself refuses.
if os.environ.get("WINGMAN_ENABLE_OPS") and os.environ.get("RENDER"):
    print("[ops] WINGMAN_ENABLE_OPS is set but RENDER is too — REFUSING to mount the ops "
          "console. It is local-only by design; unset one of the two.")
elif os.environ.get("WINGMAN_ENABLE_OPS"):
    # Imported lazily so the shipped web service never even imports ops/ (which pulls in
    # agent-orchestration + subprocess code). Every route inside is localhost-gated AND
    # requires WINGMAN_OPS_TOKEN in a header.
    from ops.admin import router as ops_router
    app.include_router(ops_router)
    print("[ops] Admin console ENABLED at /admin and /api/agents/* (localhost + ops token)")


# ---------------- Static pages (repo root) + optional web-app bundle ----------------
# The old vanilla-JS SPA was retired at tag `workingwithauth` (Phase 3 cutover): the web
# frontend is now the Expo app in frontend/. The repo-root route survives ONLY to serve
# the static pages the app still links to on this host — public/terms.html / public/privacy.html /
# public/about.html plus the public/styles.css + public/favicon.svg they use — with the same deny-list so the
# service can't hand out source, secrets, or logs.
#
# SERVE_WEB_DIST=1 (set on Render, never locally) additionally serves the exported Expo
# web bundle from frontend/dist at the root, so the custom domain keeps serving the app
# from the same origin as the API. It is deliberately opt-in: a stale local dist/ served
# silently at :8000 would shadow the Metro dev server and confuse every local repro.
_DENY_EXT = {".py", ".pyc", ".pyo", ".log", ".sql", ".ps1", ".md", ".txt", ".sh"}
_DENY_NAMES = {".env", "agent_settings.json"}

# Directories under the repo root that this route must never reach into. The extension
# deny-list above is by FILE TYPE and misses whole categories: .json, .xlsx and .docx are
# not on it, so before the 2026-09-04 tidy-up `GET /Opportunities.xlsx`,
# `/opportunities.json` and `/test_resume.docx` were all publicly downloadable from
# production. Relocating them into data/ does NOT fix that on its own — _resolve_static
# joins any relative path under REPO_ROOT and only dotdirs were blocked — so the
# directories are named here as well. This is a targeted patch, not the fix: the route is
# still deny-list-shaped, and PRODUCTION_READINESS_PLAN.md High #5 ("catch-all static
# route serves the repo") wants an ALLOW-list of the handful of pages this exists for.
_DENY_DIRS = {"agent_logs", "data", "db", "docs", "tests", "eval", "legal", "frontend", "scripts"}

# Where the web app lives when this service does NOT serve it itself. When set (e.g. a
# separate Static Site origin), a browser hitting the root is redirected there; otherwise
# a plain JSON status answers. Ignored when SERVE_WEB_DIST is on.
WEB_APP_URL = os.environ.get("WEB_APP_URL", "").strip()

SERVE_WEB_DIST = bool(os.environ.get("SERVE_WEB_DIST"))
# Everything this route is allowed to serve, and the ONLY directory it looks in.
# It used to resolve against REPO_ROOT and defend with the extension/name/dir deny-lists
# below -- i.e. every file in the repo was reachable unless something remembered to
# exclude it, which is PRODUCTION_READINESS_PLAN.md High #5 ("catch-all static route
# serves the repo"). Measured before the change: GET /logic_map.html returned 200 in
# production, publishing the ops console's internal pipeline map. Resolving inside
# public/ inverts the default -- a file is served because it was PUT there, and the
# deny-lists are now belt-and-braces rather than the only thing standing in the way.
#
# URLs are unchanged: `rel` is the request path and this is only where we look for it,
# so /terms.html still serves (from public/terms.html). That matters because /terms.html
# and /privacy.html are in lifecycle emails already delivered, and the pages reference
# public/styles.css / public/favicon.svg with RELATIVE hrefs, which the browser resolves to /styles.css.
PUBLIC_DIR = os.path.join(REPO_ROOT, "public")

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
    if any(p.startswith(".") for p in parts) or _DENY_DIRS.intersection(parts):
        return None
    candidate = os.path.normpath(os.path.join(PUBLIC_DIR, rel))
    if candidate != PUBLIC_DIR and not candidate.startswith(PUBLIC_DIR + os.sep):
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

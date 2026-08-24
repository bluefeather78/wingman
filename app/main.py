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

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import GEMINI_API_KEY, ANTHROPIC_API_KEY
from app.routes import (
    ai, opportunities, account, user_data, google_oauth, mailing_list,
    subscription, resume, auth, email,
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


@app.middleware("http")
async def no_cache(request: Request, call_next):
    """Disable HTTP caching on every response (static files AND API JSON), matching the
    old Handler.end_headers. Without this Chrome can silently serve a stale script.js."""
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
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
               subscription, resume, auth, email):
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

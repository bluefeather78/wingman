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
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import GEMINI_API_KEY, ANTHROPIC_API_KEY
from app.routes import (
    ai, opportunities, account, user_data, google_oauth, mailing_list,
    subscription, resume, auth,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

app = FastAPI(title="Highschool Wingman", docs_url=None, redoc_url=None, openapi_url=None)


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
               subscription, resume, auth):
    app.include_router(module.router)


# ---------------- Local-only ops console (never shipped) ----------------
if os.environ.get("WINGMAN_ENABLE_OPS"):
    # Imported lazily so the shipped web service never even imports ops/ (which pulls in
    # agent-orchestration + subprocess code). Every route inside is localhost-gated too.
    from ops.admin import router as ops_router
    app.include_router(ops_router)
    print("[ops] Admin console ENABLED at /admin and /api/agents/* (localhost only)")


# ---------------- Static SPA (repo root) ----------------
# The old SimpleHTTPRequestHandler served the whole repo root. We keep serving the SPA
# assets from there (index.html/script.js/styles.css/*.html + images/fonts) but refuse
# source, secrets, and logs so the shipped service can't hand those out. Phase 3 moves
# the frontend to a Render Static Site and this block goes away.
_DENY_EXT = {".py", ".pyc", ".pyo", ".log", ".sql", ".ps1", ".md", ".txt", ".sh"}
_DENY_NAMES = {".env", "agent_settings.json"}


def _resolve_static(rel: str):
    rel = rel.strip("/") or "index.html"
    # Reject dotfiles/dotdirs (.env, .git, ...), agent logs, and traversal.
    parts = rel.split("/")
    if any(p.startswith(".") for p in parts) or "agent_logs" in parts:
        return None
    candidate = os.path.normpath(os.path.join(REPO_ROOT, rel))
    if candidate != REPO_ROOT and not candidate.startswith(REPO_ROOT + os.sep):
        return None
    if os.path.isdir(candidate):
        candidate = os.path.join(candidate, "index.html")
    base = os.path.basename(candidate).lower()
    _, ext = os.path.splitext(base)
    if ext in _DENY_EXT or base in _DENY_NAMES:
        return None
    return candidate if os.path.isfile(candidate) else None


@app.get("/{full_path:path}")
def serve_static(full_path: str):
    resolved = _resolve_static(full_path)
    if resolved is None:
        raise HTTPException(status_code=404)
    return FileResponse(resolved)


if __name__ != "__main__":
    _g = "LIVE" if GEMINI_API_KEY else "MOCK"
    _c = "LIVE" if ANTHROPIC_API_KEY else "MOCK"
    print(f"[app] Highschool Wingman FastAPI ready [messages: {_g}] [messages-claude: {_c}]")

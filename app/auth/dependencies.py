"""FastAPI auth dependencies (PLAN_2_auth.md).

Two dependencies, matching the plan's two route classes:

  get_current_user  — owned-data routes. Requires a valid access token; a missing/invalid/
                      expired one is a hard 401. The route reads user.id and IGNORES any
                      userid in the body: that is what closes the IDOR.
  get_optional_user — attribution-only routes (the AI proxies). Returns the token's userid
                      when a valid token is present, else None. NEVER 401s, so mock mode
                      and legitimately signed-out usage keep working.

A 503 (not a 401) is raised when JWT_SECRET is unconfigured, so a deployment problem doesn't
masquerade as "your session is invalid" and bounce every user to the login screen.
"""
from dataclasses import dataclass

from fastapi import Request, HTTPException

from app.auth.tokens import verify_access_token, AuthError, AuthConfigError


@dataclass
class AuthedUser:
    """The verified identity a protected route acts as. `id` is the lowercased userid."""
    id: str


def _bearer_token(request: Request):
    header = request.headers.get("Authorization") or ""
    if header[:7].lower() == "bearer ":
        return header[7:].strip()
    return None


async def get_current_user(request: Request) -> AuthedUser:
    token = _bearer_token(request)
    try:
        userid = verify_access_token(token)
    except AuthConfigError:
        raise HTTPException(status_code=503,
                            detail="Authentication is temporarily unavailable.")
    except AuthError:
        raise HTTPException(status_code=401, detail="Please sign in to continue.")
    return AuthedUser(id=userid)


async def get_optional_user(request: Request):
    token = _bearer_token(request)
    if not token:
        return None
    try:
        return AuthedUser(id=verify_access_token(token))
    except (AuthError, AuthConfigError):
        # Attribution-only caller: a bad or expired token is treated as signed-out rather
        # than as an error, so the request still runs (mock mode, public/free usage).
        return None

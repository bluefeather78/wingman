"""Session auth (Phase 2 — PLAN_2_auth.md).

Identity travels in a signed JWT, never in the request body: this is what closes the
pre-migration IDOR on the per-account data routes. The public surface:

  issue_tokens(userid, token_version)   -> mint an access+refresh pair at login
  verify_access_token / verify_refresh_token
  hash_password / verify_password       -> argon2 over the client SHA-256, with a
                                           transparent upgrade path for legacy rows
  get_current_user  (FastAPI dependency, hard 401)   -> owned-data routes
  get_optional_user (FastAPI dependency, never 401)  -> attribution-only routes
  AuthError / AuthConfigError

Nothing here imports app.core or app.routes, so it stays a leaf the rest of app/ hangs
off (app.deps composes it with the subscription gate).
"""
from app.auth.tokens import (
    issue_tokens, verify_access_token, verify_refresh_token,
    AuthError, AuthConfigError,
)
from app.auth.passwords import hash_password, verify_password, is_legacy_hash
from app.auth.dependencies import get_current_user, get_optional_user, AuthedUser

__all__ = [
    "issue_tokens", "verify_access_token", "verify_refresh_token",
    "AuthError", "AuthConfigError",
    "hash_password", "verify_password", "is_legacy_hash",
    "get_current_user", "get_optional_user", "AuthedUser",
]

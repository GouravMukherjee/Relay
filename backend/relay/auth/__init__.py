"""Authentication and multi-tenant RLS support for Relay.

Public surface:
    Claims              -- verified, normalized JWT claims (Pydantic model)
    AuthError           -- raised on verification failure (-> 401 at the gateway)
    verify_token        -- async Supabase JWT verification (RS256 JWKS + HS256 secret)
    current_claims      -- FastAPI dep: verify bearer token, bootstrap principal, publish RLS claims
    current_claims_ws   -- WebSocket variant (token from ?token=)
    current_user        -- FastAPI dep: load the authenticated User row
    current_org         -- FastAPI dep: load the authenticated Organization row
    require_role        -- RBAC dependency factory
    CURRENT_CLAIMS      -- ContextVar consumed by relay.db.base.get_session for RLS
    apply_rls_claims    -- stamp verified claims onto a DB connection (SET LOCAL request.jwt.claims)
    set_current_claims  -- publish claims onto CURRENT_CLAIMS
    privileged_scope    -- privileged (RLS-bypassing) session for workers/seed
"""

from __future__ import annotations

from relay.auth.deps import (
    current_claims,
    current_claims_ws,
    current_org,
    current_user,
    require_role,
)
from relay.auth.jwt import AuthError, Claims, verify_token
from relay.auth.rls import (
    CURRENT_CLAIMS,
    apply_rls_claims,
    clear_rls_claims,
    get_current_claims,
    privileged_scope,
    reset_current_claims,
    set_current_claims,
)

__all__ = [
    "Claims",
    "AuthError",
    "verify_token",
    "current_claims",
    "current_claims_ws",
    "current_user",
    "current_org",
    "require_role",
    "CURRENT_CLAIMS",
    "apply_rls_claims",
    "clear_rls_claims",
    "set_current_claims",
    "reset_current_claims",
    "get_current_claims",
    "privileged_scope",
]

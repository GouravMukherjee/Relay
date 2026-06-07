"""FastAPI auth dependencies: token extraction, principal resolution, RBAC.

Responsibilities
----------------
* ``current_claims`` — verify the bearer token (``Authorization: Bearer <jwt>``
  for REST, or ``?token=<jwt>`` for the WebSocket handshake), publish it on the
  :data:`relay.auth.rls.CURRENT_CLAIMS` contextvar so ``relay.db.base.get_session``
  applies RLS, and (on first sign-in) bootstrap the org + owner membership so the
  claims always carry a concrete ``org_id``.
* ``current_user`` / ``current_org`` — load the ORM principal/tenant rows.
* ``require_role(*roles)`` — RBAC guard dependency factory.

Import-cycle safety: this module imports the DB layer lazily inside the request
handlers. ``relay.db.base.get_session`` lazily imports :mod:`relay.auth.rls`
(not this module), so there is no static cycle.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Annotated, Awaitable, Callable

from fastapi import Depends, Query, Request, WebSocket
from fastapi.exceptions import HTTPException

from relay.auth.jwt import AuthError, Claims, verify_token
from relay.auth.rls import set_current_claims
from relay.config import settings

if TYPE_CHECKING:  # avoid importing ORM models at import time
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("relay.auth.deps")


# ---------------------------------------------------------------------------
# Errors (mapped to API_SPEC error bodies by the gateway exception handlers)
# ---------------------------------------------------------------------------


def _unauthorized(message: str = "invalid or missing credentials") -> HTTPException:
    return HTTPException(
        status_code=401,
        detail={"error": {"code": "unauthorized", "message": message}},
        headers={"WWW-Authenticate": "Bearer"},
    )


def _forbidden(message: str = "insufficient role") -> HTTPException:
    return HTTPException(
        status_code=403,
        detail={"error": {"code": "forbidden", "message": message}},
    )


# ---------------------------------------------------------------------------
# Token extraction
# ---------------------------------------------------------------------------


def _bearer_from_request(request: Request) -> str | None:
    header = request.headers.get("authorization") or request.headers.get(
        "Authorization"
    )
    if not header:
        return None
    parts = header.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].strip():
        return parts[1].strip()
    return None


# ---------------------------------------------------------------------------
# First sign-in bootstrap (org + owner membership)
# ---------------------------------------------------------------------------


async def _bootstrap_principal(claims: Claims) -> Claims:
    """Ensure the authenticated user has a User row, an Organization, and a
    membership. Returns claims guaranteed to carry a concrete ``org_id``.

    Runs in a privileged (RLS-bypassing) session because, on first sign-in,
    there is no ``org_id`` yet — so request-scoped RLS could not see/insert the
    bootstrap rows. All writes here are explicitly self/org scoped.

    Idempotent: re-runs converge (no duplicate org/user/membership).
    """
    # Lazy imports break the auth <-> db import cycle.
    from sqlalchemy import select

    from relay.db.base import privileged_session
    from relay.db.models import Organization, OrgMembership, User
    from relay.ids import new_id

    async with privileged_session() as session:
        user = await session.get(User, claims.user_id)

        # Resolve the org: prefer the claim, else an existing membership, else
        # create a fresh org for this user (they become its owner).
        org_id = claims.org_id

        if user is not None and not org_id:
            org_id = getattr(user, "organization_id", None)

        if not org_id:
            membership = (
                await session.execute(
                    select(OrgMembership).where(
                        OrgMembership.user_id == claims.user_id
                    )
                )
            ).scalars().first()
            if membership is not None:
                org_id = membership.organization_id

        created_org = False
        if not org_id:
            # Brand-new principal -> spin up their org.
            org = Organization(
                id=new_id_uuid(),
                name=(claims.email or "My Organization"),
            )
            session.add(org)
            org_id = org.id
            created_org = True

        if user is None:
            user = User(
                id=claims.user_id,
                organization_id=org_id,
                name=(claims.email or "User"),
                role="owner" if created_org else (claims.role or "member"),
            )
            session.add(user)
        elif not getattr(user, "organization_id", None):
            user.organization_id = org_id

        # Ensure a membership row exists (owner on a freshly created org).
        membership = (
            await session.execute(
                select(OrgMembership).where(
                    OrgMembership.user_id == claims.user_id,
                    OrgMembership.organization_id == org_id,
                )
            )
        ).scalars().first()
        if membership is None:
            session.add(
                OrgMembership(
                    id=new_id("mem"),
                    organization_id=org_id,
                    user_id=claims.user_id,
                    role="owner" if created_org else (claims.role or "member"),
                )
            )

        # commit handled by the privileged_session context manager
        resolved_role = getattr(user, "role", None) or claims.role or "member"

    return claims.model_copy(update={"org_id": str(org_id), "role": resolved_role})


def new_id_uuid() -> str:
    """Generate a UUID string for control-plane PKs (organizations/users).

    Organizations/users use bare UUIDs (users.id == Supabase ``sub``); only
    tenant entities use the prefixed scheme. Kept here so deps has no hard
    dependency on a specific ids helper for the UUID case.
    """
    import uuid

    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# current_claims (REST)
# ---------------------------------------------------------------------------


async def current_claims(request: Request) -> Claims:
    """Verify the request's bearer token, bootstrap the principal, and publish
    the claims onto the RLS contextvar for the remainder of the request.
    """
    token = _bearer_from_request(request)
    if not token:
        raise _unauthorized("missing bearer token")
    try:
        claims = await verify_token(token)
    except AuthError as exc:
        raise _unauthorized(str(exc)) from exc

    claims = await _bootstrap_principal(claims)
    # Publish for relay.db.base.get_session (RLS). The contextvar reset is left
    # to the natural request lifecycle; FastAPI runs each request in its own
    # context so values do not leak between requests.
    set_current_claims(claims)
    return claims


# ---------------------------------------------------------------------------
# current_claims (WebSocket) — token comes from ?token= query param
# ---------------------------------------------------------------------------


async def current_claims_ws(
    websocket: WebSocket,
    token: Annotated[str | None, Query()] = None,
) -> Claims:
    """WebSocket variant: verify ``?token=`` and (origin-permitting) publish claims.

    The WS router is responsible for ``websocket.accept()``/close codes; this
    dependency only verifies and bootstraps. Raises :class:`AuthError`-derived
    ``HTTPException`` (401) if the token is bad so the router can reject (1008).
    """
    if not token:
        # Some clients pass the token in a subprotocol/header; fall back there.
        token = websocket.query_params.get("token") or _bearer_from_ws_headers(
            websocket
        )
    if not token:
        raise _unauthorized("missing ws token")

    # Origin check: lock to the configured frontend origin.
    origin = websocket.headers.get("origin")
    if origin and settings.frontend_origin and origin != settings.frontend_origin:
        raise _forbidden("origin not allowed")

    try:
        claims = await verify_token(token)
    except AuthError as exc:
        raise _unauthorized(str(exc)) from exc

    claims = await _bootstrap_principal(claims)
    set_current_claims(claims)
    return claims


def _bearer_from_ws_headers(websocket: WebSocket) -> str | None:
    header = websocket.headers.get("authorization")
    if not header:
        return None
    parts = header.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].strip():
        return parts[1].strip()
    return None


# ---------------------------------------------------------------------------
# current_user / current_org
# ---------------------------------------------------------------------------


async def current_user(
    claims: Annotated[Claims, Depends(current_claims)],
):
    """Load the authenticated :class:`relay.db.models.User` row (RLS-scoped)."""
    from relay.db.base import get_session
    from relay.db.models import User

    # Use a short-lived RLS session; claims are already on the contextvar.
    agen = get_session()
    session: "AsyncSession" = await agen.__anext__()
    try:
        user = await session.get(User, claims.user_id)
        if user is None:
            raise _unauthorized("user not found")
        return user
    finally:
        await agen.aclose()


async def current_org(
    claims: Annotated[Claims, Depends(current_claims)],
):
    """Load the authenticated principal's :class:`relay.db.models.Organization`."""
    from relay.db.base import get_session
    from relay.db.models import Organization

    agen = get_session()
    session: "AsyncSession" = await agen.__anext__()
    try:
        org = await session.get(Organization, claims.org_id)
        if org is None:
            raise _unauthorized("organization not found")
        return org
    finally:
        await agen.aclose()


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------


def require_role(*roles: str) -> Callable[..., Awaitable[Claims]]:
    """Dependency factory: require the principal's role to be one of ``roles``.

    Usage::

        @router.delete("/documents/{id}", dependencies=[Depends(require_role("owner", "admin"))])
    """
    allowed = {r.lower() for r in roles}

    async def _checker(
        claims: Annotated[Claims, Depends(current_claims)],
    ) -> Claims:
        if claims.role.lower() not in allowed:
            raise _forbidden(
                f"role '{claims.role}' not in required roles {sorted(allowed)}"
            )
        return claims

    return _checker


__all__ = [
    "current_claims",
    "current_claims_ws",
    "current_user",
    "current_org",
    "require_role",
]

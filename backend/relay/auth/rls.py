"""Row-Level Security (RLS) claim propagation for Relay.

Tenant isolation is enforced at the database via Postgres RLS policies named
``org_isolation`` on every tenant table. Those policies read the current request's
JWT claims out of the ``request.jwt.claims`` GUC (a Supabase/PostgREST convention):

    USING (organization_id = (current_setting('request.jwt.claims', true)::json ->> 'org_id')::uuid)

This module is the single place that sets that GUC on a connection. Per request,
``relay.db.base.get_session`` reads the verified :data:`CURRENT_CLAIMS` contextvar and
calls :func:`apply_rls_claims` so the policies have the right ``org_id`` to enforce.

The privileged worker/seed scope deliberately does NOT call this — it bypasses RLS and
scopes by ``organization_id`` explicitly in queries instead.

NOTE: This module defines a small structural :class:`Claims` protocol so it has no import
dependency on :mod:`relay.auth.jwt` (which defines the concrete Pydantic ``Claims`` model
used everywhere else). Any object exposing ``user_id``, ``org_id`` and ``role`` works.
"""

from __future__ import annotations

import json
from contextvars import ContextVar
from typing import Protocol, runtime_checkable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession


@runtime_checkable
class Claims(Protocol):
    """Structural type for verified JWT claims.

    The concrete implementation is :class:`relay.auth.jwt.Claims` (a Pydantic model).
    Defined as a Protocol here purely to avoid an import cycle with the DB layer.
    """

    user_id: str
    org_id: str
    role: str


# Set by the auth dependency (``relay.auth.deps.current_claims``) for the duration of a
# request, and read by ``relay.db.base.get_session`` to apply RLS on the per-request
# connection. Defaults to ``None`` — when no claims are present the DB dependency must
# refuse to leak data (no org_id => RLS USING clause evaluates against NULL => no rows).
CURRENT_CLAIMS: ContextVar[Claims | None] = ContextVar("CURRENT_CLAIMS", default=None)


def claims_to_jwt_json(claims: Claims) -> str:
    """Serialize verified claims into the JSON shape the RLS policies expect.

    Mirrors the PostgREST ``request.jwt.claims`` convention. We include ``sub`` (the
    Supabase auth subject == ``user_id``), ``org_id`` and ``role``. ``org_id`` is the
    one the ``org_isolation`` policies key on.
    """
    return json.dumps(
        {
            "sub": claims.user_id,
            "org_id": claims.org_id,
            "role": claims.role,
        }
    )


async def apply_rls_claims(
    conn: AsyncConnection | AsyncSession,
    claims: Claims,
) -> None:
    """Apply the verified claims to ``conn`` as a transaction-local Postgres GUC.

    Uses ``SET LOCAL`` so the setting is scoped to the current transaction and is
    automatically reset on commit/rollback — no leakage across pooled connections.

    The value is bound as a parameter (not string-interpolated) to avoid SQL injection
    via claim values. ``SET LOCAL`` does not accept bind parameters directly, so we use
    ``set_config(setting, value, is_local := true)`` which does.
    """
    payload = claims_to_jwt_json(claims)
    await conn.execute(
        text("SELECT set_config('request.jwt.claims', :claims, true)"),
        {"claims": payload},
    )


async def clear_rls_claims(conn: AsyncConnection | AsyncSession) -> None:
    """Reset the RLS GUC for this transaction (e.g. before a privileged operation)."""
    await conn.execute(
        text("SELECT set_config('request.jwt.claims', '', true)")
    )


def set_current_claims(claims: Claims | None):
    """Publish ``claims`` as the current request principal on :data:`CURRENT_CLAIMS`.

    Returns the :class:`~contextvars.Token` so callers may restore the previous
    value; ``relay.auth.deps`` relies on FastAPI's per-request context isolation
    and generally does not need to reset it manually.
    """
    return CURRENT_CLAIMS.set(claims)


def reset_current_claims(token) -> None:
    """Restore :data:`CURRENT_CLAIMS` to the value captured before ``set_current_claims``."""
    CURRENT_CLAIMS.reset(token)


def get_current_claims() -> Claims | None:
    """Return the claims published for the current context, or ``None``."""
    return CURRENT_CLAIMS.get()


def privileged_scope(org_id: str):
    """Yield a privileged (RLS-bypassing) DB session for workers/seed.

    Thin re-export of :func:`relay.db.base.privileged_session`; the ``org_id``
    is accepted for call-site clarity and forward-compat (callers MUST still
    scope their statements by ``organization_id`` explicitly — RLS is the
    request-path backstop and is not active on this session). Imported lazily to
    avoid the auth <-> db import cycle.
    """
    from relay.db.base import privileged_session

    return privileged_session()

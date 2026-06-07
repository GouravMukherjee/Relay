"""Async SQLAlchemy 2.0 engine, session maker, declarative base, and DB dependencies.

Two access modes:

1. Request scope (RLS enforced) -- :func:`get_session`. The FastAPI dependency reads the
   verified claims from :data:`relay.auth.rls.CURRENT_CLAIMS` (set by the auth dep) and
   applies them to the connection via :func:`relay.auth.rls.apply_rls_claims` BEFORE
   yielding. The Postgres ``org_isolation`` policies then transparently scope every query
   to the caller's ``org_id``. If no claims are present the GUC is left empty, so the RLS
   ``USING`` clause evaluates ``organization_id = NULL`` -> no tenant rows are visible.

2. Privileged scope (RLS bypassed) -- :func:`privileged_session`, for arq workers and the
   seed script. These connect as a role with ``BYPASSRLS`` privileges (or as the table
   owner) and MUST scope by ``organization_id`` explicitly in their queries.

The ``relay_app`` role (created by the initial migration) is the runtime app role and is
subject to RLS; it is intentionally NOLOGIN/no-bypass. The connection string in
``settings.database_url`` may be a superuser/owner for local dev — RLS is still exercised
by setting ``request.jwt.claims`` and relying on the policies; for strict enforcement in
prod, connect the request path as ``relay_app`` (which cannot bypass RLS).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from relay.config import settings

# ---------------------------------------------------------------------------
# Engine + session factory
# ---------------------------------------------------------------------------

# settings.database_url must use the asyncpg driver, e.g.
#   postgresql+asyncpg://user:pass@host:5432/relay
engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    future=True,
)

async_session_maker: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


class Base(DeclarativeBase):
    """Declarative base for all Relay ORM models."""


# ---------------------------------------------------------------------------
# Request-scoped session (RLS enforced)
# ---------------------------------------------------------------------------


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yield an :class:`AsyncSession` with RLS claims applied.

    Reads the verified claims from the :data:`relay.auth.rls.CURRENT_CLAIMS` contextvar
    (populated by the auth dependency earlier in the request) and stamps them onto the
    transaction so Postgres RLS policies can enforce tenant isolation.

    ``apply_rls_claims`` is imported lazily to avoid an import cycle
    (``relay.auth`` ultimately imports the DB layer for user/org bootstrapping).
    """
    # Lazy import to break the auth <-> db import cycle.
    from relay.auth.rls import CURRENT_CLAIMS, apply_rls_claims

    async with async_session_maker() as session:
        claims = CURRENT_CLAIMS.get()
        if claims is not None:
            # SET LOCAL via set_config(...) is transaction-scoped; binds to this
            # session's connection and resets on commit/rollback.
            await apply_rls_claims(session, claims)
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ---------------------------------------------------------------------------
# Privileged session (RLS bypassed) -- workers / seed
# ---------------------------------------------------------------------------


@asynccontextmanager
async def privileged_session() -> AsyncIterator[AsyncSession]:
    """Context manager yielding a session that does NOT apply RLS claims.

    Intended for arq ingestion workers and the seed script, which run outside a request
    and have no JWT. Callers are responsible for scoping every statement by
    ``organization_id`` explicitly (RLS is the request-path backstop, not active here).

    For true RLS bypass at the DB level the connecting role needs ``BYPASSRLS`` (or be the
    owning role); since no ``request.jwt.claims`` GUC is set here, any tenant table guarded
    by ``org_isolation`` would otherwise return zero rows under a non-bypass role.
    """
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

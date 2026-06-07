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
from sqlalchemy.pool import NullPool

from relay.config import settings

# ---------------------------------------------------------------------------
# Engine + session factory
# ---------------------------------------------------------------------------

# settings.database_url must use the asyncpg driver, e.g.
#   postgresql+asyncpg://user:pass@host:5432/relay


def _engine_connect_args(url: str) -> dict:
    """Build asyncpg ``connect_args`` for *url*.

    - Managed Postgres (Supabase et al.) requires TLS, and asyncpg does NOT infer
      it from the URL — attach a default SSL context for any non-local host.
    - The Supabase transaction pooler (pgBouncer, port 6543) does not support
      prepared statements; disable asyncpg's statement cache when targeting it.
    """
    args: dict = {}
    lowered = url.lower()
    is_local = (
        "@localhost" in lowered or "@127.0.0.1" in lowered or "@postgres:" in lowered
    )
    if not is_local:
        import ssl as _ssl

        # TLS required by managed Postgres (Supabase). Encrypt but do NOT verify the
        # CA chain — equivalent to libpq sslmode=require (the mode Supabase's own
        # connection strings use). Avoids "self-signed certificate in chain" on the
        # pooler / networks without the full CA bundle.
        ctx = _ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = _ssl.CERT_NONE
        args["ssl"] = ctx
    if ":6543" in url:  # pgBouncer transaction-pooling mode
        args["statement_cache_size"] = 0
    return args


# Connection pooling is process-dependent:
#
# - The AGENT worker runs each LiveKit job in its OWN event loop, and an asyncpg
#   connection is bound to the loop that created it — a pooled connection reused from a
#   previous job's loop raises asyncpg "got result for unknown protocol state" / panics on
#   teardown. There it MUST use NullPool (fresh connection per session, closed on exit) so
#   connections never leak across loops. The agent process opts in via RELAY_DB_NULLPOOL=1.
#
# - The GATEWAY runs in a single, long-lived uvicorn event loop, so pooling is both safe
#   and important: NullPool there means every request pays a fresh TLS handshake to the
#   remote Supabase pooler (hundreds of ms each), which crushes latency under the inbound
#   pipeline's many DB ops. The gateway uses a normal pre-pinged pool.
import os as _os

_use_nullpool = bool(_os.getenv("RELAY_DB_NULLPOOL"))
_is_postgres = settings.database_url.lower().startswith("postgresql")
_engine_kwargs: dict = {
    "future": True,
    "connect_args": _engine_connect_args(settings.database_url),
}
if _use_nullpool:
    _engine_kwargs["poolclass"] = NullPool
elif _is_postgres:
    # QueuePool tuning is Postgres-only — SQLite (tests) uses StaticPool and rejects
    # pool_size/max_overflow. NOTE: pool_pre_ping is intentionally OFF — the DB is a
    # REMOTE Supabase pooler (US-East), so a SELECT-1 pre-ping adds a full network
    # round-trip (~0.5–1s) to EVERY session checkout. The inbound pipeline opens several
    # sessions per message, so that tax stacked into multi-second stalls. pool_recycle
    # keeps connections fresh instead.
    _engine_kwargs["pool_size"] = 10
    _engine_kwargs["max_overflow"] = 20
    _engine_kwargs["pool_recycle"] = 1800     # recycle before Supabase's idle timeout

engine = create_async_engine(settings.database_url, **_engine_kwargs)

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

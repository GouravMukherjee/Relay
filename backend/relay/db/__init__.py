"""Relay data layer: async SQLAlchemy engine, session, ORM models.

Public surface:
    Base                  -- DeclarativeBase for all ORM models
    engine                -- the shared async engine
    async_session_maker   -- request-scoped session factory (RLS applied per request)
    get_session           -- FastAPI dependency yielding an RLS-scoped AsyncSession
    privileged_session    -- context manager yielding a session that BYPASSES RLS
                             (workers/seed; callers MUST scope by organization_id)
"""

from __future__ import annotations

from relay.db.base import (
    Base,
    async_session_maker,
    engine,
    get_session,
    privileged_session,
)

__all__ = [
    "Base",
    "engine",
    "async_session_maker",
    "get_session",
    "privileged_session",
]

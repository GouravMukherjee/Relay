"""Account / user / notification routes (additive endpoints).

Endpoints
---------
GET /me               Current authenticated user
GET /users            List all users in the org (Team tab)
GET /notifications    List notifications for the authenticated user

All paths are mounted under /api/v1 by create_app().
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from relay.auth.deps import current_claims, current_user
from relay.auth.jwt import Claims
from relay.db.base import get_session
from relay.db.models import Notification, User
from relay.schemas.account import (
    NotificationListResponse,
    User as UserSchema,
    UserListResponse,
    notification_to_schema,
    user_to_schema,
)

logger = logging.getLogger("relay.gateway.routes.account")

router = APIRouter(tags=["account"])


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "/me",
    response_model=UserSchema,
    summary="Return the current authenticated user",
)
async def get_me(
    user: User = Depends(current_user),
) -> UserSchema:
    return user_to_schema(user)


@router.get(
    "/users",
    response_model=UserListResponse,
    summary="List all users in the authenticated org (Team tab)",
)
async def list_users(
    claims: Claims = Depends(current_claims),
    db: AsyncSession = Depends(get_session),
) -> UserListResponse:
    result = await db.execute(
        select(User)
        .where(User.organization_id == claims.org_id)
        .order_by(User.name.asc())
    )
    users = result.scalars().all()
    return UserListResponse(users=[user_to_schema(u) for u in users])


@router.get(
    "/notifications",
    response_model=NotificationListResponse,
    summary="List notifications for the authenticated user",
)
async def list_notifications(
    claims: Claims = Depends(current_claims),
    db: AsyncSession = Depends(get_session),
) -> NotificationListResponse:
    """Return notifications scoped to this user within their org.

    The RLS ``org_isolation`` policy on notifications already filters by
    ``organization_id``; we additionally filter by ``user_id`` so that each
    user only sees their own notifications (or org-wide ones where user_id IS NULL).
    """
    result = await db.execute(
        select(Notification)
        .where(
            (Notification.user_id == claims.user_id)
            | (Notification.user_id.is_(None))
        )
        .order_by(Notification.created_at.desc())
    )
    notifications = result.scalars().all()
    return NotificationListResponse(
        notifications=[notification_to_schema(n) for n in notifications]
    )

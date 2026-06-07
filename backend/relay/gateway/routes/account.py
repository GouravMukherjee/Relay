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

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
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


class TtsRequest(BaseModel):
    """Body for the whisper-back text-to-speech endpoint."""

    text: str = Field(..., min_length=1, max_length=5000)
    voice_id: str | None = None


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


class UpdateMeRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    email: str | None = Field(None, max_length=254)


@router.patch(
    "/me",
    response_model=UserSchema,
    summary="Update the current user's profile (name / email display)",
)
async def update_me(
    body: UpdateMeRequest,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> UserSchema:
    """Patch the mutable profile fields. Updating email here changes the display
    name only — Supabase auth email requires a separate flow via the client SDK."""
    if body.name is not None:
        user.name = body.name.strip()
    if body.email is not None:
        user.email = body.email.strip() or None
    await db.flush()
    return user_to_schema(user)


@router.post(
    "/tts",
    summary="Synthesize speech for a card answer (MiniMax whisper-back)",
    responses={200: {"content": {"audio/mpeg": {}}}},
)
async def tts(
    body: TtsRequest,
    claims: Claims = Depends(current_claims),
) -> Response:
    """Return MP3 audio for *text* via MiniMax T2A. 503 if TTS is not configured."""
    try:
        from relay.adapters.minimax_tts import MinimaxTTS

        mp3 = await MinimaxTTS().synthesize(body.text, voice_id=body.voice_id)
    except RuntimeError as exc:  # missing creds
        raise HTTPException(
            status_code=503,
            detail={"error": {"code": "internal_error", "message": f"TTS unavailable: {exc}"}},
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.warning("tts failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={"error": {"code": "internal_error", "message": "TTS failed"}},
        ) from exc
    return Response(content=mp3, media_type="audio/mpeg")


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

"""Session management routes.

Endpoints
---------
POST  /sessions                        Create (start) a new session
GET   /sessions                        List all sessions for the org (Transcripts tab)
GET   /sessions/{session_id}           Get a single SessionInfo
POST  /sessions/{session_id}/end       End an active session
GET   /sessions/{session_id}/cards     Cards produced during the session
GET   /sessions/{session_id}/transcript  Utterance transcript for the session
POST  /sessions/{session_id}/livekit-token  Mint/refresh a LiveKit room token
POST  /sessions/{session_id}/reply     Send a suggested reply to the customer (Desk)

All paths are mounted under /api/v1 by create_app().
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from relay.auth.deps import current_claims
from relay.auth.jwt import Claims
from relay.db.base import get_session
from relay.db.models import Card, CardSource, Chunk, Document, Session, Utterance
from relay.ids import new_id
from relay.schemas.account import (
    LiveKitTokenResponse,
    ReplyRequest,
    ReplyResponse,
)
from relay.schemas.cards import Card as CardSchema
from relay.schemas.cards import SessionCardsResponse, card_to_schema, source_from_card_source
from relay.schemas.sessions import (
    CreateSessionRequest,
    CreateSessionResponse,
    EndSessionResponse,
    SessionInfo,
    SessionListResponse,
    TranscriptResponse,
    session_to_schema,
    utterance_to_schema,
)

logger = logging.getLogger("relay.gateway.routes.sessions")

router = APIRouter(tags=["sessions"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_session_or_404(session_id: str, db: AsyncSession) -> Session:
    row = await db.get(Session, session_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "session_not_found", "message": f"Session {session_id!r} not found"}},
        )
    return row


async def _card_count(session_id: str, db: AsyncSession) -> int:
    result = await db.execute(
        select(func.count()).select_from(Card).where(Card.session_id == session_id)
    )
    return result.scalar_one()


async def _resolve_card_schema(card: Card, db: AsyncSession) -> CardSchema:
    """Load CardSource + Chunk + Document for a Card and build the schema."""
    result = await db.execute(
        select(CardSource, Chunk, Document)
        .join(Chunk, CardSource.chunk_id == Chunk.id)
        .join(Document, Chunk.document_id == Document.id)
        .where(CardSource.card_id == card.id)
    )
    rows = result.all()
    sources = [source_from_card_source(cs, chunk, doc.title) for cs, chunk, doc in rows]
    return card_to_schema(card, sources)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "/sessions",
    status_code=201,
    response_model=CreateSessionResponse,
    summary="Create (start) a new session",
)
async def create_session(
    body: CreateSessionRequest,
    claims: Claims = Depends(current_claims),
    db: AsyncSession = Depends(get_session),
) -> CreateSessionResponse:
    session_id = new_id("ses")
    org_id = claims.org_id

    # Mint a LiveKit token if a room is provided or for live/intake modes.
    livekit_token: str | None = None
    livekit_room = body.livekit_room or f"relay-{session_id}"
    if body.mode in ("live", "intake"):
        try:
            from relay.adapters.livekit_tokens import ensure_room, mint_livekit_token

            # Stamp session context onto the room so the agent worker can read
            # org_id / mode / customer_id from room metadata (NOT from client msgs).
            try:
                await ensure_room(
                    livekit_room,
                    {
                        "session_id": session_id,
                        "org_id": str(org_id),
                        "mode": body.mode,
                        "customer_id": body.customer_id or "",
                    },
                )
            except Exception as exc:  # noqa: BLE001 — best-effort; never block session start
                logger.warning("LiveKit room ensure failed: %s", exc)

            livekit_token = mint_livekit_token(
                room=livekit_room,
                identity=claims.user_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("LiveKit token mint failed: %s", exc)

    session = Session(
        id=session_id,
        organization_id=org_id,
        mode=body.mode,
        customer_id=body.customer_id,
        livekit_room=livekit_room if body.mode in ("live", "intake") else body.livekit_room,
        status="active",
        started_at=datetime.now(timezone.utc),
    )
    db.add(session)
    # commit handled by get_session

    logger.info("session.create session_id=%s mode=%s org_id=%s", session_id, body.mode, org_id)
    return CreateSessionResponse(
        session_id=session_id,
        ws_url=f"/ws/sessions/{session_id}",
        livekit_token=livekit_token,
    )


@router.get(
    "/sessions",
    response_model=SessionListResponse,
    summary="List all sessions for the org (Transcripts tab)",
)
async def list_sessions(
    claims: Claims = Depends(current_claims),
    db: AsyncSession = Depends(get_session),
) -> SessionListResponse:
    result = await db.execute(select(Session).order_by(Session.started_at.desc()))
    sessions = result.scalars().all()

    session_infos: list[SessionInfo] = []
    for s in sessions:
        cnt = await _card_count(s.id, db)
        session_infos.append(session_to_schema(s, card_count=cnt))

    return SessionListResponse(sessions=session_infos)


@router.get(
    "/sessions/{session_id}",
    response_model=SessionInfo,
    summary="Get a single session",
)
async def get_session_info(
    session_id: str,
    claims: Claims = Depends(current_claims),
    db: AsyncSession = Depends(get_session),
) -> SessionInfo:
    session = await _get_session_or_404(session_id, db)
    cnt = await _card_count(session_id, db)
    return session_to_schema(session, card_count=cnt)


@router.post(
    "/sessions/{session_id}/end",
    response_model=EndSessionResponse,
    summary="End an active session",
)
async def end_session(
    session_id: str,
    claims: Claims = Depends(current_claims),
    db: AsyncSession = Depends(get_session),
) -> EndSessionResponse:
    session = await _get_session_or_404(session_id, db)
    session.status = "ended"
    session.ended_at = datetime.now(timezone.utc)
    # commit handled by get_session
    logger.info("session.end session_id=%s org_id=%s", session_id, claims.org_id)
    return EndSessionResponse(status="ended")


@router.get(
    "/sessions/{session_id}/cards",
    response_model=SessionCardsResponse,
    summary="Get cards produced during a session",
)
async def get_session_cards(
    session_id: str,
    claims: Claims = Depends(current_claims),
    db: AsyncSession = Depends(get_session),
) -> SessionCardsResponse:
    await _get_session_or_404(session_id, db)

    result = await db.execute(
        select(Card)
        .where(Card.session_id == session_id)
        .order_by(Card.created_at.asc())
    )
    cards = result.scalars().all()

    card_schemas: list[CardSchema] = []
    for card in cards:
        card_schemas.append(await _resolve_card_schema(card, db))

    return SessionCardsResponse(cards=card_schemas)


@router.get(
    "/sessions/{session_id}/transcript",
    response_model=TranscriptResponse,
    summary="Get the utterance transcript for a session",
)
async def get_session_transcript(
    session_id: str,
    claims: Claims = Depends(current_claims),
    db: AsyncSession = Depends(get_session),
) -> TranscriptResponse:
    await _get_session_or_404(session_id, db)

    result = await db.execute(
        select(Utterance)
        .where(Utterance.session_id == session_id)
        .order_by(Utterance.ts.asc())
    )
    utterances = result.scalars().all()
    return TranscriptResponse(utterances=[utterance_to_schema(u) for u in utterances])


@router.post(
    "/sessions/{session_id}/livekit-token",
    response_model=LiveKitTokenResponse,
    summary="Mint or refresh a LiveKit room token for a session",
)
async def get_livekit_token(
    session_id: str,
    claims: Claims = Depends(current_claims),
    db: AsyncSession = Depends(get_session),
) -> LiveKitTokenResponse:
    session = await _get_session_or_404(session_id, db)
    room = session.livekit_room or f"relay-{session_id}"

    try:
        from relay.adapters.livekit_tokens import mint_livekit_token

        token = mint_livekit_token(room=room, identity=claims.user_id)
    except Exception as exc:
        logger.error("LiveKit token mint failed for session %s: %s", session_id, exc)
        raise HTTPException(
            status_code=503,
            detail={"error": {"code": "internal_error", "message": "LiveKit token generation failed"}},
        ) from exc

    return LiveKitTokenResponse(livekit_token=token, livekit_room=room)


@router.post(
    "/sessions/{session_id}/reply",
    response_model=ReplyResponse,
    summary="Send a suggested reply to the customer (Desk mode)",
)
async def send_reply(
    session_id: str,
    body: ReplyRequest,
    claims: Claims = Depends(current_claims),
    db: AsyncSession = Depends(get_session),
) -> ReplyResponse:
    """Desk: dispatch the suggested (optionally edited) resolution to the customer.

    In the current implementation we log the intent and return ``{"status": "sent"}``.
    A production build would deliver the message via the configured channel
    (e.g. email, help-desk ticket) — mark with TODO when wiring.
    """
    await _get_session_or_404(session_id, db)

    # TODO: integrate with customer messaging channel (email / ticket API)
    logger.info(
        "session.reply session_id=%s card_id=%s org_id=%s text_len=%d",
        session_id,
        body.card_id,
        claims.org_id,
        len(body.text),
    )
    return ReplyResponse(status="sent")

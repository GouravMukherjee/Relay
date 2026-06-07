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
from relay.config import settings
from relay.ids import new_id, stable_session_id
from relay.schemas.account import (
    LiveKitTokenResponse,
    ReplyRequest,
    ReplyResponse,
)
from relay.schemas.cards import Card as CardSchema
from relay.schemas.cards import SessionCardsResponse, card_to_schema, source_from_card_source
from relay.schemas.inbound import InboundSessionResponse
from relay.schemas.sessions import (
    CreateSessionRequest,
    CreateSessionResponse,
    DemoSessionResponse,
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
            from relay.adapters.livekit_tokens import (
                ensure_agent_dispatch,
                ensure_room,
                mint_livekit_token,
            )

            room_metadata = {
                "session_id": session_id,
                "org_id": str(org_id),
                "mode": body.mode,
                "customer_id": body.customer_id or "",
            }

            # Stamp session context onto the room so the agent worker can read
            # org_id / mode / customer_id from room metadata (NOT from client msgs).
            try:
                await ensure_room(livekit_room, room_metadata)
            except Exception as exc:  # noqa: BLE001 — best-effort; never block session start
                logger.warning("LiveKit room ensure failed: %s", exc)

            # The agent worker is a NAMED agent (explicit dispatch only), so we must
            # dispatch it to this session's room — automatic dispatch is off. Best-effort.
            try:
                await ensure_agent_dispatch(livekit_room, room_metadata)
            except Exception as exc:  # noqa: BLE001 — best-effort; never block session start
                logger.warning("LiveKit agent dispatch failed: %s", exc)

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
    "/sessions/demo",
    response_model=DemoSessionResponse,
    summary="Get (or provision) the fixed inbound-phone demo session",
)
async def get_demo_session(
    claims: Claims = Depends(current_claims),
    db: AsyncSession = Depends(get_session),
) -> DemoSessionResponse:
    """Return the demo session the dashboard watches for inbound phone calls.

    Ensures (idempotently): the demo Session row exists, the LiveKit demo room exists
    with session metadata, and the named agent is dispatched to it. Returns a LiveKit
    token so the rep can optionally join the room and publish the browser mic as a
    fallback audio source. The ``session_id`` is DETERMINISTIC (derived from the room
    name) so it matches the id the agent worker uses for the same room.
    """
    room = settings.livekit_demo_room or "relay-demo"
    org_id = claims.org_id
    session_id = stable_session_id(room)

    # Ensure the demo Session row (cards persist with this session_id FK).
    existing = await db.get(Session, session_id)
    if existing is None:
        db.add(
            Session(
                id=session_id,
                organization_id=org_id,
                mode="live",
                livekit_room=room,
                status="active",
                started_at=datetime.now(timezone.utc),
            )
        )
        # commit handled by get_session

    metadata = {
        "session_id": session_id,
        "org_id": str(org_id),
        "mode": "live",
        "customer_id": "",
    }

    livekit_token: str | None = None
    try:
        from relay.adapters.livekit_tokens import (
            ensure_agent_dispatch,
            ensure_room,
            mint_livekit_token,
        )

        try:
            await ensure_room(room, metadata)
            await ensure_agent_dispatch(room, metadata)
        except Exception as exc:  # noqa: BLE001 — best-effort; watching still works
            logger.warning("demo room/dispatch setup failed: %s", exc)

        livekit_token = mint_livekit_token(room=room, identity=claims.user_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("demo LiveKit token mint failed: %s", exc)

    logger.info("session.demo session_id=%s room=%s org_id=%s", session_id, room, org_id)
    return DemoSessionResponse(
        session_id=session_id,
        ws_url=f"/ws/sessions/{session_id}",
        livekit_room=room,
        livekit_token=livekit_token,
    )


@router.get(
    "/inbound/session",
    response_model=InboundSessionResponse,
    summary="Get (or provision) the rep's view of the demo inbound thread",
)
async def get_inbound_session(
    claims: Claims = Depends(current_claims),
    db: AsyncSession = Depends(get_session),
) -> InboundSessionResponse:
    """Return the rep-side session the Desk/Intake panels watch for the demo inbound thread.

    Analogous to ``GET /sessions/demo``: derives the DETERMINISTIC inbound session id from
    the demo thread, ensures the Session row exists, and returns its WS url so the dashboard
    streams customer messages, cards, and leads with no manual room selection. The thread id
    is stamped onto ``livekit_room`` as ``"inbound:" + thread_id`` so it's recoverable.
    """
    from relay.gateway.ws import inbound_session_id, register_thread

    thread_id = settings.inbound_demo_thread
    session_id = inbound_session_id(thread_id)
    register_thread(thread_id)  # cache the mapping for /sessions/{id}/reply
    org_id = claims.org_id

    existing = await db.get(Session, session_id)
    if existing is None:
        db.add(
            Session(
                id=session_id,
                organization_id=org_id,
                mode="desk",
                livekit_room=f"inbound:{thread_id}",
                status="active",
                started_at=datetime.now(timezone.utc),
            )
        )
        # commit handled by get_session

    logger.info("session.inbound session_id=%s thread_id=%s org_id=%s", session_id, thread_id, org_id)
    return InboundSessionResponse(
        session_id=session_id,
        ws_url=f"/ws/sessions/{session_id}",
        thread_id=thread_id,
    )


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
    For an **inbound thread** the reply is ALSO delivered to the customer widget: we
    persist ``Utterance(speaker="agent")`` and push a ``message`` (role=agent) envelope to
    the widget WS. A production build would also deliver via the configured channel
    (e.g. email, help-desk ticket) — marked with TODO when wiring.
    """
    session = await _get_session_or_404(session_id, db)

    # --- Inbound-thread delivery: route the rep reply back to the customer widget. ---
    # Recover the thread id from the in-process map, falling back to the session row's
    # ``livekit_room`` ("inbound:" + thread_id) so it survives a process restart.
    from relay.gateway.ws import deliver_to_widget, thread_for_session

    thread_id = thread_for_session(session_id)
    if thread_id is None and session.livekit_room and session.livekit_room.startswith("inbound:"):
        thread_id = session.livekit_room.split("inbound:", 1)[1]

    if thread_id and body.text and body.text.strip():
        text = body.text.strip()
        # Persist the agent utterance so Transcripts + history stay consistent.
        try:
            db.add(
                Utterance(
                    id=new_id("utt"),
                    session_id=session_id,
                    organization_id=claims.org_id,
                    speaker="agent",
                    text=text,
                )
            )
            await db.flush()
        except Exception as exc:  # noqa: BLE001 — never block the reply on a write error
            logger.warning("inbound reply utterance persist failed session_id=%s: %s", session_id, exc)
        # Deliver to the customer widget (best-effort fan-out; never raises).
        try:
            await deliver_to_widget(thread_id, "agent", text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("inbound reply widget delivery failed thread_id=%s: %s", thread_id, exc)

    # TODO: integrate with customer messaging channel (email / ticket API)
    logger.info(
        "session.reply session_id=%s card_id=%s org_id=%s text_len=%d inbound=%s",
        session_id,
        body.card_id,
        claims.org_id,
        len(body.text),
        bool(thread_id),
    )
    return ReplyResponse(status="sent")

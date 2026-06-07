"""Session schemas — mirror types.ts SessionInfo and Utterance exactly."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


# ── Shared object shapes ──────────────────────────────────────────────────────

class SessionInfo(BaseModel):
    session_id: str
    mode: str                               # live | desk | intake
    status: Literal["active", "ended"]
    started_at: str                         # ISO-8601 UTC
    ended_at: str | None = None
    card_count: int


class Utterance(BaseModel):
    utterance_id: str    # utt_…
    session_id: str      # ses_…
    speaker: str         # e.g. rep | prospect | customer
    text: str
    ts: str              # ISO-8601 UTC


# ── Request models ────────────────────────────────────────────────────────────

class CreateSessionRequest(BaseModel):
    mode: str                        # live | desk | intake
    livekit_room: str | None = None
    customer_id: str | None = None


# ── Response models ───────────────────────────────────────────────────────────

class CreateSessionResponse(BaseModel):
    """201 response for POST /sessions."""
    session_id: str
    ws_url: str
    livekit_token: str | None = None


class DemoSessionResponse(BaseModel):
    """GET /sessions/demo response — the fixed inbound-phone demo session.

    The dashboard's Live view uses this to WATCH the demo room: it connects its WS to
    ``session_id`` (where the agent broadcasts cards for the inbound call) and may
    optionally join ``livekit_room`` with ``livekit_token`` to publish the browser mic
    as a fallback audio source.
    """
    session_id: str
    ws_url: str
    livekit_room: str
    livekit_token: str | None = None


class EndSessionResponse(BaseModel):
    """200 response for POST /sessions/{session_id}/end."""
    status: Literal["ended"] = "ended"


class SessionListResponse(BaseModel):
    """GET /sessions response."""
    sessions: list[SessionInfo]


class TranscriptResponse(BaseModel):
    """GET /sessions/{session_id}/transcript response."""
    utterances: list[Utterance]


# ── Mapper from DB models ─────────────────────────────────────────────────────

def session_to_schema(session: object, card_count: int = 0) -> SessionInfo:
    """Map a relay.db.models.Session ORM instance to SessionInfo schema.

    The DB model uses ``id`` as its PK; the external schema exposes it
    as ``session_id``.
    """
    ended_at = getattr(session, "ended_at", None)  # type: ignore[attr-defined]
    return SessionInfo(
        session_id=session.id,  # type: ignore[attr-defined]
        mode=session.mode,  # type: ignore[attr-defined]
        status=session.status,  # type: ignore[attr-defined]
        started_at=session.started_at.isoformat() if session.started_at else "",  # type: ignore[attr-defined]
        ended_at=ended_at.isoformat() if ended_at else None,
        card_count=card_count,
    )


def utterance_to_schema(utt: object) -> Utterance:
    """Map a relay.db.models.Utterance ORM instance to Utterance schema.

    The DB model uses ``id`` as its PK; the external schema exposes it
    as ``utterance_id``.
    """
    return Utterance(
        utterance_id=utt.id,  # type: ignore[attr-defined]
        session_id=utt.session_id,  # type: ignore[attr-defined]
        speaker=utt.speaker,  # type: ignore[attr-defined]
        text=utt.text,  # type: ignore[attr-defined]
        ts=utt.ts.isoformat() if utt.ts else "",  # type: ignore[attr-defined]
    )

"""WebSocket event schemas — mirrors types.ts ServerEvent / ClientEvent exactly."""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel

from relay.schemas.cards import Card


# ── Server → Client events ────────────────────────────────────────────────────

class TranscriptPartialData(BaseModel):
    speaker: str
    text: str


class TranscriptFinalData(BaseModel):
    utterance_id: str
    speaker: str
    text: str


class SessionStatusData(BaseModel):
    status: Literal["active", "ended"]
    retrieval_backend: Literal["moss", "pgvector"]


class ErrorEventData(BaseModel):
    code: str
    message: str


class LeadUpdateData(BaseModel):
    """lead.update event data — re-exports the full Lead shape inline to avoid
    a circular import; callers may pass a serialized Lead dict."""

    model_config = {"extra": "allow"}


# Typed envelope models (optional strict path — callers may also use the
# free-form build_event() helper from common.py for quick dispatch).

class TranscriptPartialEvent(BaseModel):
    type: Literal["transcript.partial"]
    ts: str
    data: TranscriptPartialData


class TranscriptFinalEvent(BaseModel):
    type: Literal["transcript.final"]
    ts: str
    data: TranscriptFinalData


class CardNewEvent(BaseModel):
    type: Literal["card.new"]
    ts: str
    data: Card


class CardUpdateEvent(BaseModel):
    """card.update — partial Card fields plus the mandatory card_id."""
    type: Literal["card.update"]
    ts: str
    data: dict[str, Any]   # { card_id, ...Partial<Card> }


class SessionStatusEvent(BaseModel):
    type: Literal["session.status"]
    ts: str
    data: SessionStatusData


class LeadUpdateEvent(BaseModel):
    type: Literal["lead.update"]
    ts: str
    data: dict[str, Any]   # serialized Lead shape


class ErrorEvent(BaseModel):
    type: Literal["error"]
    ts: str
    data: ErrorEventData


# ── Client → Server events ────────────────────────────────────────────────────

class ModeSetData(BaseModel):
    mode: str   # live | desk | intake


class QueryManualData(BaseModel):
    text: str


class CardPinData(BaseModel):
    card_id: str


class CardDismissData(BaseModel):
    card_id: str


class ClientEvent(BaseModel):
    """Inbound WS message from the browser.

    Discriminated by ``type``; ``data`` is kept generic so that the WS handler
    can validate the payload after inspecting the type.
    """
    type: str
    data: dict[str, Any] = {}

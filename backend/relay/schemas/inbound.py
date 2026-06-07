"""Inbound (customer-facing widget) channel schemas — the frozen INBOUND_CONTRACT I/O.

A *thread* is one customer conversation. It maps deterministically to a Relay *session*
(``stable_session_id("inbound:" + thread_id)``) so the rep's existing Desk/Intake panels —
which already speak the ``session_id``-keyed WS event language — light up unchanged.

These are the public (no-auth) REST shapes mounted under ``/api/v1`` plus the rep-side
``GET /inbound/session`` response. The widget WebSocket envelopes themselves are plain
``{type, ts, data}`` dicts built via :func:`relay.schemas.common.build_event`.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


# ── Public REST (no Supabase auth) ────────────────────────────────────────────


class CreateThreadRequest(BaseModel):
    """``POST /inbound/threads`` body. ``display_name`` is an optional visitor label."""

    display_name: str | None = None


class CreateThreadResponse(BaseModel):
    """``POST /inbound/threads`` response: the thread id + its widget WS url."""

    thread_id: str
    ws_url: str


class InboundMessageRequest(BaseModel):
    """``POST /inbound/threads/{thread_id}/messages`` body — a customer message."""

    text: str
    display_name: str | None = None


class InboundMessageResponse(BaseModel):
    """``POST /inbound/threads/{thread_id}/messages`` response (HTTP 202)."""

    status: Literal["received"] = "received"


# ── Rep-side view (Supabase auth) ─────────────────────────────────────────────


class InboundSessionResponse(BaseModel):
    """``GET /inbound/session`` response — the rep's view of the demo inbound thread."""

    session_id: str
    ws_url: str
    thread_id: str


__all__ = [
    "CreateThreadRequest",
    "CreateThreadResponse",
    "InboundMessageRequest",
    "InboundMessageResponse",
    "InboundSessionResponse",
]

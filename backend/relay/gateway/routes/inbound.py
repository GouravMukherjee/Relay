"""Public inbound-channel REST routes (the customer-facing widget).

Mounted under ``/api/v1`` like every other route module, but **public** (NO Supabase
auth) — the widget is unauthenticated by design for the demo (gate behind a token before
prod). See ``docs/INBOUND_CONTRACT.md``.

Endpoints
---------
POST  /inbound/threads                      Create or resolve a thread (→ thread_id + ws_url)
POST  /inbound/threads/{thread_id}/messages Customer sends a message (202; pipeline async)

Each thread maps deterministically to a Relay session so the rep's existing Desk/Intake
panels light up unchanged: ``session_id = stable_session_id("inbound:" + thread_id)``.

The full server pipeline on a customer message (persist → echo → notify rep → classify →
intake triage → grounded Desk synthesis → clear typing) lives in
:func:`relay.gateway.ws.handle_inbound_message`. These routes just resolve the thread and
hand off; they NEVER 500 the widget on a pipeline error.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter

from relay.config import settings
from relay.ids import new_id
from relay.schemas.inbound import (
    CreateThreadRequest,
    CreateThreadResponse,
    InboundMessageRequest,
    InboundMessageResponse,
)

logger = logging.getLogger("relay.gateway.routes.inbound")

router = APIRouter(tags=["inbound"])


def _resolve_thread_id(display_name: str | None) -> str:
    """Pick the thread id for a (possibly new) visitor.

    For the demo the default thread is ``settings.inbound_demo_thread`` so the rep dashboard
    has a fixed thread to watch. When a ``display_name`` is supplied we treat it as a fresh
    visitor and mint a random thread id (still acceptable per the contract).
    """
    if display_name and display_name.strip():
        return f"inbound-{new_id('thr').split('_', 1)[-1]}"
    return settings.inbound_demo_thread


@router.post(
    "/inbound/threads",
    status_code=200,
    response_model=CreateThreadResponse,
    summary="Create or resolve an inbound customer thread (public)",
)
async def create_thread(body: CreateThreadRequest) -> CreateThreadResponse:
    """Create/resolve a thread and return its id + the widget WS url to connect to."""
    from relay.gateway.ws import register_thread

    thread_id = _resolve_thread_id(body.display_name)
    register_thread(thread_id)  # cache the thread<->session mapping eagerly
    logger.info("inbound.thread thread_id=%s", thread_id)
    return CreateThreadResponse(
        thread_id=thread_id,
        ws_url=f"/ws/inbound/{thread_id}",
    )


@router.post(
    "/inbound/threads/{thread_id}/messages",
    status_code=202,
    response_model=InboundMessageResponse,
    summary="Customer sends a message on a thread (public)",
)
async def post_message(
    thread_id: str, body: InboundMessageRequest
) -> InboundMessageResponse:
    """Accept a customer message and run the inbound pipeline asynchronously.

    Returns ``202 {status:"received"}`` immediately; the persist/echo/notify/classify/
    triage/synthesis steps stream out over the WebSockets. Best-effort — the widget is
    never 500'd by a downstream failure.
    """
    from relay.gateway.ws import handle_inbound_message

    text = (body.text or "").strip()
    if text:
        # Fire-and-forget: the pipeline streams results over the rep + widget sockets.
        asyncio.create_task(
            handle_inbound_message(
                thread_id=thread_id, text=text, display_name=body.display_name
            )
        )
    return InboundMessageResponse(status="received")


__all__ = ["router"]

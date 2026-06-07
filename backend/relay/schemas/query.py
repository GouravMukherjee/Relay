"""Query endpoint schemas."""
from __future__ import annotations

from pydantic import BaseModel

from relay.schemas.cards import Card


# ── Request / Response models ─────────────────────────────────────────────────

class QueryRequest(BaseModel):
    """POST /query request body."""
    session_id: str | None = None
    mode: str                       # live | desk | intake
    text: str
    customer_id: str | None = None


class QueryResponse(BaseModel):
    """POST /query response.

    ``card`` is None when there is no relevant grounding ("no card" signal).
    Per the contract this is NOT surfaced as an HTTP error — the response is
    200 with ``card: null``.
    """
    card: Card | None

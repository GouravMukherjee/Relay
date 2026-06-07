"""Common/shared schema types used across multiple modules."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


# ── Error envelope ────────────────────────────────────────────────────────────

class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


# ── WebSocket envelope helper ─────────────────────────────────────────────────

def build_event(type: str, data: dict[str, Any], ts: str) -> dict[str, Any]:
    """Build a WS envelope dict.

    Args:
        type: Event type string (e.g. "card.new").
        data: Payload dict.
        ts:   ISO-8601 UTC timestamp string — callers are responsible for
              supplying this; this function never calls datetime.now().
    """
    return {"type": type, "ts": ts, "data": data}

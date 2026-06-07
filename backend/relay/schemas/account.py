"""Account / user schemas (additive endpoints)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


# ── Shared object shapes ──────────────────────────────────────────────────────

class User(BaseModel):
    id: str        # UUID (Supabase auth sub)
    name: str
    role: str
    email: str | None = None


class Notification(BaseModel):
    id: str        # ntf_…
    text: str
    read: bool
    created_at: str   # ISO-8601 UTC


# ── Response models ───────────────────────────────────────────────────────────

class UserListResponse(BaseModel):
    """GET /users response."""
    users: list[User]


class NotificationListResponse(BaseModel):
    """GET /notifications response."""
    notifications: list[Notification]


class LiveKitTokenResponse(BaseModel):
    """POST /sessions/{session_id}/livekit-token response."""
    livekit_token: str
    livekit_room: str


class ReplyRequest(BaseModel):
    """POST /sessions/{session_id}/reply request body (additive)."""
    card_id: str | None = None
    text: str


class ReplyResponse(BaseModel):
    """POST /sessions/{session_id}/reply response."""
    status: Literal["sent"] = "sent"


# ── Mapper from DB models ─────────────────────────────────────────────────────

def user_to_schema(user: object) -> User:
    """Map a relay.db.models.User ORM instance to User schema."""
    return User(
        id=str(user.id),  # type: ignore[attr-defined]
        name=user.name,  # type: ignore[attr-defined]
        role=user.role,  # type: ignore[attr-defined]
        email=getattr(user, "email", None),  # type: ignore[attr-defined]
    )


def notification_to_schema(ntf: object) -> Notification:
    """Map a relay.db.models.Notification ORM instance to Notification schema."""
    return Notification(
        id=ntf.id,  # type: ignore[attr-defined]
        text=ntf.text,  # type: ignore[attr-defined]
        read=bool(ntf.read),  # type: ignore[attr-defined]
        created_at=ntf.created_at.isoformat() if ntf.created_at else "",  # type: ignore[attr-defined]
    )

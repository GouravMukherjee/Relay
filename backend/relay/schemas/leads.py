"""Lead schemas — mirror types.ts Lead / LeadQualifiers exactly."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


# ── Shared object shapes ──────────────────────────────────────────────────────

class LeadQualifiers(BaseModel):
    budget: str | None = None
    timeline: str | None = None
    need: str | None = None

    model_config = {"extra": "allow"}   # allow arbitrary additional keys


class Lead(BaseModel):
    lead_id: str       # lead_…
    session_id: str    # ses_…
    name: str
    company: str
    email: str
    qualifiers: LeadQualifiers
    score: int         # 0..100 ICP fit
    status: Literal["hot", "warm", "cold"]
    routed_to: str | None
    created_at: str    # ISO-8601 UTC


# ── Response models ───────────────────────────────────────────────────────────

class LeadListResponse(BaseModel):
    """GET /leads response."""
    leads: list[Lead]


class RouteLeadResponse(BaseModel):
    """POST /leads/{lead_id}/route response."""
    routed_to: str


class BookLeadResponse(BaseModel):
    """POST /leads/{lead_id}/book response (additive)."""
    status: Literal["booked"] = "booked"
    calendar_url: str | None = None


# ── Mapper from DB model ──────────────────────────────────────────────────────

def lead_to_schema(lead: object) -> Lead:
    """Map a relay.db.models.Lead ORM instance to Lead schema.

    The DB model uses ``id`` as its PK; the external schema exposes it
    as ``lead_id``.
    """
    raw_qualifiers: Any = getattr(lead, "qualifiers", {}) or {}  # type: ignore[attr-defined]
    return Lead(
        lead_id=lead.id,  # type: ignore[attr-defined]
        session_id=lead.session_id,  # type: ignore[attr-defined]
        name=lead.name,  # type: ignore[attr-defined]
        company=lead.company,  # type: ignore[attr-defined]
        email=lead.email,  # type: ignore[attr-defined]
        qualifiers=LeadQualifiers.model_validate(raw_qualifiers),
        score=lead.score or 0,  # type: ignore[attr-defined]
        status=lead.status,  # type: ignore[attr-defined]
        routed_to=getattr(lead, "routed_to", None),  # type: ignore[attr-defined]
        created_at=lead.created_at.isoformat() if lead.created_at else "",  # type: ignore[attr-defined]
    )

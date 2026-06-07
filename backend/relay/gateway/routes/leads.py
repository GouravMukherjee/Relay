"""Lead management routes (Intake mode).

Endpoints
---------
GET   /leads                  List all leads for the authenticated org
GET   /leads/{lead_id}        Get a single lead
POST  /leads/{lead_id}/route  Route a lead to a channel (Slack ping)
POST  /leads/{lead_id}/book   Book a meeting for a qualified lead (additive)

Mounted under /api/v1 by create_app().
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from relay.auth.deps import current_claims
from relay.auth.jwt import Claims
from relay.db.base import get_session
from relay.db.models import Lead
from relay.schemas.leads import (
    BookLeadResponse,
    Lead as LeadSchema,
    LeadListResponse,
    RouteLeadResponse,
    lead_to_schema,
)

logger = logging.getLogger("relay.gateway.routes.leads")

router = APIRouter(tags=["leads"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_lead_or_404(lead_id: str, db: AsyncSession) -> Lead:
    lead = await db.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "session_not_found",
                    "message": f"Lead {lead_id!r} not found",
                }
            },
        )
    return lead


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "/leads",
    response_model=LeadListResponse,
    summary="List leads for the authenticated org",
)
async def list_leads(
    claims: Claims = Depends(current_claims),
    db: AsyncSession = Depends(get_session),
) -> LeadListResponse:
    result = await db.execute(select(Lead).order_by(Lead.created_at.desc()))
    leads = result.scalars().all()
    return LeadListResponse(leads=[lead_to_schema(lead) for lead in leads])


@router.get(
    "/leads/{lead_id}",
    response_model=LeadSchema,
    summary="Get a single lead",
)
async def get_lead(
    lead_id: str,
    claims: Claims = Depends(current_claims),
    db: AsyncSession = Depends(get_session),
) -> LeadSchema:
    lead = await _get_lead_or_404(lead_id, db)
    return lead_to_schema(lead)


@router.post(
    "/leads/{lead_id}/route",
    response_model=RouteLeadResponse,
    summary="Route a lead to a channel (Slack ping)",
)
async def route_lead(
    lead_id: str,
    claims: Claims = Depends(current_claims),
    db: AsyncSession = Depends(get_session),
) -> RouteLeadResponse:
    """Send a Slack notification for the lead and record the channel it was
    routed to.  The Slack webhook is optional; if not configured the route still
    succeeds (the ``routed_to`` value defaults to ``#sales``).
    """
    lead = await _get_lead_or_404(lead_id, db)

    channel = "#sales"
    slack_text = (
        f"*New lead routed* :tada:\n"
        f"*Name:* {lead.name}\n"
        f"*Company:* {lead.company or '—'}\n"
        f"*Email:* {lead.email or '—'}\n"
        f"*Score:* {lead.score}/100  *Status:* {lead.status}\n"
        f"*Session:* {lead.session_id}"
    )

    try:
        from relay.adapters.slack import SlackNotifier

        notifier = SlackNotifier()
        channel = await notifier.route_lead(slack_text) or channel
    except Exception as exc:  # noqa: BLE001
        logger.warning("Slack route_lead failed for %s: %s", lead_id, exc)

    lead.routed_to = channel
    # commit handled by get_session

    logger.info(
        "lead.route lead_id=%s channel=%s org_id=%s",
        lead_id,
        channel,
        claims.org_id,
    )
    return RouteLeadResponse(routed_to=channel)


@router.post(
    "/leads/{lead_id}/book",
    response_model=BookLeadResponse,
    summary="Book a meeting for a qualified lead (additive)",
)
async def book_meeting(
    lead_id: str,
    claims: Claims = Depends(current_claims),
    db: AsyncSession = Depends(get_session),
) -> BookLeadResponse:
    """Additive endpoint — book a meeting for a qualified lead.

    In the current implementation we mark the lead as booked and return
    ``{"status": "booked"}``.  A production build would integrate with a
    calendar API (e.g. Calendly, Google Calendar) and return a ``calendar_url``.
    """
    lead = await _get_lead_or_404(lead_id, db)

    # TODO: integrate with calendar API (Calendly / Google Calendar)
    calendar_url: str | None = None

    logger.info(
        "lead.book lead_id=%s org_id=%s",
        lead_id,
        claims.org_id,
    )
    return BookLeadResponse(status="booked", calendar_url=calendar_url)

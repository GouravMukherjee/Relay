"""Customer routes (Desk mode).

Endpoints
---------
GET   /customers              List customers for the org (with derived history)
GET   /customers/{id}         Get a single customer profile

A customer's ``history`` and ``plan`` are derived from their ``Memory`` rows — Desk
stores past tickets / facts as memories, so the panel can show "recent tickets" and
badge the plan tier without a dedicated column. Additive to API_SPEC; tenant scoping
is enforced by RLS on the request-scoped session (org_isolation on customers/memories).

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
from relay.db.models import Customer, Memory
from relay.schemas.customers import (
    CustomerHistoryItem,
    CustomerListResponse,
    CustomerProfile,
)

logger = logging.getLogger("relay.gateway.routes.customers")

router = APIRouter(tags=["customers"])

_PLAN_KEYWORDS = ("Enterprise", "Growth", "Starter")


def _infer_plan(memories: list[Memory]) -> str | None:
    """Infer the account plan/tier by scanning memory text (most-specific first)."""
    blob = " ".join(m.text for m in memories).lower()
    for plan in _PLAN_KEYWORDS:
        if plan.lower() in blob:
            return plan
    return None


def _profile(customer: Customer, memories: list[Memory]) -> CustomerProfile:
    history = [
        CustomerHistoryItem(
            memory_id=m.id,
            kind=m.kind,
            text=m.text,
            resolved="resolved" in (m.text or "").lower(),
            created_at=m.created_at.isoformat() if m.created_at else "",
        )
        for m in memories
    ]
    return CustomerProfile(
        customer_id=customer.id,
        name=customer.name,
        company=customer.company,
        email=customer.email,
        plan=_infer_plan(memories),
        history=history,
    )


async def _memories_for(customer_id: str, db: AsyncSession) -> list[Memory]:
    result = await db.execute(
        select(Memory)
        .where(Memory.customer_id == customer_id)
        .order_by(Memory.created_at.desc())
    )
    return list(result.scalars().all())


@router.get(
    "/customers",
    response_model=CustomerListResponse,
    summary="List customers for the org (Desk mode)",
)
async def list_customers(
    claims: Claims = Depends(current_claims),
    db: AsyncSession = Depends(get_session),
) -> CustomerListResponse:
    result = await db.execute(select(Customer).order_by(Customer.created_at.asc()))
    customers = list(result.scalars().all())

    profiles: list[CustomerProfile] = []
    for c in customers:
        profiles.append(_profile(c, await _memories_for(c.id, db)))
    return CustomerListResponse(customers=profiles)


@router.get(
    "/customers/{customer_id}",
    response_model=CustomerProfile,
    summary="Get a single customer profile",
)
async def get_customer(
    customer_id: str,
    claims: Claims = Depends(current_claims),
    db: AsyncSession = Depends(get_session),
) -> CustomerProfile:
    customer = await db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "session_not_found", "message": f"Customer {customer_id!r} not found"}},
        )
    return _profile(customer, await _memories_for(customer_id, db))

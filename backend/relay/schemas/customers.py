"""Customer (Desk mode) schemas.

A ``CustomerProfile`` is the support agent's view of a customer: identity plus a
short interaction ``history`` derived from the customer's ``Memory`` rows (past
tickets / facts). ``plan`` is inferred from the memory text (Starter / Growth /
Enterprise) so the Desk panel can badge the account tier without a dedicated column.

These are additive to the frozen API_SPEC (Desk needs a customer + history to render
the CUSTOMER panel), following the same conventions as the other schemas.
"""
from __future__ import annotations

from pydantic import BaseModel


class CustomerHistoryItem(BaseModel):
    """One past interaction (ticket / fact) for a customer."""

    memory_id: str
    kind: str                 # fact | summary | preference | ticket
    text: str
    resolved: bool            # convenience flag for the UI chip
    created_at: str           # ISO-8601 UTC


class CustomerProfile(BaseModel):
    """A customer plus their recent interaction history (Desk CUSTOMER panel)."""

    customer_id: str
    name: str
    company: str | None = None
    email: str | None = None
    plan: str | None = None
    history: list[CustomerHistoryItem] = []


class CustomerListResponse(BaseModel):
    """GET /customers response."""

    customers: list[CustomerProfile]

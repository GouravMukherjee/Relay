"""No-hallucination tests (TEST_PLAN G1/G4): no relevant chunk -> {card: null}.

Grounded-or-silent. When retrieval finds nothing relevant (or the LLM declines),
the system MUST return "no card" — never an invented answer — and the /query
endpoint surfaces this as HTTP 200 with ``{"card": null}`` (not an error).
"""

from __future__ import annotations

import uuid

import pytest

from relay.interfaces.llm import LLMClient
from relay.orchestrator.synth import Orchestrator

from tests.conftest import ORG_A, FakeLLM, FakeRetrieval
from tests.conftest import test_session_maker as session_maker

pytestmark = pytest.mark.asyncio


async def _seed_session(mode: str = "live") -> str:
    from relay.db.models import Session

    sid = f"ses_{uuid.uuid4().hex[:24]}"
    async with session_maker() as s:
        s.add(Session(id=sid, organization_id=ORG_A, mode=mode, status="active"))
        await s.commit()
    return sid


async def test_orchestrator_returns_none_when_no_chunks():
    """Off-topic query -> retrieval empty -> orchestrator returns None (G1)."""
    sid = await _seed_session()
    async with session_maker() as db:
        orch = Orchestrator(retrieval=FakeRetrieval(), llm=FakeLLM(), session=db)
        card = await orch.synthesize(
            session_id=sid,
            org_id=ORG_A,
            mode="live",
            query_text="zxqwv unrelated gibberish topic",
        )
    assert card is None


async def test_orchestrator_returns_none_when_llm_declines():
    """Chunks exist but the LLM finds nothing relevant -> None (no fabrication)."""

    class _DecliningLLM(LLMClient):
        async def synthesize_card(self, *, query, chunks, mode, window=None):
            return None

    sid = await _seed_session()
    async with session_maker() as db:
        orch = Orchestrator(retrieval=FakeRetrieval(), llm=_DecliningLLM(), session=db)
        card = await orch.synthesize(
            session_id=sid,
            org_id=ORG_A,
            mode="live",
            query_text="What uptime SLA does Relay guarantee?",
        )
    assert card is None


async def test_query_endpoint_returns_card_null_offtopic(client):
    """/query off-topic -> HTTP 200 with {"card": null} (not an error)."""
    sid = await _seed_session()
    r = await client.post(
        "/api/v1/query",
        json={"session_id": sid, "mode": "live", "text": "completely off topic zzzz"},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"card": None}


async def test_query_endpoint_no_card_does_not_persist(client):
    """A no-grounding query must not persist any Card row."""
    from sqlalchemy import func, select

    from relay.db.models import Card

    sid = await _seed_session()
    async with session_maker() as db:
        before = (
            await db.execute(select(func.count()).select_from(Card).where(Card.session_id == sid))
        ).scalar_one()

    r = await client.post(
        "/api/v1/query",
        json={"session_id": sid, "mode": "live", "text": "nothing relevant here qqqq"},
    )
    assert r.json() == {"card": None}

    async with session_maker() as db:
        after = (
            await db.execute(select(func.count()).select_from(Card).where(Card.session_id == sid))
        ).scalar_one()
    assert after == before

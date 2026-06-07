"""Grounding tests (TEST_PLAN G2/G3): a card cites ONLY retrieved chunks.

The grounding guard is the most important invariant: an answer must be backed by
the chunks that retrieval actually returned, and a citation must never reference a
chunk that was not retrieved.
"""

from __future__ import annotations

import uuid

import pytest

from relay.interfaces.llm import CardDraft, LLMClient
from relay.interfaces.retrieval import RetrievalResult, RetrievalService, RetrievedChunk
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


async def test_card_cites_only_retrieved_chunks():
    """Every source on the card maps to a chunk that retrieval returned."""
    sid = await _seed_session()
    async with session_maker() as db:
        orch = Orchestrator(retrieval=FakeRetrieval(), llm=FakeLLM(), session=db)
        card = await orch.synthesize(
            session_id=sid,
            org_id=ORG_A,
            mode="live",
            query_text="What uptime SLA does Relay guarantee?",
        )
        await db.commit()

    assert card is not None
    assert card.sources, "a grounded card must cite at least one source (G2)"
    # The fake retrieval returns the SLA chunk; the cited source must match it.
    cited_docs = {s.document_id for s in card.sources}
    assert "doc_security_0001" in cited_docs
    # Snippet must come from the real chunk text (G3 — citation resolves to text).
    assert any("uptime SLA" in s.snippet for s in card.sources)


async def test_hallucinated_citation_is_dropped():
    """If the LLM cites a chunk id that was NOT retrieved, the guard drops it.

    A custom LLM returns a citation for a chunk that retrieval never surfaced;
    the orchestrator must not include it. With no valid cited id remaining, the
    orchestrator falls back to citing the retrieved chunks (never uncited, never
    a phantom citation).
    """

    class _RogueLLM(LLMClient):
        async def synthesize_card(self, *, query, chunks, mode, window=None):
            return CardDraft(
                answer="Definitely true.",
                title="Rogue",
                used_chunk_ids=["chk_NOT_RETRIEVED_9999"],
            )

    sid = await _seed_session()
    async with session_maker() as db:
        orch = Orchestrator(retrieval=FakeRetrieval(), llm=_RogueLLM(), session=db)
        card = await orch.synthesize(
            session_id=sid,
            org_id=ORG_A,
            mode="live",
            query_text="What is the Growth plan price?",
        )
        await db.commit()

    assert card is not None
    # The phantom chunk id must never appear as a citation.
    # Sources only carry document_id/title/snippet; assert the retrieved pricing
    # chunk's document is what got cited (the fallback), not a fabricated source.
    assert all(s.document_id != "chk_NOT_RETRIEVED_9999" for s in card.sources)
    assert card.sources
    assert {s.document_id for s in card.sources} <= {"doc_pricing_0001", "doc_security_0001"}


async def test_card_sources_persisted_reference_real_chunk_ids():
    """CardSource rows persisted by the orchestrator reference retrieved chunk ids."""
    from sqlalchemy import select

    from relay.db.models import CardSource

    sid = await _seed_session()
    async with session_maker() as db:
        orch = Orchestrator(retrieval=FakeRetrieval(), llm=FakeLLM(), session=db)
        card = await orch.synthesize(
            session_id=sid,
            org_id=ORG_A,
            mode="live",
            query_text="What uptime SLA does Relay guarantee?",
        )
        await db.commit()

    assert card is not None
    async with session_maker() as db:
        rows = (
            await db.execute(select(CardSource).where(CardSource.card_id == card.card_id))
        ).scalars().all()
    assert rows, "orchestrator must persist CardSource rows"
    retrieved_ids = {"chk_sla_0001", "chk_price_0001"}
    for r in rows:
        assert r.chunk_id in retrieved_ids
        assert r.organization_id == ORG_A

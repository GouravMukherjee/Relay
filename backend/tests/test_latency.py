"""Latency tests (TEST_PLAN L2/L3): the synth path records latency_ms in budget.

With the deterministic fakes the synthesis path does no network I/O, so it must
be comfortably inside the ~300 ms short-card budget. We also verify the injected
clock is honoured (no import-time ``datetime.now``), making latency measurable.
"""

from __future__ import annotations

import time
import uuid

import pytest

from relay.orchestrator.synth import Orchestrator

from tests.conftest import ORG_A, FakeLLM, FakeRetrieval
from tests.conftest import test_session_maker as session_maker

pytestmark = pytest.mark.asyncio

# Short-card synthesis budget (TEST_PLAN L3). Generous on the fake path.
SYNTH_BUDGET_MS = 300


async def _seed_session() -> str:
    from relay.db.models import Session

    sid = f"ses_{uuid.uuid4().hex[:24]}"
    async with session_maker() as s:
        s.add(Session(id=sid, organization_id=ORG_A, mode="live", status="active"))
        await s.commit()
    return sid


async def test_synth_records_latency_within_budget():
    sid = await _seed_session()
    t0 = time.perf_counter()
    async with session_maker() as db:
        orch = Orchestrator(retrieval=FakeRetrieval(), llm=FakeLLM(), session=db)
        card = await orch.synthesize(
            session_id=sid,
            org_id=ORG_A,
            mode="live",
            query_text="What uptime SLA does Relay guarantee?",
        )
        await db.commit()
    wall_ms = (time.perf_counter() - t0) * 1000.0

    assert card is not None
    assert isinstance(card.latency_ms, int)
    assert card.latency_ms >= 0
    assert card.latency_ms <= SYNTH_BUDGET_MS, f"synth latency {card.latency_ms}ms exceeds budget"
    assert wall_ms < 1000, "fake synth path should be well under a second"


async def test_injected_clock_drives_latency_ms():
    """latency_ms is computed from the injected clock (deterministic, testable)."""
    sid = await _seed_session()

    ticks = iter([100.0, 100.123])  # start, end -> 123 ms elapsed

    def fake_clock() -> float:
        return next(ticks)

    async with session_maker() as db:
        orch = Orchestrator(
            retrieval=FakeRetrieval(),
            llm=FakeLLM(),
            session=db,
            clock=fake_clock,
        )
        card = await orch.synthesize(
            session_id=sid,
            org_id=ORG_A,
            mode="live",
            query_text="What is the Growth plan price?",
        )
        await db.commit()

    assert card is not None
    assert card.latency_ms == 123

"""WebSocket tests (TEST_PLAN F2 + the WS contract).

Verifies the ``/ws/sessions/{session_id}`` round-trip:
* on connect the server emits ``session.status`` (status + retrieval_backend),
* ``mode.set`` then ``query.manual`` over the socket yields a ``card.new`` event
  whose payload is a full Card,
* an off-topic ``query.manual`` (no grounding) yields NO card event (silent).

Uses Starlette's TestClient (its own WS implementation). The WS path verifies the
``?token=`` itself (not via dependency_overrides), so we pass a real HS256 token.
The orchestrator factory is monkeypatched to inject the deterministic fakes bound
to the test DB session.
"""

from __future__ import annotations

import uuid

import pytest
from starlette.testclient import TestClient

import relay.gateway.ws as ws_module
from relay.gateway.app import create_app
from relay.orchestrator.synth import Orchestrator

from tests.conftest import ORG_A, USER_A, FakeLLM, FakeRetrieval, make_token
from tests.conftest import test_session_maker as session_maker


def _seed_session_sync(mode: str = "live") -> str:
    """Create a session row synchronously via a fresh event loop."""
    import asyncio

    from relay.db.models import Session

    sid = f"ses_{uuid.uuid4().hex[:24]}"

    async def _go():
        async with session_maker() as s:
            s.add(Session(id=sid, organization_id=ORG_A, mode=mode, status="active"))
            await s.commit()

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_go())
    finally:
        loop.close()
    return sid


@pytest.fixture
def ws_app(monkeypatch):
    """App whose WS orchestrator factory returns the deterministic fakes."""

    def _fake_build_orchestrator(db):
        return Orchestrator(retrieval=FakeRetrieval(), llm=FakeLLM(), session=db)

    monkeypatch.setattr(ws_module, "_build_orchestrator", _fake_build_orchestrator)
    return create_app()


def test_ws_session_status_and_card_new(ws_app):
    sid = _seed_session_sync()
    token = make_token(user_id=USER_A, org_id=ORG_A)

    with TestClient(ws_app) as tc:
        with tc.websocket_connect(f"/ws/sessions/{sid}?token={token}") as wsconn:
            # First event on connect: session.status
            status = wsconn.receive_json()
            assert status["type"] == "session.status"
            assert status["data"]["status"] == "active"
            assert status["data"]["retrieval_backend"] in ("moss", "pgvector")
            assert "ts" in status

            # Set mode then send a grounded manual query.
            wsconn.send_json({"type": "mode.set", "data": {"mode": "live"}})
            wsconn.send_json(
                {"type": "query.manual", "data": {"text": "What uptime SLA does Relay guarantee?"}}
            )

            evt = wsconn.receive_json()
            assert evt["type"] == "card.new", evt
            card = evt["data"]
            assert card["session_id"] == sid
            assert card["answer"]
            assert card["sources"]
            assert set(card) == {
                "card_id",
                "session_id",
                "mode",
                "title",
                "answer",
                "sources",
                "trigger_text",
                "latency_ms",
                "created_at",
            }


def test_ws_no_card_on_offtopic_is_silent(ws_app):
    """An off-topic query produces no card.new (grounded-or-silent over WS)."""
    sid = _seed_session_sync()
    token = make_token(user_id=USER_A, org_id=ORG_A)

    with TestClient(ws_app) as tc:
        with tc.websocket_connect(f"/ws/sessions/{sid}?token={token}") as wsconn:
            assert wsconn.receive_json()["type"] == "session.status"

            wsconn.send_json(
                {"type": "query.manual", "data": {"text": "irrelevant nonsense zzzz qqqq"}}
            )
            # Follow with a grounded query; the first event we get back MUST be the
            # grounded card (the off-topic one produced nothing), proving silence.
            wsconn.send_json(
                {"type": "query.manual", "data": {"text": "What is the Growth plan price?"}}
            )
            evt = wsconn.receive_json()
            assert evt["type"] == "card.new"
            assert "Growth plan" in evt["data"]["answer"]


def test_ws_rejects_missing_token(ws_app):
    sid = _seed_session_sync()
    with TestClient(ws_app) as tc:
        with pytest.raises(Exception):
            with tc.websocket_connect(f"/ws/sessions/{sid}") as wsconn:
                wsconn.receive_json()

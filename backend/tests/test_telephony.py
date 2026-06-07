"""Inbound-phone (telephony) wiring tests — deterministic demo session + endpoint.

These run credential-free: LiveKit calls inside the demo endpoint are best-effort and
swallowed when creds are absent, so the endpoint still returns the deterministic
session id + ws_url (the dashboard only needs those to WATCH the room).
"""
from __future__ import annotations

import pytest

from relay.ids import stable_session_id


def test_stable_session_id_is_deterministic():
    a = stable_session_id("relay-demo")
    b = stable_session_id("relay-demo")
    assert a == b
    assert a.startswith("ses_")
    # Different room -> different id.
    assert stable_session_id("relay-demo") != stable_session_id("other-room")


@pytest.mark.asyncio
async def test_demo_session_endpoint_returns_deterministic_session(client):
    from relay.config import settings

    res = await client.get("/api/v1/sessions/demo")
    assert res.status_code == 200, res.text
    body = res.json()

    expected_room = settings.livekit_demo_room or "relay-demo"
    assert body["livekit_room"] == expected_room
    assert body["session_id"] == stable_session_id(expected_room)
    assert body["ws_url"] == f"/ws/sessions/{body['session_id']}"
    # Same call again -> same session id (idempotent / deterministic).
    res2 = await client.get("/api/v1/sessions/demo")
    assert res2.json()["session_id"] == body["session_id"]


@pytest.mark.asyncio
async def test_demo_session_not_shadowed_by_id_route(client):
    """/sessions/demo must resolve to the demo handler, not /sessions/{id} with id='demo'."""
    res = await client.get("/api/v1/sessions/demo")
    assert res.status_code == 200
    # The {session_id} route would 404 on a non-existent 'demo' session.
    assert "livekit_room" in res.json()

"""Contract tests: response shapes match frontend/src/types.ts / API_SPEC.

Covers the documents, sessions, query, leads, and account endpoint surfaces.
Field names and types are asserted against the frozen TS mirror.
"""

from __future__ import annotations

import uuid

import pytest

from tests.conftest import ORG_A, USER_A
from tests.conftest import test_session_maker as session_maker

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_session(mode: str = "live", status: str = "active") -> str:
    from relay.db.models import Session

    sid = f"ses_{uuid.uuid4().hex[:24]}"
    async with session_maker() as s:
        s.add(Session(id=sid, organization_id=ORG_A, mode=mode, status=status))
        await s.commit()
    return sid


async def _seed_lead() -> str:
    from relay.db.models import Lead, Session

    sid = await _seed_session(mode="intake")
    lid = f"lead_{uuid.uuid4().hex[:24]}"
    async with session_maker() as s:
        s.add(
            Lead(
                id=lid,
                session_id=sid,
                organization_id=ORG_A,
                name="Sarah Chen",
                company="Acme Corp",
                email="sarah@acme.test",
                qualifiers={"budget": "$50k", "timeline": "Q3", "need": "compliance"},
                score=82,
                status="hot",
                routed_to=None,
            )
        )
        await s.commit()
    return lid


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


async def test_documents_upload_and_list_contract(client):
    files = {"file": ("FAQ.txt", b"Relay grounds every answer in your docs.", "text/plain")}
    r = await client.post("/api/v1/documents", files=files, data={"title": "FAQ.pdf"})
    assert r.status_code == 202, r.text
    body = r.json()
    assert set(body) == {"document_id", "status"}
    assert body["document_id"].startswith("doc_")
    assert body["status"] == "processing"

    r2 = await client.get("/api/v1/documents")
    assert r2.status_code == 200
    docs = r2.json()["documents"]
    assert isinstance(docs, list) and docs
    doc = next(d for d in docs if d["document_id"] == body["document_id"])
    assert set(doc) == {"document_id", "title", "status", "chunk_count", "created_at"}
    assert doc["status"] in {"processing", "ready", "failed"}
    assert isinstance(doc["chunk_count"], int)

    # GET single
    r3 = await client.get(f"/api/v1/documents/{body['document_id']}")
    assert r3.status_code == 200
    assert r3.json()["document_id"] == body["document_id"]


async def test_documents_unsupported_type(client):
    files = {"file": ("malware.exe", b"MZ\x90\x00", "application/x-msdownload")}
    r = await client.post("/api/v1/documents", files=files)
    assert r.status_code == 415
    err = r.json()["error"]
    assert err["code"] == "document_unsupported"
    assert "message" in err


async def test_documents_delete(client):
    files = {"file": ("doc.txt", b"hello world content", "text/plain")}
    up = await client.post("/api/v1/documents", files=files)
    doc_id = up.json()["document_id"]
    r = await client.delete(f"/api/v1/documents/{doc_id}")
    assert r.status_code == 204
    # gone
    r2 = await client.get(f"/api/v1/documents/{doc_id}")
    assert r2.status_code == 404


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


async def test_create_session_contract(client):
    r = await client.post("/api/v1/sessions", json={"mode": "live"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["session_id"].startswith("ses_")
    assert body["ws_url"] == f"/ws/sessions/{body['session_id']}"
    assert "livekit_token" in body  # may be null without creds

    sid = body["session_id"]
    info = (await client.get(f"/api/v1/sessions/{sid}")).json()
    assert set(info) == {"session_id", "mode", "status", "started_at", "ended_at", "card_count"}
    assert info["mode"] == "live"
    assert info["status"] == "active"
    assert info["card_count"] == 0


async def test_session_end_and_list(client):
    sid = await _seed_session()
    r = await client.post(f"/api/v1/sessions/{sid}/end")
    assert r.status_code == 200
    assert r.json() == {"status": "ended"}

    lst = await client.get("/api/v1/sessions")
    assert lst.status_code == 200
    sessions = lst.json()["sessions"]
    assert any(s["session_id"] == sid and s["status"] == "ended" for s in sessions)


async def test_session_cards_and_transcript_shapes(client):
    sid = await _seed_session()
    cards = await client.get(f"/api/v1/sessions/{sid}/cards")
    assert cards.status_code == 200
    assert cards.json() == {"cards": []}

    tr = await client.get(f"/api/v1/sessions/{sid}/transcript")
    assert tr.status_code == 200
    assert tr.json() == {"utterances": []}


async def test_session_not_found(client):
    r = await client.get("/api/v1/sessions/ses_does_not_exist")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "session_not_found"


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------


async def test_query_grounded_contract(client):
    sid = await _seed_session()
    r = await client.post(
        "/api/v1/query",
        json={"session_id": sid, "mode": "live", "text": "What uptime SLA does Relay guarantee?"},
    )
    assert r.status_code == 200, r.text
    card = r.json()["card"]
    assert card is not None
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
    assert card["card_id"].startswith("card_")
    assert card["session_id"] == sid
    assert card["sources"]
    src = card["sources"][0]
    assert set(src) == {"document_id", "title", "snippet", "score"}
    assert isinstance(card["latency_ms"], int)


# ---------------------------------------------------------------------------
# Leads
# ---------------------------------------------------------------------------


async def test_leads_list_get_route_contract(client):
    lid = await _seed_lead()

    lst = await client.get("/api/v1/leads")
    assert lst.status_code == 200
    leads = lst.json()["leads"]
    lead = next(x for x in leads if x["lead_id"] == lid)
    assert set(lead) == {
        "lead_id",
        "session_id",
        "name",
        "company",
        "email",
        "qualifiers",
        "score",
        "status",
        "routed_to",
        "created_at",
    }
    assert lead["status"] in {"hot", "warm", "cold"}
    assert isinstance(lead["score"], int)
    assert lead["routed_to"] is None

    one = await client.get(f"/api/v1/leads/{lid}")
    assert one.status_code == 200
    assert one.json()["lead_id"] == lid

    routed = await client.post(f"/api/v1/leads/{lid}/route")
    assert routed.status_code == 200
    assert "routed_to" in routed.json()

    booked = await client.post(f"/api/v1/leads/{lid}/book")
    assert booked.status_code == 200
    assert booked.json()["status"] == "booked"


# ---------------------------------------------------------------------------
# Account
# ---------------------------------------------------------------------------


async def test_me_contract(client):
    r = await client.get("/api/v1/me")
    assert r.status_code == 200, r.text
    me = r.json()
    assert me["id"] == USER_A
    assert "name" in me and "role" in me
    # email is optional but present in our seed
    assert "email" in me


async def test_users_and_notifications_contract(client):
    users = await client.get("/api/v1/users")
    assert users.status_code == 200
    ulist = users.json()["users"]
    assert any(u["id"] == USER_A for u in ulist)
    for u in ulist:
        assert {"id", "name", "role"} <= set(u)

    ntf = await client.get("/api/v1/notifications")
    assert ntf.status_code == 200
    assert "notifications" in ntf.json()

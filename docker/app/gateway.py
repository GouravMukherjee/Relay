"""Relay Gateway — FastAPI REST + WebSocket hub (TDD §3.8, API_SPEC.md).

This is a runnable skeleton: health/readiness work, the REST surface from
API_SPEC is stubbed, and the per-session WebSocket echoes the envelope shape so
the frontend can integrate against it. Fill in the TODO bodies during Phase 1.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .db import close_pool, ping


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: pool is lazily created on first use.
    yield
    await close_pool()


app = FastAPI(title="Relay Gateway", version="0.1.0", lifespan=lifespan)

# Frontend (Vercel) calls this service cross-origin. Lock down in prod.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Health ──────────────────────────────────────────────────────────────────
@app.get("/healthz")
async def healthz():
    """Liveness — process is up. Used by container HEALTHCHECK / TFY probe."""
    return {"status": "ok", "service": "gateway", "ts": _now()}


@app.get("/readyz")
async def readyz():
    """Readiness — dependencies reachable (DB)."""
    db_ok = await ping()
    return {"status": "ready" if db_ok else "degraded", "db": db_ok}


# ── REST surface (API_SPEC.md) — stubs ──────────────────────────────────────
@app.post("/v1/documents", status_code=202)
async def upload_document():
    # TODO(T1.4): Unsiloed parse -> chunk -> embed -> Moss + Postgres.
    return {"document_id": "doc_stub", "status": "processing"}


@app.get("/v1/documents")
async def list_documents():
    return {"documents": []}


@app.post("/v1/sessions", status_code=201)
async def create_session():
    # TODO: insert session, mint LiveKit token, return ws_url.
    return {
        "session_id": "ses_stub",
        "ws_url": "/ws/sessions/ses_stub",
        "livekit_token": None,
    }


@app.post("/v1/query")
async def manual_query():
    # TODO(T2.1): retrieval -> Claude synthesis -> Card | null.
    return {"card": None}


@app.get("/v1/leads")
async def list_leads():
    return {"leads": []}


# ── WebSocket hub (API_SPEC.md §WebSocket) ──────────────────────────────────
@app.websocket("/ws/sessions/{session_id}")
async def session_ws(ws: WebSocket, session_id: str):
    """Bidirectional transcript + card stream for one session."""
    await ws.accept()
    await ws.send_json(
        {
            "type": "session.status",
            "ts": _now(),
            "data": {"status": "active", "retrieval_backend": "moss"},
        }
    )
    try:
        while True:
            # Client -> server: mode.set, query.manual, card.pin, card.dismiss.
            msg = await ws.receive_json()
            # TODO: route to orchestrator / retrieval and stream card.new back.
            await ws.send_json(
                {"type": "ack", "ts": _now(), "data": {"received": msg.get("type")}}
            )
    except WebSocketDisconnect:
        # TODO: mark session idle / clean up subscriptions.
        pass

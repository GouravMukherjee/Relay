"""Relay Retrieval Service — Moss primary, pgvector fallback (TDD §3.4, ADR-001).

Internal-only FastAPI service. The gateway/worker POST a query here and get back
top-k chunks. Target <10ms via Moss; falls back to pgvector cosine search.
Runs on port 8001 (distinct from the gateway's 8000).
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI
from pydantic import BaseModel

from .config import settings
from .db import get_pool, ping

app = FastAPI(title="Relay Retrieval", version="0.1.0")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class QueryIn(BaseModel):
    text: str
    top_k: int = 5
    organization_id: str = "00000000-0000-0000-0000-000000000001"


class Chunk(BaseModel):
    chunk_id: str
    document_id: str
    text: str
    score: float


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "service": "retrieval", "ts": _now()}


@app.get("/readyz")
async def readyz():
    return {"status": "ready" if await ping() else "degraded"}


@app.post("/retrieve")
async def retrieve(q: QueryIn) -> dict:
    """Return top-k chunks. Tries Moss first, falls back to pgvector."""
    backend = "moss"
    try:
        chunks = await _moss_topk(q)
    except Exception:
        backend = "pgvector"
        chunks = await _pgvector_topk(q)
    return {"backend": backend, "chunks": [c.model_dump() for c in chunks]}


async def _moss_topk(q: QueryIn) -> list[Chunk]:
    """TODO(T1.5): call Moss with settings.moss_endpoint/api_key, resolve moss_ref."""
    if not settings.moss_api_key:
        raise RuntimeError("Moss not configured")  # forces fallback locally
    # ... real Moss call here ...
    return []


async def _pgvector_topk(q: QueryIn) -> list[Chunk]:
    """Demo-safe fallback: cosine search over chunks.embedding.

    NOTE: needs the query embedded to a vector(1024) first (TODO: embed q.text).
    Left as an empty-safe stub so the service boots without an embedding model.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Placeholder query proving DB connectivity; real version embeds q.text
        # and runs:  ORDER BY embedding <=> $1  LIMIT q.top_k
        rows = await conn.fetch(
            "SELECT id, document_id, text FROM chunks LIMIT $1", q.top_k
        )
    return [
        Chunk(
            chunk_id=str(r["id"]),
            document_id=str(r["document_id"]),
            text=r["text"],
            score=0.0,
        )
        for r in rows
    ]
